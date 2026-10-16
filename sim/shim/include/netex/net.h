#ifndef SHIM_NETEX_NET_H
#define SHIM_NETEX_NET_H
/* The PS3 network stack is BSD sockets; the simulator uses the host's real sockets
   (qemu-user passes them through), so the viewer telemetry really reaches the PC. */
#include "ps3shim.h"
int socketclose(int s);
int sys_net_initialize_network(void);
#endif
