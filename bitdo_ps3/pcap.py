"""Minimal USBPcap-format (.pcap, LINKTYPE_USBPCAP=249) writer, so the simulated console's USB
traffic can be opened in Wireshark/TShark or REA's `inspect-packet-capture`, and diffed against a
real USBPcapCMD capture of the same receiver on Windows."""
from __future__ import annotations

import struct
import threading
import time

LINKTYPE_USBPCAP = 249
URB_FUNCTION_SELECT_CONFIGURATION = 0x0000
URB_FUNCTION_CONTROL_TRANSFER = 0x0008
URB_FUNCTION_BULK_OR_INTERRUPT_TRANSFER = 0x0009
XFER_INTERRUPT, XFER_CONTROL = 1, 2


class UsbPcapWriter:
    def __init__(self, path):
        self.f = open(path, "wb")
        self.f.write(struct.pack("<IHHiIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, LINKTYPE_USBPCAP))
        self.lock = threading.Lock()
        self.irp = 1

    def _write(self, function: int, info: int, device: int, endpoint: int, transfer: int, data: bytes,
               stage: int | None = None) -> None:
        hdr_len = 27 + (1 if stage is not None else 0)
        with self.lock:
            self.irp += 1
            hdr = struct.pack("<HQIHBHHBBI", hdr_len, self.irp, 0, function, info, 1, device, endpoint, transfer, len(data))
            if stage is not None:
                hdr += bytes([stage])
            pkt = hdr + data
            t = time.time()
            self.f.write(struct.pack("<IIII", int(t), int((t % 1) * 1e6), len(pkt), len(pkt)) + pkt)
            self.f.flush()

    def interrupt(self, device: int, endpoint: int, data: bytes, completion: bool) -> None:
        self._write(URB_FUNCTION_BULK_OR_INTERRUPT_TRANSFER, 1 if completion else 0, device, endpoint,
                    XFER_INTERRUPT, data)

    def control_setup(self, device: int, setup: bytes) -> None:
        self._write(URB_FUNCTION_CONTROL_TRANSFER, 0, device, 0x00, XFER_CONTROL, setup, stage=0)

    def close(self) -> None:
        with self.lock:
            self.f.close()
