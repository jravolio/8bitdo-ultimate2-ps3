/*
 * lv2_shim.c - a small simulated PS3 (lv2 kernel + VSH libraries) that hosts an
 * unmodified VSH plugin compiled for 32-bit big-endian PowerPC and run under qemu-ppc.
 *
 * What is emulated, and how faithfully:
 *   - ppu threads, mutexes, event queues (incl. IPC by key), timers  -> pthreads
 *   - cellFs                     -> files under $PS3SIM_ROOT (/dev_hdd0/... layout)
 *   - lv2 syscalls 41/145/461/573/574 and Cobra syscall 8 (PS3MAPI)
 *   - libpad: 7 ports, LDD pads, insert-into-game mode, port settings, a "game" view
 *   - libusbd: extra-LDD table (with the retail ~22 entry budget), probe/attach/detach
 *     on a single USBD thread, static descriptor scanning, pipes, async transfers whose
 *     completions run on that USBD thread. Real USB I/O is forwarded to the PC-side
 *     bridge (bitdo_ps3/bridge.py) which owns the actual device.
 *   - VSH export table at 0x1008C so the plugin's own getNIDfunc() finds
 *     vshtask_notify / allocator malloc+free exactly as on the console.
 *
 * Everything observable is reported as JSON lines on stdout for the test harness.
 */
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#include <cell/cell_fs.h>
#include <cell/pad.h>
#include <cell/pad/libpad_dbg.h>
#include <cell/sysmodule.h>
#include <cell/usbd.h>
#include <netex/net.h>
#include <sys/event.h>
#include <sys/ppu_thread.h>
#include <sys/process.h>
#include <sys/synchronization.h>
#include <sys/timer.h>

#include "shim_internal.h"

/* ------------------------------------------------------------------------------------- */
/* output                                                                                 */

static pthread_mutex_t out_lock = PTHREAD_MUTEX_INITIALIZER;
static uint64_t t0_ns;

static uint64_t mono_ns(void) {
  struct timespec ts;
  clock_gettime(CLOCK_MONOTONIC, &ts);
  return (uint64_t)ts.tv_sec * 1000000000ULL + (uint64_t)ts.tv_nsec;
}

double shim_now(void) { return (double)(mono_ns() - t0_ns) / 1e9; }

void shim_json_str(char *dst, size_t cap, const char *s) {
  size_t o = 0;
  if (cap < 3) return;
  dst[o++] = '"';
  for (; s && *s && o + 7 < cap; s++) {
    unsigned char c = (unsigned char)*s;
    if (c == '"' || c == '\\') { dst[o++] = '\\'; dst[o++] = (char)c; }
    else if (c < 0x20) o += (size_t)snprintf(dst + o, cap - o, "\\u%04x", c);
    else dst[o++] = (char)c;
  }
  dst[o++] = '"';
  dst[o] = 0;
}

void shim_emit(const char *fmt, ...) {
  char body[3072];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(body, sizeof(body), fmt, ap);
  va_end(ap);
  pthread_mutex_lock(&out_lock);
  fprintf(stdout, "{\"t\":%.4f,%s}\n", shim_now(), body);
  fflush(stdout);
  pthread_mutex_unlock(&out_lock);
}

void shim_hex(char *dst, const uint8_t *src, size_t n) {
  static const char hx[] = "0123456789abcdef";
  for (size_t i = 0; i < n; i++) { dst[2 * i] = hx[src[i] >> 4]; dst[2 * i + 1] = hx[src[i] & 15]; }
  dst[2 * n] = 0;
}

static int unhex(const char *s, uint8_t *dst, size_t cap) {
  size_t n = 0;
  if (!s || strcmp(s, "-") == 0) return 0;
  while (s[0] && s[1] && n < cap) {
    unsigned v;
    if (sscanf(s, "%2x", &v) != 1) break;
    dst[n++] = (uint8_t)v;
    s += 2;
  }
  return (int)n;
}

/* ------------------------------------------------------------------------------------- */
/* threads                                                                                */

typedef struct { void (*entry)(uint64_t); uint64_t arg; char name[32]; } thread_start_t;

static void *thread_tramp(void *p) {
  thread_start_t st = *(thread_start_t *)p;
  free(p);
  pthread_setname_np(pthread_self(), st.name[0] ? st.name : "ppu");
  st.entry(st.arg);
  return NULL;
}

int sys_ppu_thread_create(sys_ppu_thread_t *thread_id, void (*entry)(uint64_t), uint64_t arg, int prio,
                          size_t stacksize, uint64_t flags, const char *threadname) {
  pthread_t th;
  pthread_attr_t at;
  thread_start_t *st = calloc(1, sizeof(*st));
  (void)prio;
  st->entry = entry;
  st->arg = arg;
  snprintf(st->name, sizeof(st->name), "%.15s", threadname ? threadname : "ppu");
  pthread_attr_init(&at);
  /* PS3 VSH plugins run on 8-16 KB stacks; host libc calls inside the shim need more,
     so the simulated stack is never smaller than 256 KB (stack depth is not under test). */
  pthread_attr_setstacksize(&at, stacksize < (256u << 10) ? (256u << 10) : stacksize);
  if (!(flags & SYS_PPU_THREAD_CREATE_JOINABLE)) pthread_attr_setdetachstate(&at, PTHREAD_CREATE_DETACHED);
  if (pthread_create(&th, &at, thread_tramp, st) != 0) { free(st); pthread_attr_destroy(&at); return (int)0x80010004; }
  pthread_attr_destroy(&at);
  if (thread_id) *thread_id = (sys_ppu_thread_t)(uintptr_t)th;
  shim_emit("\"ev\":\"thread_create\",\"name\":\"%.15s\"", threadname ? threadname : "");
  return CELL_OK;
}

void sys_ppu_thread_exit(uint64_t val) { pthread_exit((void *)(uintptr_t)val); }

int sys_ppu_thread_join(sys_ppu_thread_t thread_id, uint64_t *vptr) {
  void *rv = NULL;
  int r = pthread_join((pthread_t)(uintptr_t)thread_id, &rv);
  if (vptr) *vptr = (uint64_t)(uintptr_t)rv;
  return r == 0 ? CELL_OK : (int)0x80010005;
}

int sys_ppu_thread_get_id(sys_ppu_thread_t *thread_id) {
  *thread_id = (sys_ppu_thread_t)(uintptr_t)pthread_self();
  return CELL_OK;
}

sys_pid_t sys_process_getpid(void) { return 0x01000300; /* VSH */ }

/* ------------------------------------------------------------------------------------- */
/* mutexes                                                                                */

#define MAX_MUTEX 256
static pthread_mutex_t mutexes[MAX_MUTEX];
static uint8_t mutex_used[MAX_MUTEX];
static pthread_mutex_t mutex_tab_lock = PTHREAD_MUTEX_INITIALIZER;

