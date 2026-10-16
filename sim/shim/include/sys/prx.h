#ifndef SHIM_SYS_PRX_H
#define SHIM_SYS_PRX_H
#include "ps3shim.h"

#define SYS_PRX_RESIDENT     0
#define SYS_PRX_NO_RESIDENT  1
#define SYS_PRX_START_OK     0
#define SYS_PRX_STOP_OK      0

/* The simulated PRX loader (vsh_main.c) calls these exactly like lv2 calls a module's
   module_start/module_stop entries. */
#define SYS_MODULE_INFO(name, attr, major, minor) const char shim_module_name[] = #name
#define SYS_MODULE_START(fn) int (*const shim_module_start)(uint64_t) = (int (*)(uint64_t))(fn)
#define SYS_MODULE_STOP(fn)  int (*const shim_module_stop)(void) = (int (*)(void))(fn)
#endif
