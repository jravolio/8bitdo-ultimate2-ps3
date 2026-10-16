"""USB bridge between the simulated PS3 (qemu-ppc in WSL) and a USB device on this PC.

The simulated console's libusbd forwards every USB operation here over TCP (one text line per
operation, see sim/shim/lv2_shim.c). The device is either the VirtualReceiver or the real 8BitDo
receiver opened through libusb + UsbDk (UsbDk temporarily detaches it from Windows while the
simulation runs, then hands it back).

Protocol (one line each):
  console -> bridge   HELLO | WATCH vid pid | SETCONF req dev val | SETIF req dev if alt
                      IN req dev ep len | OUT req dev ep hex | CTRL req dev setup8hex data|- | CANCEL dev ep
  bridge -> console   ATTACH dev vid pid hexdesc | DETACH dev | DONE req result count hex|-
"""
from __future__ import annotations

import ctypes
import socket
import threading
from pathlib import Path

from .pcap import UsbPcapWriter


class RealUsbDevice:
    """The physical receiver through libusb with the UsbDk backend (no driver changes)."""

    def __init__(self, vid: int, pid: int):
        import libusb_package
        import usb.backend.libusb1
        import usb.core
        import usb.util

        self._usb = usb
        path = libusb_package.get_library_path()
        be = usb.backend.libusb1.get_backend(find_library=lambda _: str(path))
        be.lib.libusb_set_option.argtypes = [ctypes.c_void_p, ctypes.c_int]
        if be.lib.libusb_set_option(be.ctx, 1) != 0:  # LIBUSB_OPTION_USE_USBDK, on the live context
            raise RuntimeError("UsbDk backend unavailable (is the UsbDk service installed and running?)")
        self.dev = usb.core.find(idVendor=vid, idProduct=pid, backend=be)
        if self.dev is None:
            raise RuntimeError(f"{vid:04x}:{pid:04x} not found")
        self.vid, self.pid = vid, pid
        devd = bytes(self.dev.ctrl_transfer(0x80, 6, 0x0100, 0, 18, 1000))
        head = bytes(self.dev.ctrl_transfer(0x80, 6, 0x0200, 0, 9, 1000))
        total = head[2] | head[3] << 8
        cfg = bytes(self.dev.ctrl_transfer(0x80, 6, 0x0200, 0, total, 1000))
        self.descriptors = devd + cfg
        self.descriptor_source = "real device"
        self._claimed: set[int] = set()
        self._ep_iface = self._map_endpoints(cfg)

    @staticmethod
    def _map_endpoints(cfg: bytes) -> dict[int, int]:
        m, i, iface = {}, 0, 0
        while i + 1 < len(cfg) and cfg[i] >= 2:
            if cfg[i + 1] == 4:
                iface = cfg[i + 2]
            elif cfg[i + 1] == 5:
                m[cfg[i + 2]] = iface
            i += cfg[i]
        return m

    def _claim(self, ep: int) -> None:
        iface = self._ep_iface.get(ep, 0)
        if iface not in self._claimed:
            self._usb.util.claim_interface(self.dev, iface)
            self._claimed.add(iface)

    def read_in(self, ep: int, length: int, cancelled: threading.Event) -> bytes | None:
        self._claim(ep)
        while not cancelled.is_set():
            try:
                return bytes(self.dev.read(ep, length, timeout=100))
            except self._usb.core.USBTimeoutError:
                continue
        return None

    def write_out(self, ep: int, data: bytes) -> int:
        self._claim(ep)
        return self.dev.write(ep, data, timeout=500)

    def set_configuration(self, value: int) -> int:
        try:
            self.dev.set_configuration(value)
        except (NotImplementedError, self._usb.core.USBError):
            pass  # Windows already configured it with the same (only) configuration
        return 0

    def set_interface(self, ifnum: int, alt: int) -> int:
        self.dev.set_interface_altsetting(ifnum, alt)
        return 0

    def control(self, setup: bytes, data: bytes) -> tuple[int, bytes]:
        bm, breq = setup[0], setup[1]
        wv, wi, wl = (setup[2] | setup[3] << 8), (setup[4] | setup[5] << 8), (setup[6] | setup[7] << 8)
        if bm & 0x80:
            return 0, bytes(self.dev.ctrl_transfer(bm, breq, wv, wi, wl, 1000))
        self.dev.ctrl_transfer(bm, breq, wv, wi, data, 1000)
        return 0, b""

    def close(self) -> None:
        for i in self._claimed:
            try:
                self._usb.util.release_interface(self.dev, i)
            except Exception:
                pass
        self._usb.util.dispose_resources(self.dev)


