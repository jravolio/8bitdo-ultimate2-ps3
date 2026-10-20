"""Start XPAD Revolution on the PS3 with its libpad polling disabled, without modifying any file.

Why: on this console (4.92 + PS3HEN 3.4.0) XPAD froze the XMB after ~2 minutes. Its game-loader
polls cellPadGetData() on every pad port every 10 ms from its own thread inside VSH, alongside the
XMB's own pad reads. The SELECT+L3+R3 loader also works from XPAD's own USB data, so that polling
is not needed.

How: webMAN loads the unmodified plugin, then (before XPAD's 10 s startup delay ends) one branch in
its main loop is rewritten in VSH memory through webMAN's PS3MAPI:
  0x8a38  419e0010  bt cr7.eq, +0x10   ->  480002c4  b 0x8cfc   (skip the libpad poll block)
The 24 bytes around it are verified first; nothing is written if they differ.
"""
from __future__ import annotations

import re
import socket
import time
import urllib.parse
import urllib.request

from .ps3ftp import PLUGIN_LINE

VSH_PID = 0x01000300
MODULE_NAME = "XPADD"
PATCH_OFFSET = 0x8A38                        # in segment 0 of xpad_vsh_autodiscovery.sprx v1.0.0
CONTEXT_OFFSET = 0x8A30
CONTEXT = bytes.fromhex("e80900002fa00000419e00107c00f8502ba0270f409d02b8")
PATCHED = bytes.fromhex("480002c4")
CRLF = "\r\n"


def _http(host: str, path: str, timeout: float = 20) -> str:
    with urllib.request.urlopen(f"http://{host}{path}", timeout=timeout) as r:
        return r.read().decode("latin-1", "replace")


class PS3MAPIClient:
    """webMAN MOD's PS3MAPI: the TCP server on 7887 (FTP-like, '200 a|b|c' replies) when it is
    enabled, otherwise the same commands over HTTP (/ps3mapi.ps3?<command>, JSON reply)."""

    def __init__(self, host: str, port: int = 7887, timeout: float = 8):
        self.host, self.s = host, None
        try:
            self.s = socket.create_connection((host, port), timeout=2)
            self.s.settimeout(timeout)
            self.f = self.s.makefile("rb")
            self._read_reply()
        except OSError:
            self.s = None  # fall back to HTTP

    def _read_reply(self) -> str:
        while True:
            line = self.f.readline().decode("latin-1").rstrip(CRLF)
            if not line:
                raise ConnectionError("PS3MAPI closed the connection")
            # The server greets twice: "220 OK: PS3 Manager API Server v1." on connect and then
            # "230 OK: Connected to PS3 Manager API Server."; the second would otherwise be read
            # as the reply to the first command and every reply would lag by one.
            if line.startswith("230 OK: Connected"):
                continue
            if re.match(r"^\d{3} ", line):
                return line

    def cmd(self, line: str) -> str:
        if self.s is None:
            body = _http(self.host, "/ps3mapi.ps3?" + urllib.parse.quote(line, safe="/"))
            m = re.search(r'"response":\s*(.*)\}\s*$', body, re.S)
            if not m:
                raise RuntimeError(f"PS3MAPI {line!r}: {body[:200]}")
            return m.group(1).strip()
        self.s.sendall((line + CRLF).encode("latin-1"))
        reply = self._read_reply()
        if not reply.startswith("2"):
            raise RuntimeError(f"PS3MAPI {line!r}: {reply}")
        return reply[4:]

    def module_ids(self, pid: int = VSH_PID) -> list[int]:
        r = self.cmd(f"MODULE GETALLPRXID {pid}")
        if "0x" in r:  # HTTP mode prints ids as 0x%x with no separator between them
            ids = [int(x, 16) for x in re.findall(r"0x([0-9A-Fa-f]{1,8})", r)]
        else:          # TCP mode: decimal ids separated by |
            ids = [int(x) for x in r.split("|") if x.strip().isdigit()]
        return [i for i in dict.fromkeys(ids) if i]

    def module_name(self, prx_id: int, pid: int = VSH_PID) -> str:
        return self.cmd(f"MODULE GETNAME {pid} {prx_id}").strip().strip('"')

    def segments(self, prx_id: int, pid: int = VSH_PID) -> list[dict]:
        r = self.cmd(f"MODULE GETSEGMENTS {pid} {prx_id}")
        sep = r'"?[|,]\s*(?:0x)?'
        pat = r"seg(\d)" + sep + r"([0-9A-Fa-f]{16})" + sep + r"([0-9A-Fa-f]{16})" + sep + r"([0-9A-Fa-f]{16})"
        return [{"index": int(m[0]), "base": int(m[1], 16), "filesz": int(m[2], 16), "memsz": int(m[3], 16)}
                for m in re.findall(pat, r)]

    def close(self) -> None:
        if self.s is None:
            return
        try:
            self.s.sendall(b"DISCONNECT" + CRLF.encode())
        except OSError:
            pass
        self.s.close()


