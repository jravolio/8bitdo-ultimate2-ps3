#ifndef SHIM_SYS_PPU_THREAD_H
#define SHIM_SYS_PPU_THREAD_H
#include "ps3shim.h"

#define SYS_PPU_THREAD_CREATE_JOINABLE  0x0000000000000001ULL
#define SYS_PPU_THREAD_CREATE_INTERRUPT 0x0000000000000002ULL

int sys_ppu_thread_create(sys_ppu_thread_t *thread_id, void (*entry)(uint64_t), uint64_t arg,
                          int prio, size_t stacksize, uint64_t flags, const char *threadname);
void sys_ppu_thread_exit(uint64_t val);
int sys_ppu_thread_join(sys_ppu_thread_t thread_id, uint64_t *vptr);
int sys_ppu_thread_get_id(sys_ppu_thread_t *thread_id);
#endif
