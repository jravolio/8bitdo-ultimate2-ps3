#ifndef SHIM_CELL_PAD_H
#define SHIM_CELL_PAD_H
#include "ps3shim.h"

/* libpad (sys_io) - values from PSL1GHT io/pad.h and RPCS3 Emu/Io/pad_types.h */
#define CELL_PAD_OK                         0
#define CELL_PAD_ERROR_FATAL                ((int32_t)0x80121101)
#define CELL_PAD_ERROR_INVALID_PARAMETER    ((int32_t)0x80121102)
#define CELL_PAD_ERROR_ALREADY_INITIALIZED  ((int32_t)0x80121103)
#define CELL_PAD_ERROR_UNINITIALIZED        ((int32_t)0x80121104)
#define CELL_PAD_ERROR_RESOURCE_ALLOCATION_FAILED ((int32_t)0x80121105)
#define CELL_PAD_ERROR_DATA_READ_FAILED     ((int32_t)0x80121106)
#define CELL_PAD_ERROR_NO_DEVICE            ((int32_t)0x80121107)
#define CELL_PAD_ERROR_UNSUPPORTED_GAMEPAD  ((int32_t)0x80121108)
#define CELL_PAD_ERROR_TOO_MANY_DEVICES     ((int32_t)0x80121109)
#define CELL_PAD_ERROR_EBUSY                ((int32_t)0x8012110a)

#define CELL_PAD_MAX_PORT_NUM        7
#define CELL_PAD_MAX_CODES           64
#define CELL_PAD_MAX_CAPABILITY_INFO 32
#define CELL_PAD_ACTUATOR_MAX        2

#define CELL_PAD_STATUS_DISCONNECTED   0x00000000
#define CELL_PAD_STATUS_CONNECTED      0x00000001
#define CELL_PAD_STATUS_ASSIGN_CHANGES 0x00000002

#define CELL_PAD_SETTING_PRESS_ON   0x00000002
#define CELL_PAD_SETTING_SENSOR_ON  0x00000004
#define CELL_PAD_SETTING_PRESS_OFF  0x00000000
#define CELL_PAD_SETTING_SENSOR_OFF 0x00000000

#define CELL_PAD_CAPABILITY_PS3_CONFORMITY  0x00000001
#define CELL_PAD_CAPABILITY_PRESS_MODE      0x00000002
#define CELL_PAD_CAPABILITY_SENSOR_MODE     0x00000004
#define CELL_PAD_CAPABILITY_HP_ANALOG_STICK 0x00000008
#define CELL_PAD_CAPABILITY_ACTUATOR        0x00000010

#define CELL_PAD_DEV_TYPE_STANDARD  0
#define CELL_PAD_DEV_TYPE_BD_REMOCON 4
#define CELL_PAD_DEV_TYPE_LDD       5

/* button[CELL_PAD_BTN_OFFSET_DIGITAL1] */
#define CELL_PAD_CTRL_LEFT     0x00000080
#define CELL_PAD_CTRL_DOWN     0x00000040
#define CELL_PAD_CTRL_RIGHT    0x00000020
#define CELL_PAD_CTRL_UP       0x00000010
#define CELL_PAD_CTRL_START    0x00000008
#define CELL_PAD_CTRL_R3       0x00000004
#define CELL_PAD_CTRL_L3       0x00000002
#define CELL_PAD_CTRL_SELECT   0x00000001
/* button[CELL_PAD_BTN_OFFSET_DIGITAL2] */
#define CELL_PAD_CTRL_SQUARE   0x00000080
#define CELL_PAD_CTRL_CROSS    0x00000040
#define CELL_PAD_CTRL_CIRCLE   0x00000020
#define CELL_PAD_CTRL_TRIANGLE 0x00000010
#define CELL_PAD_CTRL_R1       0x00000008
#define CELL_PAD_CTRL_L1       0x00000004
#define CELL_PAD_CTRL_R2       0x00000002
#define CELL_PAD_CTRL_L2       0x00000001
/* button[0] of LDD data */
#define CELL_PAD_CTRL_LDD_PS   0x00000001

#define CELL_PAD_BTN_OFFSET_DIGITAL1       2
#define CELL_PAD_BTN_OFFSET_DIGITAL2       3
#define CELL_PAD_BTN_OFFSET_ANALOG_RIGHT_X 4
#define CELL_PAD_BTN_OFFSET_ANALOG_RIGHT_Y 5
#define CELL_PAD_BTN_OFFSET_ANALOG_LEFT_X  6
#define CELL_PAD_BTN_OFFSET_ANALOG_LEFT_Y  7
#define CELL_PAD_BTN_OFFSET_PRESS_RIGHT    8
#define CELL_PAD_BTN_OFFSET_PRESS_LEFT     9
#define CELL_PAD_BTN_OFFSET_PRESS_UP       10
#define CELL_PAD_BTN_OFFSET_PRESS_DOWN     11
#define CELL_PAD_BTN_OFFSET_PRESS_TRIANGLE 12
#define CELL_PAD_BTN_OFFSET_PRESS_CIRCLE   13
#define CELL_PAD_BTN_OFFSET_PRESS_CROSS    14
#define CELL_PAD_BTN_OFFSET_PRESS_SQUARE   15
#define CELL_PAD_BTN_OFFSET_PRESS_L1       16
#define CELL_PAD_BTN_OFFSET_PRESS_R1       17
#define CELL_PAD_BTN_OFFSET_PRESS_L2       18
#define CELL_PAD_BTN_OFFSET_PRESS_R2       19
#define CELL_PAD_BTN_OFFSET_SENSOR_X       20
#define CELL_PAD_BTN_OFFSET_SENSOR_Y       21
#define CELL_PAD_BTN_OFFSET_SENSOR_Z       22
#define CELL_PAD_BTN_OFFSET_SENSOR_G       23

typedef struct CellPadData {
  int32_t len;
  uint16_t button[CELL_PAD_MAX_CODES];
} CellPadData;

typedef struct CellPadInfo2 {
  uint32_t max_connect;
  uint32_t now_connect;
  uint32_t system_info;
  uint32_t port_status[CELL_PAD_MAX_PORT_NUM];
  uint32_t port_setting[CELL_PAD_MAX_PORT_NUM];
  uint32_t device_capability[CELL_PAD_MAX_PORT_NUM];
  uint32_t device_type[CELL_PAD_MAX_PORT_NUM];
} CellPadInfo2;

typedef struct CellPadActParam {
  uint8_t motor[CELL_PAD_ACTUATOR_MAX];
  uint8_t reserved[6];
} CellPadActParam;

int32_t cellPadInit(uint32_t max_connect);
int32_t cellPadEnd(void);
int32_t cellPadClearBuf(uint32_t port_no);
int32_t cellPadGetData(uint32_t port_no, CellPadData *data);
int32_t cellPadGetInfo2(CellPadInfo2 *info);
int32_t cellPadSetPortSetting(uint32_t port_no, uint32_t port_setting);
int32_t cellPadSetActDirect(uint32_t port_no, CellPadActParam *param);

int32_t cellPadLddRegisterController(void);
int32_t cellPadLddDataInsert(int32_t handle, CellPadData *data);
int32_t cellPadLddGetPortNo(int32_t handle);
int32_t cellPadLddUnregisterController(int32_t handle);
#endif