int sys_mutex_create(sys_mutex_t *mutex_id, sys_mutex_attribute_t *attr) {
  pthread_mutexattr_t ma;
  pthread_mutex_lock(&mutex_tab_lock);
  for (int i = 0; i < MAX_MUTEX; i++) {
    if (!mutex_used[i]) {
      pthread_mutexattr_init(&ma);
      pthread_mutexattr_settype(&ma, (attr && attr->attr_recursive == SYS_SYNC_RECURSIVE) ? PTHREAD_MUTEX_RECURSIVE
                                                                                          : PTHREAD_MUTEX_ERRORCHECK);
      pthread_mutex_init(&mutexes[i], &ma);
      pthread_mutexattr_destroy(&ma);
      mutex_used[i] = 1;
      *mutex_id = (sys_mutex_t)(i + 0x85000001);
      pthread_mutex_unlock(&mutex_tab_lock);
      return CELL_OK;
    }
  }
  pthread_mutex_unlock(&mutex_tab_lock);
  return (int)0x80010005; /* EAGAIN */
}

static pthread_mutex_t *mutex_get(sys_mutex_t id) {
  uint32_t i = id - 0x85000001u;
  return (i < MAX_MUTEX && mutex_used[i]) ? &mutexes[i] : NULL;
}

int sys_mutex_destroy(sys_mutex_t id) {
  pthread_mutex_t *m = mutex_get(id);
  if (!m) return (int)0x80010005;
  pthread_mutex_destroy(m);
  mutex_used[id - 0x85000001u] = 0;
  return CELL_OK;
}

int sys_mutex_lock(sys_mutex_t id, usecond_t timeout) {
  pthread_mutex_t *m = mutex_get(id);
  if (!m) return (int)0x80010005;
  if (timeout == 0) return pthread_mutex_lock(m) == 0 ? CELL_OK : (int)0x80010009;
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  ts.tv_sec += (time_t)(timeout / 1000000ULL);
  ts.tv_nsec += (long)(timeout % 1000000ULL) * 1000L;
  if (ts.tv_nsec >= 1000000000L) { ts.tv_sec++; ts.tv_nsec -= 1000000000L; }
  int r = pthread_mutex_timedlock(m, &ts);
  return r == 0 ? CELL_OK : (r == ETIMEDOUT ? (int)0x8001000B : (int)0x80010009);
}

int sys_mutex_trylock(sys_mutex_t id) {
  pthread_mutex_t *m = mutex_get(id);
  if (!m) return (int)0x80010005;
  return pthread_mutex_trylock(m) == 0 ? CELL_OK : (int)0x8001000A;
}

int sys_mutex_unlock(sys_mutex_t id) {
  pthread_mutex_t *m = mutex_get(id);
  if (!m) return (int)0x80010005;
  return pthread_mutex_unlock(m) == 0 ? CELL_OK : (int)0x80010009;
}

/* ------------------------------------------------------------------------------------- */
/* event queues / ports                                                                   */

#define MAX_EQ 16
#define MAX_EQ_DEPTH 127
typedef struct {
  int used;
  uint64_t key;
  int size, head, count;
  sys_event_t ev[MAX_EQ_DEPTH];
  pthread_mutex_t lk;
  pthread_cond_t cv;
} equeue_t;
static equeue_t equeues[MAX_EQ];
static pthread_mutex_t eq_tab_lock = PTHREAD_MUTEX_INITIALIZER;

int sys_event_queue_create(sys_event_queue_t *equeue_id, sys_event_queue_attribute_t *attr, uint64_t key, int32_t size) {
  (void)attr;
  if (size <= 0 || size > MAX_EQ_DEPTH) return (int)0x80010002;
  pthread_mutex_lock(&eq_tab_lock);
  for (int i = 0; i < MAX_EQ; i++) {
    if (equeues[i].used && key && equeues[i].key == key) { pthread_mutex_unlock(&eq_tab_lock); return (int)0x80010014; /* EEXIST */ }
  }
  for (int i = 0; i < MAX_EQ; i++) {
    if (!equeues[i].used) {
      memset(&equeues[i], 0, sizeof(equeues[i]));
      equeues[i].used = 1;
      equeues[i].key = key;
      equeues[i].size = size;
      pthread_mutex_init(&equeues[i].lk, NULL);
      pthread_cond_init(&equeues[i].cv, NULL);
      *equeue_id = (sys_event_queue_t)(0x8d000001 + i);
      pthread_mutex_unlock(&eq_tab_lock);
      shim_emit("\"ev\":\"event_queue_create\",\"key\":\"%016llx\",\"size\":%d", (unsigned long long)key, size);
      return CELL_OK;
    }
  }
  pthread_mutex_unlock(&eq_tab_lock);
  return (int)0x80010005;
}

static equeue_t *eq_get(sys_event_queue_t id) {
  uint32_t i = id - 0x8d000001u;
  return (i < MAX_EQ && equeues[i].used) ? &equeues[i] : NULL;
}

int sys_event_queue_destroy(sys_event_queue_t id, int32_t mode) {
  (void)mode;
  equeue_t *q = eq_get(id);
  if (!q) return (int)0x80010005;
  pthread_mutex_lock(&eq_tab_lock);
  q->used = 0;
  pthread_mutex_unlock(&eq_tab_lock);
  return CELL_OK;
}

static int eq_push(equeue_t *q, uint64_t src, uint64_t d1, uint64_t d2, uint64_t d3) {
  pthread_mutex_lock(&q->lk);
  if (q->count >= q->size) { pthread_mutex_unlock(&q->lk); return (int)0x8001000A; /* EBUSY: queue full */ }
  sys_event_t *e = &q->ev[(q->head + q->count) % q->size];
  e->source = src; e->data1 = d1; e->data2 = d2; e->data3 = d3;
  q->count++;
  pthread_cond_signal(&q->cv);
  pthread_mutex_unlock(&q->lk);
  return CELL_OK;
}

int sys_event_queue_tryreceive(sys_event_queue_t id, sys_event_t *arr, int32_t size, int32_t *number) {
  equeue_t *q = eq_get(id);
  int n = 0;
  if (!q) return (int)0x80010005;
  pthread_mutex_lock(&q->lk);
  while (q->count > 0 && n < size) {
    arr[n++] = q->ev[q->head];
    q->head = (q->head + 1) % q->size;
    q->count--;
  }
  pthread_mutex_unlock(&q->lk);
  *number = n;
  return CELL_OK;
}

int sys_event_queue_receive(sys_event_queue_t id, sys_event_t *event, usecond_t timeout) {
  equeue_t *q = eq_get(id);
  if (!q) return (int)0x80010005;
  pthread_mutex_lock(&q->lk);
  while (q->count == 0) {
    if (timeout) {
      struct timespec ts;
      clock_gettime(CLOCK_REALTIME, &ts);
      ts.tv_sec += (time_t)(timeout / 1000000ULL);
      ts.tv_nsec += (long)(timeout % 1000000ULL) * 1000L;
      if (ts.tv_nsec >= 1000000000L) { ts.tv_sec++; ts.tv_nsec -= 1000000000L; }
      if (pthread_cond_timedwait(&q->cv, &q->lk, &ts) == ETIMEDOUT) { pthread_mutex_unlock(&q->lk); return (int)0x8001000B; }
    } else {
      pthread_cond_wait(&q->cv, &q->lk);
    }
  }
  *event = q->ev[q->head];
  q->head = (q->head + 1) % q->size;
  q->count--;
  pthread_mutex_unlock(&q->lk);
  return CELL_OK;
}

/* event ports: id -> connected key */
#define MAX_EPORT 16
static uint64_t eport_key[MAX_EPORT];
static uint8_t eport_used[MAX_EPORT];

