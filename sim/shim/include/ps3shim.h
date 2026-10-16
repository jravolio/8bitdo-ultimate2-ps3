/*
 * ps3shim.h - force-included into every PS3 source compiled for the simulator.
 *
 * The simulator runs unmodified PS3 VSH-plugin C code as 32-bit big-endian PowerPC
 * (same byte order and pointer width as the PS3's Cell PPU) under qemu-ppc. These
 * headers stand in for the Sony Cell SDK; every value/layout comes from PSL1GHT
 * (ps3dev/PSL1GHT ppu/include) or RPCS3 (rpcs3/Emu/Io/pad_types.h, Cell/lv2).
 */
#ifndef PS3SHIM_H
#define PS3SHIM_H

#include <stdint.h>
#include <stddef.h>

#ifndef CELL_OK
#define CELL_OK 0
#endif

typedef uint64_t sys_ppu_thread_t;
typedef uint32_t sys_mutex_t;
typedef uint32_t sys_event_queue_t;
typedef uint32_t sys_event_port_t;
typedef int32_t sys_prx_id_t;
typedef uint64_t usecond_t;
typedef uint32_t second_t;
typedef int64_t sys_time_sec_t;
typedef int64_t sys_time_nsec_t;

/* lv2 syscall entry. Like the SDK macros, system_call_N leaves the result in a local
   named p1 that the caller may read afterwards (main.c does `return (int)p1;`). */
uint64_t shim_syscall(uint64_t num, uint64_t a1, uint64_t a2, uint64_t a3, uint64_t a4,
                      uint64_t a5, uint64_t a6, uint64_t a7, uint64_t a8);

#define system_call_0(n) \
  uint64_t p1 = shim_syscall((n), 0, 0, 0, 0, 0, 0, 0, 0); (void)p1
#define system_call_1(n, a1) \
  uint64_t p1 = shim_syscall((n), (uint64_t)(a1), 0, 0, 0, 0, 0, 0, 0); (void)p1
#define system_call_2(n, a1, a2) \
  uint64_t p1 = shim_syscall((n), (uint64_t)(a1), (uint64_t)(a2), 0, 0, 0, 0, 0, 0); (void)p1
#define system_call_3(n, a1, a2, a3) \
  uint64_t p1 = shim_syscall((n), (uint64_t)(a1), (uint64_t)(a2), (uint64_t)(a3), 0, 0, 0, 0, 0); (void)p1
#define system_call_4(n, a1, a2, a3, a4) \
  uint64_t p1 = shim_syscall((n), (uint64_t)(a1), (uint64_t)(a2), (uint64_t)(a3), (uint64_t)(a4), 0, 0, 0, 0); (void)p1
#define system_call_5(n, a1, a2, a3, a4, a5) \
  uint64_t p1 = shim_syscall((n), (uint64_t)(a1), (uint64_t)(a2), (uint64_t)(a3), (uint64_t)(a4), (uint64_t)(a5), 0, 0, 0); (void)p1
#define system_call_6(n, a1, a2, a3, a4, a5, a6) \
  uint64_t p1 = shim_syscall((n), (uint64_t)(a1), (uint64_t)(a2), (uint64_t)(a3), (uint64_t)(a4), (uint64_t)(a5), (uint64_t)(a6), 0, 0); (void)p1
#define system_call_7(n, a1, a2, a3, a4, a5, a6, a7) \
  uint64_t p1 = shim_syscall((n), (uint64_t)(a1), (uint64_t)(a2), (uint64_t)(a3), (uint64_t)(a4), (uint64_t)(a5), (uint64_t)(a6), (uint64_t)(a7), 0); (void)p1
#define system_call_8(n, a1, a2, a3, a4, a5, a6, a7, a8) \
  uint64_t p1 = shim_syscall((n), (uint64_t)(a1), (uint64_t)(a2), (uint64_t)(a3), (uint64_t)(a4), (uint64_t)(a5), (uint64_t)(a6), (uint64_t)(a7), (uint64_t)(a8)); (void)p1

int sys_time_get_current_time(sys_time_sec_t *sec, sys_time_nsec_t *nsec);

#endif
