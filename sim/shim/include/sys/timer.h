#ifndef SHIM_SYS_TIMER_H
#define SHIM_SYS_TIMER_H
#include "ps3shim.h"
int sys_timer_usleep(usecond_t sleep_time);
int sys_timer_sleep(second_t sleep_time);
#endif
