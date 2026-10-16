"""XInput (Xbox 360 wired protocol) reports, and an independent DualShock 3 reference model.

The reference model says what a game reading a DualShock 3 through libpad (cellPadGetData)
should see for a given physical input. It is written from the libpad/DS3 semantics
(PSL1GHT io/pad.h, RPCS3 pad_types.h, Linux hid-sony), NOT from XPAD's code, so comparing the
simulated console's output against it is a real check of the plugin.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# XInput button word (little-endian on the wire, bytes 2..3 of the 20-byte report)
XB = {
    "UP": 0x0001, "DOWN": 0x0002, "LEFT": 0x0004, "RIGHT": 0x0008,
    "START": 0x0010, "BACK": 0x0020, "LS": 0x0040, "RS": 0x0080,
    "LB": 0x0100, "RB": 0x0200, "GUIDE": 0x0400,
    "A": 0x1000, "B": 0x2000, "X": 0x4000, "Y": 0x8000,
}


@dataclass
class XState:
    buttons: set[str] = field(default_factory=set)
    lt: int = 0
    rt: int = 0
    lx: int = 0
    ly: int = 0
    rx: int = 0
    ry: int = 0

    def report(self) -> bytes:
        word = 0
        for b in self.buttons:
            word |= XB[b]
        return struct.pack("<BBHBBhhhh6x", 0x00, 0x14, word, self.lt, self.rt, self.lx, self.ly, self.rx, self.ry)

    @staticmethod
    def parse(rep: bytes) -> "XState":
        _, _, word, lt, rt, lx, ly, rx, ry = struct.unpack_from("<BBHBBhhhh", rep)
        return XState({k for k, v in XB.items() if word & v}, lt, rt, lx, ly, rx, ry)


# libpad CellPadData offsets / bits
D1, D2 = 2, 3
RX, RY, LX, LY = 4, 5, 6, 7
P_RIGHT, P_LEFT, P_UP, P_DOWN, P_TRI, P_CIR, P_CRO, P_SQU, P_L1, P_R1, P_L2, P_R2 = range(8, 20)
SX, SY, SZ, SG = 20, 21, 22, 23
D1_BITS = {"SELECT": 0x01, "L3": 0x02, "R3": 0x04, "START": 0x08, "UP": 0x10, "RIGHT": 0x20, "DOWN": 0x40, "LEFT": 0x80}
D2_BITS = {"L2": 0x01, "R2": 0x02, "L1": 0x04, "R1": 0x08, "TRIANGLE": 0x10, "CIRCLE": 0x20, "CROSS": 0x40, "SQUARE": 0x80}
LDD_PS = 0x0001  # button[0] bit for the PS button on LDD pads

# Positional mapping a PS3 player expects (bottom face button = Cross, etc.)
XINPUT_TO_DS3 = {
    "A": "CROSS", "B": "CIRCLE", "X": "SQUARE", "Y": "TRIANGLE",
    "LB": "L1", "RB": "R1", "BACK": "SELECT", "START": "START", "LS": "L3", "RS": "R3",
    "UP": "UP", "DOWN": "DOWN", "LEFT": "LEFT", "RIGHT": "RIGHT", "GUIDE": "PS",
}
PRESSURE_SLOT = {"CROSS": P_CRO, "CIRCLE": P_CIR, "SQUARE": P_SQU, "TRIANGLE": P_TRI, "L1": P_L1, "R1": P_R1,
                 "UP": P_UP, "DOWN": P_DOWN, "LEFT": P_LEFT, "RIGHT": P_RIGHT}


def axis_x(v: int) -> int:
    """XInput int16 (-32768 left .. 32767 right) -> DS3 0 (left) .. 255 (right), centre ~128."""
    return max(0, min(255, (v + 32768) >> 8))


def axis_y(v: int) -> int:
    """XInput int16 (+ = up) -> DS3 0 (up) .. 255 (down)."""
    return max(0, min(255, (32767 - v) >> 8))


def expected_pad(x: XState) -> dict:
    """What a game should read for this physical state (DS3 conventions)."""
    ds3 = {XINPUT_TO_DS3[b] for b in x.buttons}
    if x.lt > 0:
        ds3.add("L2")
    if x.rt > 0:
        ds3.add("R2")
    pressure = {slot: (255 if name in ds3 else 0) for name, slot in PRESSURE_SLOT.items()}
    pressure[P_L2] = x.lt
    pressure[P_R2] = x.rt
    return {
        "digital1": sum(bit for n, bit in D1_BITS.items() if n in ds3),
        "digital2": sum(bit for n, bit in D2_BITS.items() if n in ds3),
        "ps": "PS" in ds3,
        "sticks": {LX: axis_x(x.lx), LY: axis_y(x.ly), RX: axis_x(x.rx), RY: axis_y(x.ry)},
        "pressure": pressure,
    }


def decode_pad(b: list[int]) -> dict:
    names1 = [n for n, bit in D1_BITS.items() if b[D1] & bit]
    names2 = [n for n, bit in D2_BITS.items() if b[D2] & bit]
    return {
        "buttons": (["PS"] if b[0] & LDD_PS else []) + names1 + names2,
        "LX": b[LX], "LY": b[LY], "RX": b[RX], "RY": b[RY],
        "pressure": {"L2": b[P_L2], "R2": b[P_R2], "CROSS": b[P_CRO], "CIRCLE": b[P_CIR], "SQUARE": b[P_SQU],
                     "TRIANGLE": b[P_TRI], "L1": b[P_L1], "R1": b[P_R1], "UP": b[P_UP], "DOWN": b[P_DOWN],
                     "LEFT": b[P_LEFT], "RIGHT": b[P_RIGHT]},
        "sixaxis": [b[SX], b[SY], b[SZ], b[SG]],
    }


def grade(x: XState, b: list[int], stick_tol: int = 6) -> list[str]:
    """Differences between what the game saw (b = CellPadData.button[0..23]) and the DS3 model.
    Stick values may be reshaped by XPAD's DS3 curve (80% saturation, 4% deadzone), so sticks are
    checked at rest and at the rails with a tolerance; buttons and pressure are exact."""
    e = expected_pad(x)
    diffs = []
    if b[D1] != e["digital1"]:
        diffs.append(f"digital1 0x{b[D1]:02x} != 0x{e['digital1']:02x}")
    if b[D2] != e["digital2"]:
        diffs.append(f"digital2 0x{b[D2]:02x} != 0x{e['digital2']:02x}")
    if bool(b[0] & LDD_PS) != e["ps"]:
        diffs.append(f"PS bit {bool(b[0] & LDD_PS)} != {e['ps']}")
    for slot, want in e["pressure"].items():
        if b[slot] != want:
            diffs.append(f"pressure[{slot}] {b[slot]} != {want}")
    for slot, want in e["sticks"].items():
        if want in (0, 255) or 120 <= want <= 136:
            if abs(b[slot] - want) > stick_tol:
                diffs.append(f"stick[{slot}] {b[slot]} != {want}±{stick_tol}")
        elif (b[slot] - 128) * (want - 128) < 0:
            diffs.append(f"stick[{slot}] {b[slot]} on the wrong side of centre (want ~{want})")
    return diffs


# A scripted walkthrough of every input, used by the virtual receiver and by the live test.
def walkthrough() -> list[tuple[str, XState]]:
    steps: list[tuple[str, XState]] = [("rest", XState())]
    for b in ["A", "B", "X", "Y", "LB", "RB", "BACK", "START", "GUIDE", "LS", "RS", "UP", "DOWN", "LEFT", "RIGHT"]:
        steps.append((f"press {b} -> {XINPUT_TO_DS3[b]}", XState({b})))
        steps.append(("release", XState()))
    for v in (1, 64, 128, 255):
        steps.append((f"LT={v} -> L2 pressure {v}", XState(lt=v)))
    steps.append(("release", XState()))
    for v in (1, 64, 128, 255):
        steps.append((f"RT={v} -> R2 pressure {v}", XState(rt=v)))
    steps.append(("release", XState()))
    for name, kw in [("left stick left", dict(lx=-32768)), ("left stick right", dict(lx=32767)),
                     ("left stick up", dict(ly=32767)), ("left stick down", dict(ly=-32768)),
                     ("right stick left", dict(rx=-32768)), ("right stick right", dict(rx=32767)),
                     ("right stick up", dict(ry=32767)), ("right stick down", dict(ry=-32768)),
                     ("left stick half right", dict(lx=16384)), ("right stick half up", dict(ry=16384))]:
        steps.append((name, XState(**kw)))
        steps.append(("centre", XState()))
    steps.append(("chord CROSS+SQUARE+R1+L2(200)", XState({"A", "X", "RB"}, lt=200)))
    steps.append(("release", XState()))
    return steps
