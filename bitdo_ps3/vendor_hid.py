"""The receiver's vendor HID channel: report-state, key polling, and rumble.

The channel is only present while the controller is linked (2DC8:310B, MI_02,
usage page 0xFF7A).  Report-state and key polling use the short form

    81 04 <command u16> <data...>

Rumble uses the framed form

    81 04 08 00 00 00 <len u16> <crc u16> <len u32> <offset u32> <state...>

where CRC is CRC-16/MODBUS over the six state bytes.  The returned ACK means
the receiver parsed and accepted the command; it does not include motor feedback.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from . import PID_RECEIVER_XINPUT, VID_8BITDO

USAGE_PAGE = 0xFF7A
REPORT_ID_IN = 0x02
REPORT_ID_OUT = 0x81
REPORT_LEN = 64
MAX_STRENGTH = 255
MAX_DURATION_MS = 0xFFFF


def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def _crc_packet(command: int, subcommand: int, body: bytes) -> bytes:
    payload = (command.to_bytes(2, "little") + subcommand.to_bytes(2, "little")
               + len(body).to_bytes(2, "little") + crc16_modbus(body).to_bytes(2, "little")
               + len(body).to_bytes(4, "little") + bytes(4) + body)
    return bytes([REPORT_ID_OUT, 0x04]) + payload


def reportstate_packet(state: int = 1) -> bytes:
    return bytes([REPORT_ID_OUT, 0x04, 0x07, 0x00]) + (state & 0xFFFF).to_bytes(2, "little") + bytes(12)


def readkeys_packet() -> bytes:
    return bytes([REPORT_ID_OUT, 0x04, 0x03, 0x00, 0x23, 0x01]) + bytes(12)


def rumble_packet(left: int, right: int, duration_ms: int) -> bytes:
    left = max(0, min(MAX_STRENGTH, int(left)))
    right = max(0, min(MAX_STRENGTH, int(right)))
    duration_ms = max(0, min(MAX_DURATION_MS, int(duration_ms)))
    state = (right << 24) | (left << 16) | duration_ms
    return _crc_packet(8, 0, state.to_bytes(4, "little") + bytes(2))


def stop_packet() -> bytes:
    return rumble_packet(0, 0, 0)


def find_vendor_path() -> bytes | None:
    import hid
    for device in hid.enumerate(VID_8BITDO, PID_RECEIVER_XINPUT):
        if device.get("usage_page") == USAGE_PAGE:
            return device["path"]
    return None


def wait_for_vendor(timeout: float = 60, log=None) -> bytes | None:
    end = time.monotonic() + timeout
    announced = False
    while True:
        path = find_vendor_path()
        if path:
            return path
        if time.monotonic() >= end:
            return None
        if log and not announced:
            log("  turn the controller on (press Home); waiting for the vendor HID interface...")
            announced = True
        time.sleep(0.5)


def _drain(h, timeout_ms: int = 2) -> None:
    while h.read(REPORT_LEN, timeout_ms):
        pass


def _write(h, packet: bytes) -> None:
    if h.write(packet + bytes(REPORT_LEN - len(packet))) < 0:
        raise OSError("vendor HID write failed")


def _read_ack(h, subcommand: int, timeout_ms: int) -> bytes | None:
    end = time.monotonic() + timeout_ms / 1000
    while True:
        left_ms = int((end - time.monotonic()) * 1000)
        if left_ms <= 0:
            return None
        r = bytes(h.read(REPORT_LEN, min(left_ms, 50)))
        if (len(r) >= 6 and r[0] == REPORT_ID_IN and r[1] == 0x04
                and r[2:4] == b"\x04\x00" and r[4:6] == subcommand.to_bytes(2, "little")):
            return r


@dataclass
class Pulse:
    left: int
    right: int
    duration_ms: int
    ack: str | None

    @property
    def ok(self) -> bool:
        return self.ack is not None


@dataclass
class VibeCheck:
    path: bytes | None = None
    state_ack: str | None = None
    pulses: list[Pulse] = field(default_factory=list)
    stop_ack: str | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return (self.error is None and self.state_ack is not None and bool(self.pulses)
                and all(p.ok for p in self.pulses) and self.stop_ack is not None)


def verify_rumble(pulses: int = 3, duration_ms: int = 700, strength: int = 255,
                  gap_ms: int = 300, wait: float = 0, log=None) -> VibeCheck:
    """Pulse the motors and require the receiver's ACK for every packet."""
    result = VibeCheck()
    path = find_vendor_path() if wait <= 0 else wait_for_vendor(wait, log=log)
    if not path:
        result.error = "vendor HID interface not found (receiver not linked / controller off)"
        return result
    result.path = path

    import hid
    h = hid.device()
    try:
        h.open_path(path)
    except OSError as exc:
        result.error = f"could not open the vendor HID interface: {exc}"
        return result
    try:
        _drain(h)
        _write(h, reportstate_packet(1))
        result.state_ack = (ack.hex(" ") if (ack := _read_ack(h, 7, 500)) else None)

        for i in range(max(1, pulses)):
            if log:
                log(f"  pulse {i + 1}/{max(1, pulses)}: {strength}/{strength} for {duration_ms} ms")
            _write(h, rumble_packet(strength, strength, duration_ms))
            ack = _read_ack(h, 8, 800)
            result.pulses.append(Pulse(strength, strength, duration_ms, ack.hex(" ") if ack else None))
            if i + 1 < max(1, pulses):
                time.sleep((duration_ms + gap_ms) / 1000)
    except OSError as exc:
        result.error = str(exc)
    finally:
        try:
            _write(h, stop_packet())
            ack = _read_ack(h, 8, 500)
            result.stop_ack = ack.hex(" ") if ack else None
        except OSError:
            pass
        h.close()
    return result
