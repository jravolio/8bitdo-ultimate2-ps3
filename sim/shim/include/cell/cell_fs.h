#ifndef SHIM_CELL_CELL_FS_H
#define SHIM_CELL_CELL_FS_H
#include "ps3shim.h"

/* Paths under /dev_hdd0, /dev_usb000, ... are mapped into $PS3SIM_ROOT. */
#define CELL_FS_SUCCEEDED 0
#define CELL_FS_ENOENT    ((int)0x80010006)
#define CELL_FS_EIO       ((int)0x8001002B)

#define CELL_FS_O_RDONLY 000000
#define CELL_FS_O_WRONLY 000001
#define CELL_FS_O_RDWR   000002
#define CELL_FS_O_ACCMODE 000003
#define CELL_FS_O_CREAT  000100
#define CELL_FS_O_EXCL   000200
#define CELL_FS_O_TRUNC  001000
#define CELL_FS_O_APPEND 002000

typedef int32_t CellFsErrno;

CellFsErrno cellFsOpen(const char *path, int flags, int *fd, const void *arg, uint64_t size);
CellFsErrno cellFsRead(int fd, void *buf, uint64_t nbytes, uint64_t *nread);
CellFsErrno cellFsWrite(int fd, const void *buf, uint64_t nbytes, uint64_t *nwrite);
CellFsErrno cellFsClose(int fd);
CellFsErrno cellFsUnlink(const char *path);
CellFsErrno cellFsRename(const char *from, const char *to);
#endif
