"""The 8BitDo receiver on this PC: which state it is in, its firmware (read-only vendor query that
8BitDo Ultimate Software V2 sends on connect), and a live XInput check of the PC side."""
from __future__ import annotations

import ctypes
import subprocess
import time
from dataclasses import dataclass

from . import (PID_DS3, PID_RECEIVER_IDLE, PID_RECEIVER_XINPUT, PID_SWITCH_PRO, PRODUCT_ULTIMATE2_BT_RR,
               VID_8BITDO, VID_NINTENDO, VID_SONY)

INFO_CMD = bytes([0x81, 0x04, 0x00, 0x01])  # Ultimate Software V2 IDLE_CMD / getRR_CMD (read-only)
PRODUCTS = {PRODUCT_ULTIMATE2_BT_RR: "Ultimate 2 Bluetooth receiver", 0x3013: "Ultimate 2.4G receiver",
            0x6008: "Ultimate BT receiver", 0x9023: "Ultimate 2.4G receiver (START)"}


@dataclass
class ReceiverState:
    state: str              # absent | idle | xinput | switch
    pid: int | None = None
    firmware: str | None = None
    product: str | None = None
    raw_info: str | None = None


def _hid_devices():
    import hid
    return hid.enumerate()


def query_info(vendor_path: bytes) -> tuple[str, int, str] | None:
    import hid
    h = hid.device()
    h.open_path(vendor_path)
    try:
        for _ in range(5):
            h.write(INFO_CMD + bytes(64 - len(INFO_CMD)))
            r = bytes(h.read(64, 300))
            if len(r) >= 24 and r[0] == 0x02 and r[1] == 0x04 and r[5] == 0x01:
                return f"{(r[18] | r[19] << 8) / 100:.2f}", r[22] | r[23] << 8, r[:24].hex(" ")
    finally:
        h.close()
    return None


def receiver_state() -> ReceiverState:
    devs = _hid_devices()
    bitdo = [d for d in devs if d["vendor_id"] == VID_8BITDO and d["product_id"] in (PID_RECEIVER_IDLE, PID_RECEIVER_XINPUT)]
    if any(d["vendor_id"] == VID_NINTENDO and d["product_id"] == PID_SWITCH_PRO for d in devs) and not bitdo:
        return ReceiverState("switch", PID_SWITCH_PRO)
    if not bitdo:
        return ReceiverState("absent")
    pid = bitdo[0]["product_id"]
    st = ReceiverState("xinput" if pid == PID_RECEIVER_XINPUT else "idle", pid)
    vendor = [d for d in bitdo if d["usage_page"] == 0x8C or d["usage_page"] >= 0xFF00]
    for d in vendor:
        try:
            info = query_info(d["path"])
        except OSError:
            continue
        if info:
            st.firmware, prod, st.raw_info = info
            st.product = PRODUCTS.get(prod, f"product 0x{prod:04X}")
            break
    return st


# ---- PC side: XInput -------------------------------------------------------------------------

class _XGamepad(ctypes.Structure):
    _fields_ = [("wButtons", ctypes.c_ushort), ("bLeftTrigger", ctypes.c_ubyte), ("bRightTrigger", ctypes.c_ubyte),
                ("sThumbLX", ctypes.c_short), ("sThumbLY", ctypes.c_short), ("sThumbRX", ctypes.c_short),
                ("sThumbRY", ctypes.c_short)]


class _XState(ctypes.Structure):
    _fields_ = [("dwPacketNumber", ctypes.c_uint), ("Gamepad", _XGamepad)]


XINPUT_BUTTONS = {0x0001: "UP", 0x0002: "DOWN", 0x0004: "LEFT", 0x0008: "RIGHT", 0x0010: "START", 0x0020: "BACK",
                  0x0040: "LS", 0x0080: "RS", 0x0100: "LB", 0x0200: "RB", 0x1000: "A", 0x2000: "B", 0x4000: "X", 0x8000: "Y"}


def xinput_slots() -> dict[int, dict]:
    """Windows XInput view (what PC games see). Returns connected slots."""
    x = ctypes.windll.xinput1_4
    out = {}
    for i in range(4):
        st = _XState()
        if x.XInputGetState(i, ctypes.byref(st)) == 0:
            g = st.Gamepad
            out[i] = {"buttons": [n for b, n in XINPUT_BUTTONS.items() if g.wButtons & b], "LT": g.bLeftTrigger,
                      "RT": g.bRightTrigger, "LX": g.sThumbLX, "LY": g.sThumbLY, "RX": g.sThumbRX, "RY": g.sThumbRY,
                      "packet": st.dwPacketNumber}
    return out


def xinput_rumble(slot: int, left: float, right: float) -> None:
    class Vib(ctypes.Structure):
        _fields_ = [("l", ctypes.c_ushort), ("r", ctypes.c_ushort)]
    ctypes.windll.xinput1_4.XInputSetState(slot, ctypes.byref(Vib(int(left * 65535), int(right * 65535))))


# ---- DS3 on this PC ---------------------------------------------------------------------------

def ds3_status() -> str:
    ps = ("Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match 'VID_054C&PID_0268|VID_0000&PID_0002' } | "
          "ForEach-Object { $_.Status + '|' + $_.InstanceId }")
    out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
    if any(f"VID_{VID_SONY:04X}&PID_{PID_DS3:04X}" in ln.upper() and ln.startswith("OK") for ln in lines):
        return "connected (LEDs blink until a PS3 or a driver assigns a player number; that is normal on a PC)"
    if any("VID_0000&PID_0002" in ln.upper() for ln in lines):
        return ("a USB device failed enumeration (Device Descriptor Request Failed) on the port the DS3 used: "
                "unplug the DS3 cable, wait 5 s, plug it into a rear USB port")
    return "not connected to this PC"


def wait_for(state: str, timeout: float = 60) -> ReceiverState:
    end = time.monotonic() + timeout
    while True:
        st = receiver_state()
        if st.state == state or time.monotonic() > end:
            return st
        time.sleep(0.5)
