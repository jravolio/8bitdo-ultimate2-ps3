#ifndef SHIM_CELL_USBD_H
#define SHIM_CELL_USBD_H
#include "ps3shim.h"

/* libusbd - layouts from PSL1GHT usb/usb.h (identical to USB wire offsets; the PS3 code
   reads multi-byte fields with SWAP16 because descriptors stay little-endian). */
#define CELL_USBD_ERROR_NOT_INITIALIZED        ((int32_t)0x80110001)
#define CELL_USBD_ERROR_ALREADY_INITIALIZED    ((int32_t)0x80110002)
#define CELL_USBD_ERROR_NO_MEMORY              ((int32_t)0x80110003)
#define CELL_USBD_ERROR_INVALID_PARAM          ((int32_t)0x80110004)
#define CELL_USBD_ERROR_INVALID_TRANSFER_TYPE  ((int32_t)0x80110005)
#define CELL_USBD_ERROR_LDD_ALREADY_REGISTERED ((int32_t)0x80110006)
#define CELL_USBD_ERROR_LDD_NOT_ALLOCATED      ((int32_t)0x80110007)
#define CELL_USBD_ERROR_LDD_NOT_RELEASED       ((int32_t)0x80110008)
#define CELL_USBD_ERROR_LDD_NOT_FOUND          ((int32_t)0x80110009)
#define CELL_USBD_ERROR_DEVICE_NOT_FOUND       ((int32_t)0x8011000a)
#define CELL_USBD_ERROR_PIPE_NOT_ALLOCATED     ((int32_t)0x8011000b)
#define CELL_USBD_ERROR_PIPE_NOT_RELEASED      ((int32_t)0x8011000c)
#define CELL_USBD_ERROR_PIPE_NOT_FOUND         ((int32_t)0x8011000d)
#define CELL_USBD_ERROR_IOREQ_NOT_ALLOCATED    ((int32_t)0x8011000e)
#define CELL_USBD_ERROR_IOREQ_NOT_RELEASED     ((int32_t)0x8011000f)
#define CELL_USBD_ERROR_IOREQ_NOT_FOUND        ((int32_t)0x80110010)
#define CELL_USBD_ERROR_CANNOT_GET_DESCRIPTOR  ((int32_t)0x80110011)
#define CELL_USBD_ERROR_FATAL                  ((int32_t)0x801100ff)

#define CELL_USBD_PROBE_SUCCEEDED   0
#define CELL_USBD_PROBE_FAILED      (-1)
#define CELL_USBD_ATTACH_SUCCEEDED  0
#define CELL_USBD_ATTACH_FAILED     (-1)
#define CELL_USBD_DETACH_SUCCEEDED  0
#define CELL_USBD_DETACH_FAILED     (-1)

/* transfer completion codes passed as 'result' */
#define USBD_HC_CC_NOERR   0x0
#define USBD_HC_CC_MISSMF  0x1
#define USBD_HC_CC_XACT    0x2
#define USBD_HC_CC_BABBLE  0x4
#define USBD_HC_CC_DATABUF 0x8

#define USB_DESCRIPTOR_TYPE_DEVICE        0x01
#define USB_DESCRIPTOR_TYPE_CONFIGURATION 0x02
#define USB_DESCRIPTOR_TYPE_STRING        0x03
#define USB_DESCRIPTOR_TYPE_INTERFACE     0x04
#define USB_DESCRIPTOR_TYPE_ENDPOINT      0x05
#define USB_DESCRIPTOR_TYPE_HID           0x21
#define USB_DESCRIPTOR_TYPE_REPORT        0x22

typedef struct UsbDeviceDescriptor {
  uint8_t bLength;
  uint8_t bDescriptorType;
  uint16_t bcdUSB;
  uint8_t bDeviceClass;
  uint8_t bDeviceSubClass;
  uint8_t bDeviceProtocol;
  uint8_t bMaxPacketSize0;
  uint16_t idVendor;
  uint16_t idProduct;
  uint16_t bcdDevice;
  uint8_t iManufacturer;
  uint8_t iProduct;
  uint8_t iSerialNumber;
  uint8_t bNumConfigurations;
} UsbDeviceDescriptor;