int sys_event_port_create(sys_event_port_t *eport_id, int32_t port_type, uint64_t name) {
  (void)port_type; (void)name;
  for (int i = 0; i < MAX_EPORT; i++) {
    if (!eport_used[i]) { eport_used[i] = 1; eport_key[i] = 0; *eport_id = (sys_event_port_t)(0x0e000001 + i); return CELL_OK; }
  }
  return (int)0x80010005;
}

int sys_event_port_connect_ipc(sys_event_port_t eport_id, uint64_t key) {
  uint32_t i = eport_id - 0x0e000001u;
  if (i >= MAX_EPORT || !eport_used[i]) return (int)0x80010005;
  eport_key[i] = key;
  return CELL_OK;
}

int shim_event_send_ipc(uint64_t key, uint64_t d1, uint64_t d2, uint64_t d3) {
  for (int i = 0; i < MAX_EQ; i++) {
    if (equeues[i].used && equeues[i].key == key) return eq_push(&equeues[i], 0x0e000001, d1, d2, d3);
  }
  return (int)0x80010005; /* ESRCH: no queue with that key */
}

int sys_event_port_send(sys_event_port_t eport_id, uint64_t d1, uint64_t d2, uint64_t d3) {
  uint32_t i = eport_id - 0x0e000001u;
  if (i >= MAX_EPORT || !eport_used[i]) return (int)0x80010005;
  return shim_event_send_ipc(eport_key[i], d1, d2, d3);
}

/* ------------------------------------------------------------------------------------- */
/* time                                                                                   */

static double time_scale = 1.0;

int sys_timer_usleep(usecond_t us) {
  struct timespec ts = { (time_t)(us / 1000000ULL), (long)(us % 1000000ULL) * 1000L };
  while (nanosleep(&ts, &ts) != 0 && errno == EINTR) {}
  return CELL_OK;
}

int sys_timer_sleep(second_t s) {
  /* Second-granularity sleeps are only boot settling delays; PS3SIM_TIME_SCALE (<1) lets
     automated tests shorten them without touching the 2 ms input loop. */
  return sys_timer_usleep((usecond_t)((double)s * 1000000.0 * time_scale));
}

int sys_time_get_current_time(sys_time_sec_t *sec, sys_time_nsec_t *nsec) {
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  *sec = ts.tv_sec;
  *nsec = ts.tv_nsec;
  return CELL_OK;
}

/* ------------------------------------------------------------------------------------- */
/* cellFs                                                                                 */

static char fs_root[512] = "ps3root";

static void fs_map(const char *path, char *out, size_t cap) {
  if (path && path[0] == '/') snprintf(out, cap, "%s%s", fs_root, path);
  else snprintf(out, cap, "%s/%s", fs_root, path ? path : "");
}

CellFsErrno cellFsOpen(const char *path, int flags, int *fd, const void *arg, uint64_t size) {
  char p[1024];
  int of = 0;
  (void)arg; (void)size;
  fs_map(path, p, sizeof(p));
  switch (flags & CELL_FS_O_ACCMODE) {
    case CELL_FS_O_WRONLY: of = O_WRONLY; break;
    case CELL_FS_O_RDWR: of = O_RDWR; break;
    default: of = O_RDONLY;
  }
  if (flags & CELL_FS_O_CREAT) of |= O_CREAT;
  if (flags & CELL_FS_O_TRUNC) of |= O_TRUNC;
  if (flags & CELL_FS_O_APPEND) of |= O_APPEND;
  if (flags & CELL_FS_O_EXCL) of |= O_EXCL;
  int f = open(p, of, 0666);
  if (f < 0) return errno == ENOENT ? CELL_FS_ENOENT : CELL_FS_EIO;
  *fd = f;
  return CELL_FS_SUCCEEDED;
}

CellFsErrno cellFsRead(int fd, void *buf, uint64_t nbytes, uint64_t *nread) {
  ssize_t r = read(fd, buf, (size_t)nbytes);
  if (r < 0) return CELL_FS_EIO;
  if (nread) *nread = (uint64_t)r;
  return CELL_FS_SUCCEEDED;
}

CellFsErrno cellFsWrite(int fd, const void *buf, uint64_t nbytes, uint64_t *nwrite) {
  ssize_t r = write(fd, buf, (size_t)nbytes);
  if (r < 0) return CELL_FS_EIO;
  if (nwrite) *nwrite = (uint64_t)r;
  return CELL_FS_SUCCEEDED;
}

CellFsErrno cellFsClose(int fd) { return close(fd) == 0 ? CELL_FS_SUCCEEDED : CELL_FS_EIO; }

CellFsErrno cellFsUnlink(const char *path) {
  char p[1024];
  fs_map(path, p, sizeof(p));
  return unlink(p) == 0 ? CELL_FS_SUCCEEDED : CELL_FS_ENOENT;
}

CellFsErrno cellFsRename(const char *from, const char *to) {
  char a[1024], b[1024];
  fs_map(from, a, sizeof(a));
  fs_map(to, b, sizeof(b));
  return rename(a, b) == 0 ? CELL_FS_SUCCEEDED : CELL_FS_ENOENT;
}

/* ------------------------------------------------------------------------------------- */
/* misc system libraries                                                                  */

int socketclose(int s) { return close(s); }
int sys_net_initialize_network(void) { return 0; }
int cellSysmoduleLoadModule(uint16_t id) { (void)id; return CELL_OK; }
int cellSysmoduleUnloadModule(uint16_t id) { (void)id; return CELL_OK; }
int cellSysmoduleIsLoaded(uint16_t id) { (void)id; return CELL_OK; }

/* VSH exports reached through the plugin's getNIDfunc() */
static int vsh_notify(int unk, const char *msg) {
  char j[512];
  (void)unk;
  shim_json_str(j, sizeof(j), msg);
  shim_emit("\"ev\":\"notify\",\"msg\":%s", j);
  return 0;
}
static void *vsh_alloc(unsigned int size) { return calloc(1, size); }
static void vsh_free(void *p) { free(p); }

/* Build the VSH export table the plugin walks: *(u32*)0x1008C + 0x984 -> stub pointer list;
   stub+6 = u16 export count, +0x10 = library name, +0x14 = FNID table, +0x18 = function table. */
