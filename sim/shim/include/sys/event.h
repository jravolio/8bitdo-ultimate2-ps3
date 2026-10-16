#ifndef SHIM_SYS_EVENT_H
#define SHIM_SYS_EVENT_H
#include "ps3shim.h"

#define SYS_EVENT_QUEUE_LOCAL         0x00ULL
#define SYS_EVENT_PORT_LOCAL          1
#define SYS_PPU_QUEUE                 1
#define SYS_EVENT_QUEUE_DESTROY_FORCE 1

typedef struct sys_event {
  uint64_t source;
  uint64_t data1;
  uint64_t data2;
  uint64_t data3;
} sys_event_t;

typedef struct sys_event_queue_attribute {
  uint32_t attr_protocol;
  int32_t type;
  char name[8];
} sys_event_queue_attribute_t;

#define sys_event_queue_attribute_initialize(x) \
  ((x).attr_protocol = 0x00002 /* SYS_SYNC_PRIORITY */, (x).type = SYS_PPU_QUEUE, (x).name[0] = '\0')

int sys_event_queue_create(sys_event_queue_t *equeue_id, sys_event_queue_attribute_t *attr,
                           uint64_t event_queue_key, int32_t size);
int sys_event_queue_destroy(sys_event_queue_t equeue_id, int32_t mode);
int sys_event_queue_receive(sys_event_queue_t equeue_id, sys_event_t *event, usecond_t timeout);
int sys_event_queue_tryreceive(sys_event_queue_t equeue_id, sys_event_t *event_array, int32_t size,
                               int32_t *number);
int sys_event_port_create(sys_event_port_t *eport_id, int32_t port_type, uint64_t name);
int sys_event_port_connect_ipc(sys_event_port_t eport_id, uint64_t key);
int sys_event_port_send(sys_event_port_t eport_id, uint64_t data1, uint64_t data2, uint64_t data3);
#endif