typedef struct UsbConfigurationDescriptor {
  uint8_t bLength;
  uint8_t bDescriptorType;
  uint16_t wTotalLength;
  uint8_t bNumInterfaces;
  uint8_t bConfigurationValue;
  uint8_t iConfiguration;
  uint8_t bmAttributes;
  uint8_t bMaxPower;
} UsbConfigurationDescriptor;

typedef struct UsbInterfaceDescriptor {
  uint8_t bLength;
  uint8_t bDescriptorType;
  uint8_t bInterfaceNumber;
  uint8_t bAlternateSetting;
  uint8_t bNumEndpoints;
  uint8_t bInterfaceClass;
  uint8_t bInterfaceSubClass;
  uint8_t bInterfaceProtocol;
  uint8_t iInterface;
} UsbInterfaceDescriptor;

typedef struct UsbEndpointDescriptor {
  uint8_t bLength;
  uint8_t bDescriptorType;
  uint8_t bEndpointAddress;
  uint8_t bmAttributes;
  uint16_t wMaxPacketSize;
  uint8_t bInterval;
} UsbEndpointDescriptor;

typedef struct UsbDeviceRequest {
  uint8_t bmRequestType;
  uint8_t bRequest;
  uint16_t wValue;
  uint16_t wIndex;
  uint16_t wLength;
} UsbDeviceRequest;

typedef struct CellUsbdLddOps {
  const char *name;
  int32_t (*probe)(int32_t dev_id);
  int32_t (*attach)(int32_t dev_id);
  int32_t (*detach)(int32_t dev_id);
} CellUsbdLddOps;

typedef void (*CellUsbdDoneCallback)(int32_t result, int32_t count, void *arg);

int32_t cellUsbdInit(void);
int32_t cellUsbdEnd(void);
int32_t cellUsbdRegisterLdd(CellUsbdLddOps *lddops);
int32_t cellUsbdUnregisterLdd(CellUsbdLddOps *lddops);
int32_t cellUsbdRegisterExtraLdd(CellUsbdLddOps *lddops, uint16_t id_vendor, uint16_t id_product);
int32_t cellUsbdRegisterExtraLdd2(CellUsbdLddOps *lddops, uint16_t id_vendor, uint16_t id_product_min,
                                  uint16_t id_product_max);
int32_t cellUsbdUnregisterExtraLdd(CellUsbdLddOps *lddops);
void *cellUsbdScanStaticDescriptor(int32_t dev_id, void *ptr, unsigned char type);
int32_t cellUsbdOpenPipe(int32_t dev_id, UsbEndpointDescriptor *ep_desc);
int32_t cellUsbdClosePipe(int32_t pipe_id);
int32_t cellUsbdControlTransfer(int32_t pipe_id, UsbDeviceRequest *req, void *buf,
                                CellUsbdDoneCallback done_cb, void *arg);
int32_t cellUsbdBulkTransfer(int32_t pipe_id, void *buf, int32_t len, CellUsbdDoneCallback done_cb, void *arg);
int32_t cellUsbdInterruptTransfer(int32_t pipe_id, void *buf, int32_t len, CellUsbdDoneCallback done_cb,
                                  void *arg);
int32_t cellUsbdSetConfiguration(int32_t pipe_id, uint8_t config, CellUsbdDoneCallback done_cb, void *arg);
int32_t cellUsbdSetInterface(int32_t pipe_id, uint8_t ifnum, uint8_t alt, CellUsbdDoneCallback done_cb, void *arg);
int32_t cellUsbdSetPrivateData(int32_t dev_id, void *priv);
void *cellUsbdGetPrivateData(int32_t dev_id);
#endif