def read_mem(host: str, addr: int, length: int, pid: int = VSH_PID) -> bytes:
    """Bytes at addr from webMAN's /getmem.ps3mapi page (its hidden <textarea id="output"> holds
    exactly `len` bytes as hex). Not /ps3mapi.ps3?MEMORY GET: its HTTP hex conversion drops the
    high nibble ((b & 0xF0) >> 8) in webMAN 1.47."""
    body = _http(host, f"/getmem.ps3mapi?proc=0x{pid:x}&addr={addr:x}&len={length}")
    m = re.search(r'<textarea id="output"[^>]*>([0-9A-Fa-f]*)</textarea>', body)
    if not m:
        raise RuntimeError(f"getmem failed at 0x{addr:x}")
    return bytes.fromhex(m.group(1))[:length]


def write_mem(host: str, addr: int, data: bytes, pid: int = VSH_PID) -> None:
    _http(host, f"/getmem.ps3mapi?proc=0x{pid:x}&addr={addr:x}&val={data.hex()}")


def find_xpad(api: PS3MAPIClient) -> int | None:
    for prx in api.module_ids():
        try:
            if api.module_name(prx).upper() == MODULE_NAME:
                return prx
        except RuntimeError:
            continue
    return None


def xpad_state(host: str) -> dict:
    """Read-only: is XPAD loaded in VSH right now, and is its libpad polling disabled?"""
    api = PS3MAPIClient(host)
    try:
        prx = find_xpad(api)
        if prx is None:
            return {"loaded": False, "patched": False}
        base = api.segments(prx)[0]["base"]
        word = read_mem(host, base + PATCH_OFFSET, 4)
        return {"loaded": True, "patched": word == PATCHED, "prx_id": prx, "base": base, "word": word.hex()}
    finally:
        api.close()


def start_safe(host: str, log=print, load: bool = True) -> dict:
    """Load XPAD through webMAN and disable its libpad polling in memory. Returns a report."""
    api = PS3MAPIClient(host)
    log(f"PS3MAPI via {'TCP 7887' if api.s else 'HTTP'}")
    try:
        prx = find_xpad(api)
        if prx is None and load:
            # /loadprx_ps3 is not in webMAN 1.47.48 (HTTP 400). load_slot=0 = first free VSH plugin
            # slot (1..6, after the boot plugins); a busy or missing slot fails silently, so check after.
            _http(host, f"/vshplugin.ps3mapi?load_slot=0&prx={PLUGIN_LINE}")
            log("asked webMAN to load XPAD")
            for _ in range(50):
                prx = find_xpad(api)
                if prx is not None:
                    break
                time.sleep(0.1)
        if prx is None:
            raise RuntimeError("XPAD did not appear in VSH: the files are missing (run `8bitdo-ps3 ps3 install`) "
                               "or all 6 VSH plugin slots are in use (remove a boot plugin)")
        base = api.segments(prx)[0]["base"]
        ctx = read_mem(host, base + CONTEXT_OFFSET, len(CONTEXT))
        if ctx[:8] == CONTEXT[:8] and ctx[12:] == CONTEXT[12:] and ctx[8:12] == PATCHED:
            log(f"already patched (module 0x{prx:x}, base 0x{base:x})")
            return {"prx_id": prx, "base": base, "patched": True, "already": True}
        if ctx != CONTEXT:
            raise RuntimeError(f"code at 0x{base + CONTEXT_OFFSET:x} is {ctx.hex()}, expected {CONTEXT.hex()}; "
                               "different XPAD build, NOT patching")
        write_mem(host, base + PATCH_OFFSET, PATCHED)
        back = read_mem(host, base + PATCH_OFFSET, 4)
        ok = back == PATCHED
        log(f"libpad polling {'disabled' if ok else 'NOT disabled'} at 0x{base + PATCH_OFFSET:x} "
            f"(module 0x{prx:x}, segment base 0x{base:x}, read back {back.hex()})")
        return {"prx_id": prx, "base": base, "patched": ok, "readback": back.hex()}
    finally:
        api.close()
