"""A virtual 8BitDo Ultimate 2 receiver in XInput mode (2DC8:310B).

Descriptors: if captures/310b_descriptors.json exists (written by `8bitdo-ps3 capture-descriptors`
from the real receiver), those exact bytes are used. Otherwise a model of the composite that
Windows recorded for 310B is used: interface 0 = XInput (FF/5D/01, interrupt IN 0x81 + OUT 0x02),
interface 1 = HID keyboard/consumer/mouse (macros), interface 2 = HID vendor (0xFF7A config).
"""
from __future__ import annotations

import json
import struct
import threading
import time

from . import CAPTURES, PID_RECEIVER_XINPUT, VID_8BITDO
from .xinput import XState


def _model_descriptors() -> bytes:
    dev = struct.pack("<BBHBBBBHHHBBBB", 18, 1, 0x0200, 0xFF, 0xFF, 0xFF, 64, VID_8BITDO, PID_RECEIVER_XINPUT,
                      0x0100, 1, 2, 3, 1)
    xusb = bytes([0x11, 0x21, 0x00, 0x01, 0x01, 0x25, 0x81, 0x14, 0x00, 0x00, 0x00, 0x00, 0x13, 0x02, 0x08, 0x00, 0x00])
    if0 = bytes([9, 4, 0, 0, 2, 0xFF, 0x5D, 0x01, 0]) + xusb + \
        bytes([7, 5, 0x81, 3, 0x20, 0, 1]) + bytes([7, 5, 0x02, 3, 0x20, 0, 8])
    if1 = bytes([9, 4, 1, 0, 1, 3, 0, 0, 0]) + bytes([9, 0x21, 0x11, 0x01, 0, 1, 0x22, 0x60, 0]) + \
        bytes([7, 5, 0x83, 3, 0x40, 0, 1])
    if2 = bytes([9, 4, 2, 0, 2, 3, 0, 0, 0]) + bytes([9, 0x21, 0x11, 0x01, 0, 1, 0x22, 0x25, 0]) + \
        bytes([7, 5, 0x84, 3, 0x40, 0, 1]) + bytes([7, 5, 0x05, 3, 0x40, 0, 1])
    body = if0 + if1 + if2
    cfg = struct.pack("<BBHBBBBB", 9, 2, 9 + len(body), 3, 1, 0, 0xA0, 0xFA) + body
    return dev + cfg


def load_descriptors() -> tuple[bytes, str]:
    path = CAPTURES / "310b_descriptors.json"
    if path.exists():
        d = json.loads(path.read_text())
        return bytes.fromhex(d["device"]) + bytes.fromhex(d["configuration"]), f"captured ({path.name})"
    return _model_descriptors(), "modelled (capture the real ones with: 8bitdo-ps3 capture-descriptors)"


class VirtualReceiver:
    """Behaves like the receiver's XInput interface: answers interrupt-IN polls with the current
    20-byte report (immediately on change, otherwise every `heartbeat_ms`), records LED/rumble."""

    vid, pid = VID_8BITDO, PID_RECEIVER_XINPUT

    def __init__(self, heartbeat_ms: float = 8.0):
        self.descriptors, self.descriptor_source = load_descriptors()
        self.heartbeat = heartbeat_ms / 1000.0
        self._state = XState()
        self._changed = threading.Event()
        self._lock = threading.Lock()
        self.out_log: list[tuple[float, int, bytes]] = []
        self.configured = None

    def set_state(self, st: XState) -> None:
        with self._lock:
            self._state = st
        self._changed.set()

    def read_in(self, ep: int, length: int, cancelled: threading.Event) -> bytes | None:
        if ep != 0x81:
            cancelled.wait()  # macro/vendor interfaces stay silent
            return None
        self._changed.wait(self.heartbeat)
        if cancelled.is_set():
            return None
        self._changed.clear()
        with self._lock:
            return self._state.report()[:length]

    def write_out(self, ep: int, data: bytes) -> int:
        self.out_log.append((time.monotonic(), ep, bytes(data)))
        return len(data)

    def set_configuration(self, value: int) -> int:
        self.configured = value
        return 0

    def set_interface(self, ifnum: int, alt: int) -> int:
        return 0

    def control(self, setup: bytes, data: bytes) -> tuple[int, bytes]:
        return 0, b""

    def close(self) -> None:
        pass
