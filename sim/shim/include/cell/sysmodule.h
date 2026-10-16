#ifndef SHIM_CELL_SYSMODULE_H
#define SHIM_CELL_SYSMODULE_H
#include "ps3shim.h"
#define CELL_SYSMODULE_IO   0x0017
#define CELL_SYSMODULE_USBD 0x001c
int cellSysmoduleLoadModule(uint16_t id);
int cellSysmoduleUnloadModule(uint16_t id);
int cellSysmoduleIsLoaded(uint16_t id);
#endif