int shim_setup_vsh_exports(void) {
  void *m = mmap((void *)0x10000, 0x10000, PROT_READ | PROT_WRITE, MAP_FIXED | MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
  if (m == MAP_FAILED) return -1;
  uint8_t *b = (uint8_t *)0x10000;
  const uint32_t table = 0x14000, stubs = 0x15000, names = 0x16000, fnids = 0x17000, funcs = 0x18000;
  *(uint32_t *)(b + 0x8C) = table - 0x984;
  strcpy((char *)(uintptr_t)names, "vshtask");
  strcpy((char *)(uintptr_t)(names + 0x10), "allocator");
  /* vshtask: vshtask_notify */
  ((uint32_t *)(uintptr_t)fnids)[0] = 0xA02D46E7;
  ((uint32_t *)(uintptr_t)funcs)[0] = (uint32_t)(uintptr_t)vsh_notify;
  /* allocator: malloc, free */
  ((uint32_t *)(uintptr_t)(fnids + 0x10))[0] = 0x759E0635;
  ((uint32_t *)(uintptr_t)(fnids + 0x10))[1] = 0x77A602DD;
  ((uint32_t *)(uintptr_t)(funcs + 0x10))[0] = (uint32_t)(uintptr_t)vsh_alloc;
  ((uint32_t *)(uintptr_t)(funcs + 0x10))[1] = (uint32_t)(uintptr_t)vsh_free;
  for (int i = 0; i < 2; i++) {
    uint8_t *s = (uint8_t *)(uintptr_t)(stubs + 0x40 * i);
    *(uint16_t *)(s + 6) = (uint16_t)(i == 0 ? 1 : 2);
    *(uint32_t *)(s + 0x10) = names + 0x10 * i;
    *(uint32_t *)(s + 0x14) = fnids + 0x10 * i;
    *(uint32_t *)(s + 0x18) = funcs + 0x10 * i;
    ((uint32_t *)(uintptr_t)table)[i] = stubs + 0x40 * i;
  }
  ((uint32_t *)(uintptr_t)table)[2] = 0;
  return 0;
}

/* ------------------------------------------------------------------------------------- */
/* libpad                                                                                 */

typedef struct {
  int connected;
  int type;               /* CELL_PAD_DEV_TYPE_* */
  int ldd;
  int32_t handle;
  uint32_t capability;
  uint32_t setting;
  int insert_into_game;
  uint32_t status_flags;  /* pending ASSIGN_CHANGES */
  CellPadData data;
  uint32_t seq;
  uint32_t vsh_seen;
} pad_port_t;

static pad_port_t ports[CELL_PAD_MAX_PORT_NUM];
static pthread_mutex_t pad_lock = PTHREAD_MUTEX_INITIALIZER;
static int pad_initialized = 1; /* inside VSH the library is already initialized */
static int32_t next_ldd_handle = 0;

static void port_mark_assign_changes(void) {
  for (int i = 0; i < CELL_PAD_MAX_PORT_NUM; i++) ports[i].status_flags |= CELL_PAD_STATUS_ASSIGN_CHANGES;
}

void shim_pad_add_native_ds3(int port) {
  pthread_mutex_lock(&pad_lock);
  pad_port_t *p = &ports[port];
  memset(p, 0, sizeof(*p));
  p->connected = 1;
  p->type = CELL_PAD_DEV_TYPE_STANDARD;
  p->capability = 0x1F;
  p->insert_into_game = 1;
  p->handle = -1;
  p->data.len = 24;
  for (int i = CELL_PAD_BTN_OFFSET_ANALOG_RIGHT_X; i <= CELL_PAD_BTN_OFFSET_ANALOG_LEFT_Y; i++) p->data.button[i] = 0x80;
  for (int i = CELL_PAD_BTN_OFFSET_SENSOR_X; i <= CELL_PAD_BTN_OFFSET_SENSOR_G; i++) p->data.button[i] = 0x200;
  p->seq = 1;
  port_mark_assign_changes();
  pthread_mutex_unlock(&pad_lock);
  shim_emit("\"ev\":\"native_pad\",\"port\":%d,\"type\":\"DS3\"", port);
}

static int ldd_register(uint32_t capability) {
  pthread_mutex_lock(&pad_lock);
  for (int i = 0; i < CELL_PAD_MAX_PORT_NUM; i++) {
    if (!ports[i].connected) {
      pad_port_t *p = &ports[i];
      memset(p, 0, sizeof(*p));
      p->connected = 1;
      p->ldd = 1;
      p->type = CELL_PAD_DEV_TYPE_LDD;
      p->capability = capability;
      p->handle = next_ldd_handle++;
      port_mark_assign_changes();
      int h = p->handle;
      pthread_mutex_unlock(&pad_lock);
      shim_emit("\"ev\":\"ldd_register\",\"handle\":%d,\"port\":%d,\"capability\":\"0x%x\"", h, i, capability);
      return h;
    }
  }
  pthread_mutex_unlock(&pad_lock);
  return CELL_PAD_ERROR_TOO_MANY_DEVICES;
}

static pad_port_t *port_by_handle(int32_t h) {
  for (int i = 0; i < CELL_PAD_MAX_PORT_NUM; i++)
    if (ports[i].connected && ports[i].ldd && ports[i].handle == h) return &ports[i];
  return NULL;
}

int32_t cellPadInit(uint32_t max_connect) {
  (void)max_connect;
  if (pad_initialized) return CELL_PAD_ERROR_ALREADY_INITIALIZED;
  pad_initialized = 1;
  return CELL_PAD_OK;
}
int32_t cellPadEnd(void) { return CELL_PAD_OK; }
int32_t cellPadClearBuf(uint32_t port_no) { (void)port_no; return CELL_PAD_OK; }

int32_t cellPadGetInfo2(CellPadInfo2 *info) {
  memset(info, 0, sizeof(*info));
  pthread_mutex_lock(&pad_lock);
  info->max_connect = CELL_PAD_MAX_PORT_NUM;
  for (int i = 0; i < CELL_PAD_MAX_PORT_NUM; i++) {
    if (ports[i].connected) {
      info->now_connect++;
      info->port_status[i] = CELL_PAD_STATUS_CONNECTED;
      info->port_setting[i] = ports[i].setting;
      info->device_capability[i] = ports[i].capability;
      info->device_type[i] = (uint32_t)ports[i].type;
    }
    info->port_status[i] |= ports[i].status_flags;
    ports[i].status_flags = 0;
  }
  pthread_mutex_unlock(&pad_lock);
  return CELL_PAD_OK;
}

int32_t cellPadGetData(uint32_t port_no, CellPadData *data) {
  if (port_no >= CELL_PAD_MAX_PORT_NUM) return CELL_PAD_ERROR_INVALID_PARAMETER;
  pthread_mutex_lock(&pad_lock);
  pad_port_t *p = &ports[port_no];
  if (!p->connected) { pthread_mutex_unlock(&pad_lock); return CELL_PAD_ERROR_NO_DEVICE; }
  *data = p->data;
  if (p->vsh_seen == p->seq) data->len = 0; /* no new data since the last read */
  p->vsh_seen = p->seq;
  pthread_mutex_unlock(&pad_lock);
  return CELL_PAD_OK;
}

int32_t cellPadSetPortSetting(uint32_t port_no, uint32_t setting) {
  if (port_no >= CELL_PAD_MAX_PORT_NUM) return CELL_PAD_ERROR_INVALID_PARAMETER;
  pthread_mutex_lock(&pad_lock);
  ports[port_no].setting = setting;
  pthread_mutex_unlock(&pad_lock);
  shim_emit("\"ev\":\"port_setting\",\"port\":%u,\"setting\":\"0x%x\"", port_no, setting);
  return CELL_PAD_OK;
}

int32_t cellPadSetActDirect(uint32_t port_no, CellPadActParam *param) {
  shim_emit("\"ev\":\"act_direct\",\"port\":%u,\"small\":%u,\"large\":%u", port_no, param->motor[0], param->motor[1]);
  return CELL_PAD_OK;
}

int32_t cellPadLddRegisterController(void) { return ldd_register(0x1F); }

int32_t cellPadLddDataInsert(int32_t handle, CellPadData *data) {
  pthread_mutex_lock(&pad_lock);
  pad_port_t *p = port_by_handle(handle);
  if (!p) { pthread_mutex_unlock(&pad_lock); return CELL_PAD_ERROR_INVALID_PARAMETER; }
  p->data = *data;
  p->seq++;
  pthread_mutex_unlock(&pad_lock);
  return CELL_PAD_OK;
}

int32_t cellPadLddGetPortNo(int32_t handle) {
  pthread_mutex_lock(&pad_lock);
  for (int i = 0; i < CELL_PAD_MAX_PORT_NUM; i++) {
    if (ports[i].connected && ports[i].ldd && ports[i].handle == handle) { pthread_mutex_unlock(&pad_lock); return i; }
  }
  pthread_mutex_unlock(&pad_lock);
  return CELL_PAD_ERROR_INVALID_PARAMETER;
}

int32_t cellPadLddUnregisterController(int32_t handle) {
  pthread_mutex_lock(&pad_lock);
  pad_port_t *p = port_by_handle(handle);
  if (!p) { pthread_mutex_unlock(&pad_lock); return CELL_PAD_ERROR_INVALID_PARAMETER; }
  int port = (int)(p - ports);
  memset(p, 0, sizeof(*p));
  port_mark_assign_changes();
  pthread_mutex_unlock(&pad_lock);
  shim_emit("\"ev\":\"ldd_unregister\",\"handle\":%d,\"port\":%d", handle, port);
  return CELL_PAD_OK;
}

/* What a game sees: every port whose data reaches the game process. LDD pads only do so
   once syscall 573 switched them to insert-into-game mode. Called by the game thread. */
int shim_pad_game_read(int port, CellPadData *out, int *type, uint32_t *seq) {
  int ok = 0;
  pthread_mutex_lock(&pad_lock);
  pad_port_t *p = &ports[port];
  if (p->connected && (!p->ldd || p->insert_into_game)) {
    *out = p->data;
    *type = p->type;
    *seq = p->seq;
    ok = 1;
  } else if (p->connected) {
    *type = p->type;
    ok = -1; /* connected but hidden from games */
  }
  pthread_mutex_unlock(&pad_lock);
  return ok;
}

/* ------------------------------------------------------------------------------------- */
/* syscalls                                                                               */

static int sim_game_running;
static char sim_game_name[64] = "BLUS30443_main_EBOOT.BIN";

void shim_set_game_running(int on, const char *name) {
  sim_game_running = on;
  if (name && *name) snprintf(sim_game_name, sizeof(sim_game_name), "%s", name);
}

uint64_t shim_syscall(uint64_t num, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4, uint64_t a5, uint64_t a6,
                      uint64_t a7, uint64_t a8) {
  (void)a5; (void)a6; (void)a7; (void)a8;
  switch (num) {
    case 41: /* sys_ppu_thread_exit */
      sys_ppu_thread_exit(a1);
      return 0;
    case 145: { /* sys_time_get_current_time */
      sys_time_get_current_time((sys_time_sec_t *)(uintptr_t)(uint32_t)a1, (sys_time_nsec_t *)(uintptr_t)(uint32_t)a2);
      return 0;
    }
    case 461: /* sys_prx_get_module_id_by_address */
      return 0x23000001;
    case 574: { /* sys_pad_dbg_ldd_register_controller(data, &handle, addr, capability<<1) */
      int32_t *hp = (int32_t *)(uintptr_t)(uint32_t)a2;
      uint32_t cap = (uint32_t)a4 >> 1;
      shim_emit("\"ev\":\"syscall\",\"num\":574,\"name\":\"sys_pad_dbg_ldd_register_controller\",\"addr\":%u,\"capability\":\"0x%x\"",
                (unsigned)a3, cap);
      *hp = ldd_register(cap);
      return 0;
    }
    case 573: { /* sys_pad_dbg_ldd_set_data_insert_mode(handle, 0x100, &mode, 4) */
      uint32_t mode = *(uint32_t *)(uintptr_t)(uint32_t)a3;
      pthread_mutex_lock(&pad_lock);
      pad_port_t *p = port_by_handle((int32_t)a1);
      if (p) p->insert_into_game = (mode == CELL_PAD_LDD_INSERT_DATA_INTO_GAME_MODE_ON);
      pthread_mutex_unlock(&pad_lock);
      shim_emit("\"ev\":\"syscall\",\"num\":573,\"name\":\"sys_pad_dbg_ldd_set_data_insert_mode\",\"handle\":%d,\"mode\":%u,\"ok\":%s",
                (int)a1, mode, p ? "true" : "false");
      return p ? 0 : (uint64_t)(int64_t)CELL_PAD_ERROR_INVALID_PARAMETER;
    }
    case 8: /* Cobra/Mamba syscall 8 */
      if (a1 == 0x7777ULL) {
        if (a2 == 0x0021ULL) { /* PS3MAPI_OPCODE_GET_ALL_PROC_PID */
          uint32_t *pids = (uint32_t *)(uintptr_t)(uint32_t)a3;
          pids[0] = 0x01000300; /* vsh.self */
          if (sim_game_running) pids[1] = 0x01010200;
          return 0;
        }
        if (a2 == 0x0022ULL) { /* PS3MAPI_OPCODE_GET_PROC_NAME_BY_PID */
          char *name = (char *)(uintptr_t)(uint32_t)a4;
          strcpy(name, a3 == 0x01000300 ? "01000300_main_vsh.self" : sim_game_name);
          return 0;
        }
        if (a2 == 0x0044ULL) { /* PS3MAPI_OPCODE_LOAD_PROC_MODULE */
          char j[300];
          shim_json_str(j, sizeof(j), (const char *)(uintptr_t)(uint32_t)a4);
          shim_emit("\"ev\":\"ps3mapi_load_module\",\"pid\":\"0x%08x\",\"path\":%s", (unsigned)a3, j);
          return 0;
        }
      }
      shim_emit("\"ev\":\"syscall\",\"num\":8,\"unhandled\":\"0x%llx/0x%llx\"", (unsigned long long)a1, (unsigned long long)a2);
      return (uint64_t)(int64_t)(int32_t)0x80010003;
    default:
      shim_emit("\"ev\":\"syscall\",\"num\":%llu,\"unhandled\":true", (unsigned long long)num);
      return (uint64_t)(int64_t)(int32_t)0x80010003; /* ENOSYS */
  }
}

/* ------------------------------------------------------------------------------------- */
/* libusbd                                                                                */

#define MAX_LDD 64
#define MAX_DEV 16
#define MAX_PIPE 64
#define MAX_REQ 256

typedef struct { int used; CellUsbdLddOps *ops; char name[64]; uint16_t vid, pid_min, pid_max; } ldd_t;
typedef struct {
  int used, attached;
  int ldd;            /* index of the LDD that owns it, -1 none */
  uint16_t vid, pid;
  uint8_t desc[1024];
  int desc_len;
  void *priv;
} usbdev_t;
typedef struct { int used; int dev; uint8_t ep; } pipe_t;
typedef struct {
  int used;
  int pipe;
  CellUsbdDoneCallback cb;
  void *arg;
  void *buf;
  int32_t len;
  int in;
} req_t;

static ldd_t ldds[MAX_LDD];
static usbdev_t devs[MAX_DEV];
static pipe_t pipes[MAX_PIPE];
static req_t reqs[MAX_REQ];
static int ldd_budget = 22;
static uint32_t req_seq = 1;
static pthread_mutex_t usb_lock = PTHREAD_MUTEX_INITIALIZER;

static int bridge_fd = -1;
static pthread_mutex_t bridge_wlock = PTHREAD_MUTEX_INITIALIZER;

static void bridge_send(const char *fmt, ...) {
  char line[4096];
  va_list ap;
  va_start(ap, fmt);
  int n = vsnprintf(line, sizeof(line) - 2, fmt, ap);
  va_end(ap);
  if (n < 0) return;
  if (n > (int)sizeof(line) - 2) n = (int)sizeof(line) - 2;
  line[n++] = '\n';
  pthread_mutex_lock(&bridge_wlock);
  if (bridge_fd >= 0) {
    ssize_t off = 0;
    while (off < n) {
      ssize_t w = send(bridge_fd, line + off, (size_t)(n - off), MSG_NOSIGNAL);
      if (w <= 0) break;
      off += w;
    }
  }
  pthread_mutex_unlock(&bridge_wlock);
}

/* USBD thread work queue: probe/attach/detach and transfer completions all run here,
   one at a time, like the console's USBD callback context. */
typedef struct task { int kind; int dev; uint32_t req; int32_t result; int32_t count; uint8_t *data; struct task *next; } task_t;
enum { T_SCAN = 1, T_DETACH = 2, T_DONE = 3, T_ATTACH = 4 };
static task_t *tq_head, *tq_tail;
static pthread_mutex_t tq_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_cond_t tq_cv = PTHREAD_COND_INITIALIZER;

static void tq_push(task_t *t) {
  t->next = NULL;
  pthread_mutex_lock(&tq_lock);
  if (tq_tail) tq_tail->next = t; else tq_head = t;
  tq_tail = t;
  pthread_cond_signal(&tq_cv);
  pthread_mutex_unlock(&tq_lock);
}

static void tq_post(int kind, int dev, uint32_t req, int32_t result, int32_t count, const uint8_t *data) {
  task_t *t = calloc(1, sizeof(*t));
  t->kind = kind; t->dev = dev; t->req = req; t->result = result; t->count = count;
  if (data && count > 0) { t->data = malloc((size_t)count); memcpy(t->data, data, (size_t)count); }
  tq_push(t);
}

static int ldd_matches(const ldd_t *l, const usbdev_t *d) {
  return l->used && l->vid == d->vid && d->pid >= l->pid_min && d->pid <= l->pid_max;
}

static void try_attach(int dev_id) {
  usbdev_t *d = &devs[dev_id];
  if (!d->used || d->ldd >= 0) return;
  for (int i = 0; i < MAX_LDD; i++) {
    if (!ldd_matches(&ldds[i], d)) continue;
    CellUsbdLddOps *ops = ldds[i].ops;
    int32_t pr = ops->probe ? ops->probe(dev_id) : CELL_USBD_PROBE_FAILED;
    shim_emit("\"ev\":\"usbd_probe\",\"dev\":%d,\"ldd\":\"%s\",\"result\":%d", dev_id, ldds[i].name, pr);
    if (pr != CELL_USBD_PROBE_SUCCEEDED) continue;
    d->ldd = i;
    int32_t ar = ops->attach ? ops->attach(dev_id) : CELL_USBD_ATTACH_FAILED;
    shim_emit("\"ev\":\"usbd_attach\",\"dev\":%d,\"ldd\":\"%s\",\"result\":%d", dev_id, ldds[i].name, ar);
    if (ar != CELL_USBD_ATTACH_SUCCEEDED) d->ldd = -1;
    return;
  }
}

static void *usbd_thread(void *unused) {
  (void)unused;
  pthread_setname_np(pthread_self(), "usbd");
  for (;;) {
    pthread_mutex_lock(&tq_lock);
    while (!tq_head) pthread_cond_wait(&tq_cv, &tq_lock);
    task_t *t = tq_head;
    tq_head = t->next;
    if (!tq_head) tq_tail = NULL;
    pthread_mutex_unlock(&tq_lock);

    if (t->kind == T_ATTACH) {
      /* device arrival is serialized with detach/transfer completions, as on the console */
      usbdev_t *d = &devs[t->dev];
      pthread_mutex_lock(&usb_lock);
      memset(d, 0, sizeof(*d));
      d->used = 1; d->ldd = -1; d->vid = (uint16_t)(t->req >> 16); d->pid = (uint16_t)(t->req & 0xFFFF);
      d->desc_len = t->count < (int32_t)sizeof(d->desc) ? t->count : (int32_t)sizeof(d->desc);
      memcpy(d->desc, t->data, (size_t)d->desc_len);
      pthread_mutex_unlock(&usb_lock);
      shim_emit("\"ev\":\"usb_connect\",\"dev\":%d,\"vid\":\"%04x\",\"pid\":\"%04x\",\"desc_len\":%d", t->dev, d->vid, d->pid, d->desc_len);
      try_attach(t->dev);
    } else if (t->kind == T_SCAN) {
      for (int i = 0; i < MAX_DEV; i++) try_attach(i);
    } else if (t->kind == T_DETACH) {
      usbdev_t *d = &devs[t->dev];
      if (d->used) {
        int l = d->ldd;
        if (l >= 0 && ldds[l].ops && ldds[l].ops->detach) {
          int32_t r = ldds[l].ops->detach(t->dev);
          shim_emit("\"ev\":\"usbd_detach\",\"dev\":%d,\"result\":%d", t->dev, r);
        }
        pthread_mutex_lock(&usb_lock);
        d->used = 0;
        pthread_mutex_unlock(&usb_lock);
      }
    } else if (t->kind == T_DONE) {
      req_t r;
      int ok = 0;
      pthread_mutex_lock(&usb_lock);
      if (t->req < MAX_REQ && reqs[t->req].used) { r = reqs[t->req]; reqs[t->req].used = 0; ok = 1; }
      pthread_mutex_unlock(&usb_lock);
      if (ok) {
        int32_t cnt = t->count;
        if (r.in && r.buf && t->data && cnt > 0) {
          if (cnt > r.len) cnt = r.len;
          memcpy(r.buf, t->data, (size_t)cnt);
        }
        if (r.cb) r.cb(t->result, cnt, r.arg);
      }
    }
    free(t->data);
    free(t);
  }
  return NULL;
}

static void *bridge_reader(void *unused) {
  (void)unused;
  static char buf[1 << 16];
  size_t have = 0;
  pthread_setname_np(pthread_self(), "bridge");
  for (;;) {
    ssize_t n = recv(bridge_fd, buf + have, sizeof(buf) - 1 - have, 0);
    if (n <= 0) { shim_emit("\"ev\":\"bridge_closed\""); return NULL; }
    have += (size_t)n;
    buf[have] = 0;
    char *line = buf, *nl;
    while ((nl = memchr(line, '\n', have - (size_t)(line - buf))) != NULL) {
      *nl = 0;
      char cmd[16] = {0};
      sscanf(line, "%15s", cmd);
      if (strcmp(cmd, "ATTACH") == 0) {
        int dev; unsigned vid, pid; char *hex = malloc(4096);
        if (sscanf(line, "ATTACH %d %x %x %4095s", &dev, &vid, &pid, hex) == 4 && dev >= 0 && dev < MAX_DEV) {
          uint8_t desc[1024];
          int n = unhex(hex, desc, sizeof(desc));
          tq_post(T_ATTACH, dev, (uint32_t)(vid << 16 | pid), 0, n, desc);
        }
        free(hex);
      } else if (strcmp(cmd, "DETACH") == 0) {
        int dev;
        if (sscanf(line, "DETACH %d", &dev) == 1 && dev >= 0 && dev < MAX_DEV) {
          shim_emit("\"ev\":\"usb_disconnect\",\"dev\":%d", dev);
          tq_post(T_DETACH, dev, 0, 0, 0, NULL);
        }
      } else if (strcmp(cmd, "DONE") == 0) {
        unsigned req; int result, count; char hex[300] = "-";
        if (sscanf(line, "DONE %u %d %d %299s", &req, &result, &count, hex) >= 3) {
          uint8_t data[150];
          int n2 = unhex(hex, data, sizeof(data));
          tq_post(T_DONE, 0, req, result, count < n2 ? count : (n2 ? n2 : count), n2 ? data : NULL);
        }
      }
      line = nl + 1;
    }
    have -= (size_t)(line - buf);
    memmove(buf, line, have);
  }
}

int shim_usb_init(const char *hostport) {
  char host[128] = "127.0.0.1";
  int port = 39200;
  if (hostport && *hostport) {
    const char *c = strrchr(hostport, ':');
    if (c) { snprintf(host, sizeof(host), "%.*s", (int)(c - hostport), hostport); port = atoi(c + 1); }
  }
  const char *b = getenv("PS3SIM_LDD_BUDGET");
  if (b) ldd_budget = atoi(b);
  for (int tries = 0; tries < 50; tries++) {
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    struct sockaddr_in sa = {0};
    sa.sin_family = AF_INET;
    sa.sin_port = htons((uint16_t)port);
    inet_pton(AF_INET, host, &sa.sin_addr);
    if (connect(fd, (struct sockaddr *)&sa, sizeof(sa)) == 0) {
      int one = 1;
      setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
      bridge_fd = fd;
      break;
    }
    close(fd);
    usleep(200000);
  }
  pthread_t th;
  pthread_create(&th, NULL, usbd_thread, NULL);
  pthread_detach(th);
  if (bridge_fd < 0) { shim_emit("\"ev\":\"bridge_unavailable\",\"addr\":\"%s:%d\"", host, port); return -1; }
  pthread_create(&th, NULL, bridge_reader, NULL);
  pthread_detach(th);
  bridge_send("HELLO ps3sim 1");
  shim_emit("\"ev\":\"bridge_connected\",\"addr\":\"%s:%d\"", host, port);
  return 0;
}

int32_t cellUsbdInit(void) { return CELL_OK; }
int32_t cellUsbdEnd(void) { return CELL_OK; }
int32_t cellUsbdRegisterLdd(CellUsbdLddOps *ops) { (void)ops; return CELL_USBD_ERROR_LDD_NOT_ALLOCATED; }
int32_t cellUsbdUnregisterLdd(CellUsbdLddOps *ops) { (void)ops; return CELL_OK; }

int32_t cellUsbdRegisterExtraLdd2(CellUsbdLddOps *ops, uint16_t vid, uint16_t pmin, uint16_t pmax) {
  int used = 0, slot = -1;
  const char *name = ops && ops->name ? ops->name : "";
  pthread_mutex_lock(&usb_lock);
  for (int i = 0; i < MAX_LDD; i++) {
    if (!ldds[i].used) { if (slot < 0) slot = i; continue; }
    used++;
    if (strcmp(ldds[i].name, name) == 0 || (ldds[i].vid == vid && ldds[i].pid_min == pmin && ldds[i].pid_max == pmax)) {
      pthread_mutex_unlock(&usb_lock);
      shim_emit("\"ev\":\"usbd_register\",\"vid\":\"%04x\",\"pid\":\"%04x\",\"result\":\"0x80110006\"", vid, pmin);
      return CELL_USBD_ERROR_LDD_ALREADY_REGISTERED;
    }
  }
  if (used >= ldd_budget || slot < 0) {
    pthread_mutex_unlock(&usb_lock);
    shim_emit("\"ev\":\"usbd_register\",\"vid\":\"%04x\",\"pid\":\"%04x\",\"result\":\"0x801100ff\"", vid, pmin);
    return CELL_USBD_ERROR_FATAL; /* table full on retail */
  }
  ldds[slot].used = 1;
  ldds[slot].ops = ops;
  snprintf(ldds[slot].name, sizeof(ldds[slot].name), "%s", name);
  ldds[slot].vid = vid; ldds[slot].pid_min = pmin; ldds[slot].pid_max = pmax;
  pthread_mutex_unlock(&usb_lock);
  char j[160];
  shim_json_str(j, sizeof(j), name);
  shim_emit("\"ev\":\"usbd_register\",\"vid\":\"%04x\",\"pid\":\"%04x\",\"name\":%s,\"slot\":%d,\"result\":\"0x0\"", vid, pmin, j, used);
  for (unsigned p = pmin; p <= pmax; p++) bridge_send("WATCH %04x %04x", vid, p);
  tq_post(T_SCAN, 0, 0, 0, 0, NULL);
  return CELL_OK;
}

int32_t cellUsbdRegisterExtraLdd(CellUsbdLddOps *ops, uint16_t vid, uint16_t pid) {
  return cellUsbdRegisterExtraLdd2(ops, vid, pid, pid);
}

int32_t cellUsbdUnregisterExtraLdd(CellUsbdLddOps *ops) {
  int hit = -1;
  pthread_mutex_lock(&usb_lock);
  for (int i = MAX_LDD - 1; i >= 0; i--) {
    if (ldds[i].used && ldds[i].ops == ops) {
      if (hit < 0) hit = i;
      if (ops->name && strcmp(ldds[i].name, ops->name) == 0) { hit = i; break; }
    }
  }
  if (hit >= 0) ldds[hit].used = 0;
  pthread_mutex_unlock(&usb_lock);
  return hit >= 0 ? CELL_OK : CELL_USBD_ERROR_LDD_NOT_FOUND;
}

void *cellUsbdScanStaticDescriptor(int32_t dev_id, void *ptr, unsigned char type) {
  if (dev_id < 0 || dev_id >= MAX_DEV || !devs[dev_id].used) return NULL;
  usbdev_t *d = &devs[dev_id];
  uint8_t *base = d->desc, *end = d->desc + d->desc_len, *p;
  p = ptr ? (uint8_t *)ptr + ((uint8_t *)ptr)[0] : base;
  while (p >= base && p + 2 <= end && p[0] >= 2) {
    if (type == 0 || p[1] == type) return p;
    p += p[0];
  }
  return NULL;
}

int32_t cellUsbdOpenPipe(int32_t dev_id, UsbEndpointDescriptor *ep) {
  if (dev_id < 0 || dev_id >= MAX_DEV || !devs[dev_id].used) return CELL_USBD_ERROR_DEVICE_NOT_FOUND;
  pthread_mutex_lock(&usb_lock);
  for (int i = 0; i < MAX_PIPE; i++) {
    if (!pipes[i].used) {
      pipes[i].used = 1;
      pipes[i].dev = dev_id;
      pipes[i].ep = ep ? ep->bEndpointAddress : 0;
      pthread_mutex_unlock(&usb_lock);
      shim_emit("\"ev\":\"usbd_open_pipe\",\"dev\":%d,\"pipe\":%d,\"ep\":\"0x%02x\"", dev_id, i, pipes[i].ep);
      return i;
    }
  }
  pthread_mutex_unlock(&usb_lock);
  return CELL_USBD_ERROR_PIPE_NOT_ALLOCATED;
}

int32_t cellUsbdClosePipe(int32_t pipe_id) {
  int dev; uint8_t ep;
  int cancelled[MAX_REQ], nc = 0;
  pthread_mutex_lock(&usb_lock);
  if (pipe_id < 0 || pipe_id >= MAX_PIPE || !pipes[pipe_id].used) { pthread_mutex_unlock(&usb_lock); return CELL_USBD_ERROR_PIPE_NOT_FOUND; }
  dev = pipes[pipe_id].dev; ep = pipes[pipe_id].ep;
  pipes[pipe_id].used = 0;
  for (int i = 0; i < MAX_REQ; i++) if (reqs[i].used && reqs[i].pipe == pipe_id) cancelled[nc++] = i;
  pthread_mutex_unlock(&usb_lock);
  if (ep) bridge_send("CANCEL %d %02x", dev, ep);
  for (int i = 0; i < nc; i++) tq_post(T_DONE, dev, (uint32_t)cancelled[i], USBD_HC_CC_XACT, 0, NULL);
  return CELL_OK;
}

static int req_alloc(int pipe, CellUsbdDoneCallback cb, void *arg, void *buf, int32_t len, int in) {
  pthread_mutex_lock(&usb_lock);
  for (int n = 0; n < MAX_REQ; n++) {
    uint32_t i = (req_seq++) % MAX_REQ;
    if (!reqs[i].used) {
      reqs[i] = (req_t){1, pipe, cb, arg, buf, len, in};
      pthread_mutex_unlock(&usb_lock);
      return (int)i;
    }
  }
  pthread_mutex_unlock(&usb_lock);
  return -1;
}

static int pipe_ok(int32_t pipe_id) { return pipe_id >= 0 && pipe_id < MAX_PIPE && pipes[pipe_id].used; }

int32_t cellUsbdInterruptTransfer(int32_t pipe_id, void *buf, int32_t len, CellUsbdDoneCallback cb, void *arg) {
  if (!pipe_ok(pipe_id)) return CELL_USBD_ERROR_PIPE_NOT_FOUND;
  int in = (pipes[pipe_id].ep & 0x80) != 0;
  int r = req_alloc(pipe_id, cb, arg, buf, len, in);
  if (r < 0) return CELL_USBD_ERROR_IOREQ_NOT_ALLOCATED;
  if (in) {
    bridge_send("IN %d %d %02x %d", r, pipes[pipe_id].dev, pipes[pipe_id].ep, (int)len);
  } else {
    char hex[300];
    shim_hex(hex, (const uint8_t *)buf, (size_t)(len > 140 ? 140 : len));
    bridge_send("OUT %d %d %02x %s", r, pipes[pipe_id].dev, pipes[pipe_id].ep, hex);
    shim_emit("\"ev\":\"usb_out\",\"dev\":%d,\"ep\":\"0x%02x\",\"data\":\"%s\"", pipes[pipe_id].dev, pipes[pipe_id].ep, hex);
  }
  return CELL_OK;
}

int32_t cellUsbdBulkTransfer(int32_t pipe_id, void *buf, int32_t len, CellUsbdDoneCallback cb, void *arg) {
  return cellUsbdInterruptTransfer(pipe_id, buf, len, cb, arg);
}

int32_t cellUsbdControlTransfer(int32_t pipe_id, UsbDeviceRequest *req, void *buf, CellUsbdDoneCallback cb, void *arg) {
  if (!pipe_ok(pipe_id)) return CELL_USBD_ERROR_PIPE_NOT_FOUND;
  const uint8_t *raw = (const uint8_t *)req; /* 8-byte setup packet exactly as laid out in memory */
  uint16_t wlen = (uint16_t)(raw[6] | raw[7] << 8);
  int in = (raw[0] & 0x80) != 0;
  int r = req_alloc(pipe_id, cb, arg, buf, wlen, in);
  if (r < 0) return CELL_USBD_ERROR_IOREQ_NOT_ALLOCATED;
  char setup[17], data[300] = "-";
  shim_hex(setup, raw, 8);
  if (!in && wlen && buf) shim_hex(data, (const uint8_t *)buf, wlen > 140 ? 140 : wlen);
  bridge_send("CTRL %d %d %s %s", r, pipes[pipe_id].dev, setup, data);
  return CELL_OK;
}

int32_t cellUsbdSetConfiguration(int32_t pipe_id, uint8_t config, CellUsbdDoneCallback cb, void *arg) {
  if (!pipe_ok(pipe_id)) return CELL_USBD_ERROR_PIPE_NOT_FOUND;
  int r = req_alloc(pipe_id, cb, arg, NULL, 0, 0);
  if (r < 0) return CELL_USBD_ERROR_IOREQ_NOT_ALLOCATED;
  bridge_send("SETCONF %d %d %u", r, pipes[pipe_id].dev, config);
  shim_emit("\"ev\":\"usbd_set_configuration\",\"dev\":%d,\"value\":%u", pipes[pipe_id].dev, config);
  return CELL_OK;
}

int32_t cellUsbdSetInterface(int32_t pipe_id, uint8_t ifnum, uint8_t alt, CellUsbdDoneCallback cb, void *arg) {
  if (!pipe_ok(pipe_id)) return CELL_USBD_ERROR_PIPE_NOT_FOUND;
  int r = req_alloc(pipe_id, cb, arg, NULL, 0, 0);
  if (r < 0) return CELL_USBD_ERROR_IOREQ_NOT_ALLOCATED;
  bridge_send("SETIF %d %d %u %u", r, pipes[pipe_id].dev, ifnum, alt);
  return CELL_OK;
}

int32_t cellUsbdSetPrivateData(int32_t dev_id, void *priv) {
  if (dev_id < 0 || dev_id >= MAX_DEV) return CELL_USBD_ERROR_DEVICE_NOT_FOUND;
  devs[dev_id].priv = priv;
  return CELL_OK;
}

void *cellUsbdGetPrivateData(int32_t dev_id) {
  return (dev_id >= 0 && dev_id < MAX_DEV) ? devs[dev_id].priv : NULL;
}

/* ------------------------------------------------------------------------------------- */

void shim_init(void) {
  t0_ns = mono_ns();
  const char *r = getenv("PS3SIM_ROOT");
  if (r && *r) snprintf(fs_root, sizeof(fs_root), "%s", r);
  const char *s = getenv("PS3SIM_TIME_SCALE");
  if (s && *s) time_scale = atof(s);
  for (int i = 0; i < CELL_PAD_MAX_PORT_NUM; i++) ports[i].handle = -1;
}
