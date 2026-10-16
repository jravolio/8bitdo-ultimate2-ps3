#ifndef SHIM_SYS_PROCESS_H
#define SHIM_SYS_PROCESS_H
#include "ps3shim.h"
typedef uint32_t sys_pid_t;
sys_pid_t sys_process_getpid(void);
#endif
