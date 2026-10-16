#ifndef SHIM_INTERNAL_H
#define SHIM_INTERNAL_H
#include <stddef.h>
#include <stdint.h>
#include <cell/pad.h>

void shim_init(void);
int shim_setup_vsh_exports(void);
int shim_usb_init(const char *hostport);
void shim_emit(const char *fmt, ...);
void shim_json_str(char *dst, size_t cap, const char *s);
void shim_hex(char *dst, const uint8_t *src, size_t n);
double shim_now(void);
int shim_event_send_ipc(uint64_t key, uint64_t d1, uint64_t d2, uint64_t d3);
void shim_pad_add_native_ds3(int port);
int shim_pad_game_read(int port, CellPadData *out, int *type, uint32_t *seq);
void shim_set_game_running(int on, const char *name);

/* provided by the plugin through SYS_MODULE_START / SYS_MODULE_STOP */
extern int (*const shim_module_start)(uint64_t);
extern int (*const shim_module_stop)(void);
extern const char shim_module_name[];
#endif
