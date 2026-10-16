/*
 * vsh_main.c - the simulated console around the plugin.
 *
 *  1. builds the VSH export table and the lv2/libpad/libusbd shim, connects to the USB bridge
 *  2. "boots" the plugin exactly like HEN's boot_plugins.txt loader: calls its module_start
 *     on a fresh PPU thread (the plugin spawns its worker and exits that thread)
 *  3. runs a "game" that polls every pad port at 60 Hz the way cellPadGetData() consumers
 *     do, and reports what a game would see as JSON lines
 *  4. reads commands from stdin:  game start [name] | game stop | rumble <port> <large> <small>
 *                                  wait <seconds> | quit
 */
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/select.h>
#include <unistd.h>

#include <cell/pad.h>
#include "shim_internal.h"

#define RUMBLE_IPC_KEY 0x7870616472756d62ULL /* "xpadrumb", see xpad_rumble.h */

static volatile int stop_game_thread;

static void *game_thread(void *unused) {
  CellPadData last[CELL_PAD_MAX_PORT_NUM];
  uint32_t last_seq[CELL_PAD_MAX_PORT_NUM] = {0}, rate_seq[CELL_PAD_MAX_PORT_NUM] = {0};
  int hidden_reported[CELL_PAD_MAX_PORT_NUM] = {0}, seen[CELL_PAD_MAX_PORT_NUM] = {0};
  double rate_t = shim_now();
  (void)unused;
  memset(last, 0, sizeof(last));
  pthread_setname_np(pthread_self(), "game");
  while (!stop_game_thread) {
    usleep(16667);
    for (int port = 0; port < CELL_PAD_MAX_PORT_NUM; port++) {
      CellPadData d;
      int type = -1;
      uint32_t seq = 0;
      int r = shim_pad_game_read(port, &d, &type, &seq);
      if (r == -1 && !hidden_reported[port]) {
        shim_emit("\"ev\":\"pad_hidden\",\"port\":%d,\"type\":%d,\"why\":\"LDD pad not in insert-into-game mode\"", port, type);
        hidden_reported[port] = 1;
      }
      if (r != 1) { if (r == 0) { seen[port] = 0; hidden_reported[port] = 0; } continue; }
      if (seq == last_seq[port]) continue;
      last_seq[port] = seq;
      if (seen[port] && d.len == last[port].len && memcmp(d.button, last[port].button, sizeof(uint16_t) * 24) == 0) continue;
      seen[port] = 1;
      last[port] = d;
      char b[24 * 8] = {0};
      int o = 0;
      for (int i = 0; i < 24; i++) o += snprintf(b + o, sizeof(b) - (size_t)o, "%s%u", i ? "," : "", d.button[i]);
      shim_emit("\"ev\":\"pad\",\"view\":\"game\",\"port\":%d,\"type\":%d,\"len\":%d,\"b\":[%s]", port, type, d.len, b);
    }
    double now = shim_now();
    if (now - rate_t >= 2.0) {
      for (int port = 0; port < CELL_PAD_MAX_PORT_NUM; port++) {
        CellPadData d; int type; uint32_t seq;
        if (shim_pad_game_read(port, &d, &type, &seq) == 1 && seq != rate_seq[port]) {
          shim_emit("\"ev\":\"pad_rate\",\"port\":%d,\"inserts_per_s\":%.1f", port, (seq - rate_seq[port]) / (now - rate_t));
          rate_seq[port] = seq;
        }
      }
      rate_t = now;
    }
  }
  return NULL;
}

static void *prx_start(void *unused) {
  (void)unused;
  shim_emit("\"ev\":\"module_start\",\"module\":\"%s\"", shim_module_name);
  int r = shim_module_start(0); /* normally exits this thread via syscall 41 */
  shim_emit("\"ev\":\"module_start_returned\",\"result\":%d", r);
  return NULL;
}

static void *prx_stop(void *unused) {
  (void)unused;
  int r = shim_module_stop();
  shim_emit("\"ev\":\"module_stop_returned\",\"result\":%d", r);
  return NULL;
}

int main(void) {
  setvbuf(stdout, NULL, _IOLBF, 0);
  shim_init();
  if (shim_setup_vsh_exports() != 0) {
    shim_emit("\"ev\":\"fatal\",\"msg\":\"cannot map the VSH export table at 0x10000\"");
    return 2;
  }
  const char *nat = getenv("PS3SIM_NATIVE_DS3");
  if (nat && atoi(nat)) shim_pad_add_native_ds3(0);
  shim_emit("\"ev\":\"boot\",\"console\":\"ps3sim\",\"arch\":\"ppc32-be\",\"module\":\"%s\"", shim_module_name);
  shim_usb_init(getenv("PS3SIM_BRIDGE"));

  pthread_t gt, lt;
  pthread_create(&gt, NULL, game_thread, NULL);
  pthread_create(&lt, NULL, prx_start, NULL);
  pthread_join(lt, NULL);
  shim_emit("\"ev\":\"module_resident\"");

  double limit = 0;
  const char *secs = getenv("PS3SIM_SECONDS");
  if (secs) limit = atof(secs);
  char line[512];
  int stdin_open = 1;
  for (;;) {
    if (limit > 0 && shim_now() >= limit) break;
    if (!stdin_open) { usleep(100000); continue; }
    fd_set rf;
    struct timeval tv = {0, 100000};
    FD_ZERO(&rf);
    FD_SET(0, &rf);
    if (select(1, &rf, NULL, NULL, &tv) <= 0) continue;
    if (!fgets(line, sizeof(line), stdin)) { stdin_open = 0; if (limit <= 0) break; continue; }
    char cmd[32] = {0}, a[64] = {0};
    int p = 0, l = 0, s = 0;
    sscanf(line, "%31s", cmd);
    if (strcmp(cmd, "quit") == 0) break;
    if (strcmp(cmd, "game") == 0) {
      char name[64] = "";
      sscanf(line, "game %63s %63s", a, name);
      shim_set_game_running(strcmp(a, "start") == 0, name);
      shim_emit("\"ev\":\"game\",\"state\":\"%s\"", a);
    } else if (strcmp(cmd, "rumble") == 0 && sscanf(line, "rumble %d %d %d", &p, &l, &s) == 3) {
      /* what xpad_game.sprx sends after hooking cellPadSetActDirect(): data1=port,
         data2=left/large motor, data3=right/small motor */
      int r = shim_event_send_ipc(RUMBLE_IPC_KEY, (uint64_t)p, (uint64_t)l, (uint64_t)s);
      shim_emit("\"ev\":\"game_rumble\",\"port\":%d,\"large\":%d,\"small\":%d,\"result\":%d", p, l, s, r);
    } else if (strcmp(cmd, "wait") == 0) {
      double w = 0;
      sscanf(line, "wait %lf", &w);
      usleep((useconds_t)(w * 1e6));
    }
  }

  pthread_t st;
  pthread_create(&st, NULL, prx_stop, NULL);
  pthread_join(st, NULL);
  stop_game_thread = 1;
  pthread_join(gt, NULL);
  shim_emit("\"ev\":\"shutdown\"");
  return 0;
}
