#ifndef SHIM_SYS_SYNCHRONIZATION_H
#define SHIM_SYS_SYNCHRONIZATION_H
#include "ps3shim.h"

#define SYS_SYNC_FIFO          0x00001
#define SYS_SYNC_PRIORITY      0x00002
#define SYS_SYNC_RECURSIVE     0x00010
#define SYS_SYNC_NOT_RECURSIVE 0x00020
#define SYS_SYNC_NOT_PROCESS_SHARED 0x00200
#define SYS_SYNC_NOT_ADAPTIVE  0x02000

typedef struct sys_mutex_attribute {
  uint32_t attr_protocol;
  uint32_t attr_recursive;
  uint32_t attr_pshared;
  uint32_t attr_adaptive;
  uint64_t key;
  int32_t flags;
  uint32_t pad;
  char name[8];
} sys_mutex_attribute_t;

#define sys_mutex_attribute_initialize(x)                                   \
  ((x).attr_protocol = SYS_SYNC_PRIORITY, (x).attr_recursive = SYS_SYNC_NOT_RECURSIVE, \
   (x).attr_pshared = SYS_SYNC_NOT_PROCESS_SHARED, (x).attr_adaptive = SYS_SYNC_NOT_ADAPTIVE, \
   (x).key = 0, (x).flags = 0, (x).name[0] = '\0')

int sys_mutex_create(sys_mutex_t *mutex_id, sys_mutex_attribute_t *attr);
int sys_mutex_destroy(sys_mutex_t mutex_id);
int sys_mutex_lock(sys_mutex_t mutex_id, usecond_t timeout);
int sys_mutex_trylock(sys_mutex_t mutex_id);
int sys_mutex_unlock(sys_mutex_t mutex_id);
#endif
