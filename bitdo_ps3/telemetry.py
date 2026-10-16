"""Live telemetry from XPAD Revolution running on the PS3.

The plugin's viewer thread (main.c viewer_network_thread) listens on UDP 39001 for an "XPVD" v1
announcement and then streams 18-byte "XPV3" packets (125 Hz) to the announcing PC:
  [0:4] 'XPV3'  [4] version 1  [5] pad slot  [6] connected  [8:10] buttons (LE, viewer bits)
  [10] LT  [11] RT  [12] LX  [13] LY  [14] RX  [15] RY  [16:18] sequence (LE)
This is what the console itself reads from the receiver, so it proves the real PS3 path.
"""
from __future__ import annotations

import socket
import threading
import time

from .xinput import XState, axis_x, axis_y

DISCOVERY_PORT = 39001
DISCOVERY_PACKET = b"XPVD\x01\x00\x00\x00"
VIEWER_BITS = {"A": 0x0001, "B": 0x0002, "X": 0x0004, "Y": 0x0008, "LB": 0x0010, "RB": 0x0020, "BACK": 0x0040,
               "START": 0x0080, "LS": 0x0100, "RS": 0x0200, "UP": 0x0400, "DOWN": 0x0800, "LEFT": 0x1000,
               "RIGHT": 0x2000, "GUIDE": 0x4000}


def parse(pkt: bytes) -> dict | None:
    if len(pkt) < 18 or pkt[:4] != b"XPV3" or pkt[4] != 1:
        return None
    word = pkt[8] | pkt[9] << 8
    return {"pad": pkt[5], "connected": bool(pkt[6]), "buttons": {n for n, b in VIEWER_BITS.items() if word & b},
            "LT": pkt[10], "RT": pkt[11], "LX": pkt[12], "LY": pkt[13], "RX": pkt[14], "RY": pkt[15],
            "seq": pkt[16] | pkt[17] << 8}


def matches(st: XState, t: dict, tol: int = 6) -> list[str]:
    """Compare a physical state with what the console reports (raw values, before the DS3 curve)."""
    d = []
    if t["buttons"] != set(st.buttons):
        d.append(f"buttons {sorted(t['buttons'])} != {sorted(st.buttons)}")
    for k, want in (("LT", st.lt), ("RT", st.rt)):
        if abs(t[k] - want) > (0 if want in (0, 1) else 2):
            d.append(f"{k} {t[k]} != {want}")
    for k, want in (("LX", axis_x(st.lx)), ("LY", axis_y(st.ly)), ("RX", axis_x(st.rx)), ("RY", axis_y(st.ry))):
        if abs(t[k] - want) > tol:
            d.append(f"{k} {t[k]} != {want}")
    return d


class Telemetry:
    def __init__(self, target: str = "255.255.255.255", port: int = DISCOVERY_PORT):
        self.target, self.port = target, port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind(("0.0.0.0", 0))
        self.sock.settimeout(0.2)
        self.state: dict[int, dict] = {}
        self.source: str | None = None
        self.packets = 0
        self._cv = threading.Condition()
        self._stop = threading.Event()
        threading.Thread(target=self._announce, daemon=True).start()
        threading.Thread(target=self._recv, daemon=True).start()

    def _announce(self) -> None:
        while not self._stop.is_set():
            try:
                self.sock.sendto(DISCOVERY_PACKET, (self.target, self.port))
            except OSError:
                pass
            self._stop.wait(1.0)

    def _recv(self) -> None:
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(64)
            except (socket.timeout, OSError):
                continue
            t = parse(data)
            if t is None:
                continue
            with self._cv:
                self.source = addr[0]
                self.packets += 1
                self.state[t["pad"]] = t
                self._cv.notify_all()

    def wait(self, pred, timeout: float) -> dict | None:
        end = time.monotonic() + timeout
        with self._cv:
            while True:
                for t in list(self.state.values()):
                    if pred(t):
                        return t
                left = end - time.monotonic()
                if left <= 0:
                    return None
                self._cv.wait(left)

    def close(self) -> None:
        self._stop.set()
        self.sock.close()