class Bridge:
    def __init__(self, device, port: int = 0, pcap_path: Path | None = None, log=print):
        self.device = device
        self.port = port
        self.log = log
        self.pcap = UsbPcapWriter(pcap_path) if pcap_path else None
        self.watch: set[tuple[int, int]] = set()
        self.attached = False
        self.dev_id = 1
        self._conn: socket.socket | None = None
        self._wlock = threading.Lock()
        self._cancel: dict[int, threading.Event] = {}
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", port))  # port 0: let Windows pick one outside the Hyper-V reserved ranges
        self.port = self._srv.getsockname()[1]
        self._srv.listen(1)
        self.connected = threading.Event()
        self.stats = {"in": 0, "out": 0, "ctrl": 0}
        threading.Thread(target=self._accept, daemon=True).start()

    def _send(self, line: str) -> None:
        with self._wlock:
            if self._conn:
                try:
                    self._conn.sendall((line + "\n").encode())
                except OSError:
                    pass

    def _accept(self) -> None:
        conn, _ = self._srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self._conn = conn
        self.connected.set()
        buf = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except OSError:
                break
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                self._handle(line.decode(errors="replace").strip())
        for ev in self._cancel.values():
            ev.set()

    def plug(self) -> None:
        """Present the device to the console (hot-plug)."""
        if self.attached:
            return
        if (self.device.vid, self.device.pid) not in self.watch:
            return  # like lv2: unclaimed VID/PIDs never reach an LDD
        self.attached = True
        self._send(f"ATTACH {self.dev_id} {self.device.vid:04x} {self.device.pid:04x} {self.device.descriptors.hex()}")
        self.log(f"bridge: plugged {self.device.vid:04x}:{self.device.pid:04x} ({self.device.descriptor_source})")

    def unplug(self) -> None:
        if self.attached:
            self.attached = False
            for ev in self._cancel.values():
                ev.set()
            self._cancel.clear()
            self._send(f"DETACH {self.dev_id}")

    def _done(self, req: str, result: int, data: bytes = b"", count: int | None = None) -> None:
        self._send(f"DONE {req} {result} {len(data) if count is None else count} {data.hex() if data else '-'}")

    def _handle(self, line: str) -> None:
        p = line.split()
        if not p:
            return
        cmd = p[0]
        if cmd == "WATCH":
            self.watch.add((int(p[1], 16), int(p[2], 16)))
            if (self.device.vid, self.device.pid) == (int(p[1], 16), int(p[2], 16)):
                self.plug()
        elif cmd == "SETCONF":
            if self.pcap:
                self.pcap.control_setup(self.dev_id, bytes([0x00, 0x09, int(p[3]), 0, 0, 0, 0, 0]))
            self._done(p[1], self.device.set_configuration(int(p[3])))
        elif cmd == "SETIF":
            self._done(p[1], self.device.set_interface(int(p[3]), int(p[4])))
        elif cmd == "IN":
            req, ep, ln = p[1], int(p[3], 16), int(p[4])
            ev = self._cancel.setdefault(ep, threading.Event())
            threading.Thread(target=self._in_worker, args=(req, ep, ln, ev), daemon=True).start()
        elif cmd == "OUT":
            req, ep, data = p[1], int(p[3], 16), bytes.fromhex(p[4]) if p[4] != "-" else b""
            self.stats["out"] += 1
            if self.pcap:
                self.pcap.interrupt(self.dev_id, ep, data, completion=False)
            try:
                n = self.device.write_out(ep, data)
                self._done(req, 0, count=n)
            except Exception as exc:  # stall/timeout on the device
                self.log(f"bridge: OUT {ep:#x} failed: {exc}")
                self._done(req, 2, count=0)
        elif cmd == "CTRL":
            setup = bytes.fromhex(p[3])
            data = bytes.fromhex(p[4]) if len(p) > 4 and p[4] != "-" else b""
            self.stats["ctrl"] += 1
            if self.pcap:
                self.pcap.control_setup(self.dev_id, setup)
            try:
                r, back = self.device.control(setup, data)
                self._done(p[1], r, back)
            except Exception as exc:
                self.log(f"bridge: CTRL failed: {exc}")
                self._done(p[1], 2)
        elif cmd == "CANCEL":
            ev = self._cancel.pop(int(p[2], 16), None)
            if ev:
                ev.set()

    def _in_worker(self, req: str, ep: int, ln: int, cancelled: threading.Event) -> None:
        try:
            data = self.device.read_in(ep, ln, cancelled)
        except Exception as exc:
            self.log(f"bridge: IN {ep:#x} failed: {exc}")
            if "No such device" in str(exc) or "not found" in str(exc).lower():
                self.unplug()
            self._done(req, 2, count=0)
            return
        if data is None:
            return  # cancelled: the console already completed it locally
        self.stats["in"] += 1
        if self.pcap:
            self.pcap.interrupt(self.dev_id, ep, data, completion=True)
        self._done(req, 0, data)

    def close(self) -> None:
        self.unplug()
        try:
            self._srv.close()
            if self._conn:
                self._conn.close()
        except OSError:
            pass
        if self.pcap:
            self.pcap.close()
        self.device.close()
