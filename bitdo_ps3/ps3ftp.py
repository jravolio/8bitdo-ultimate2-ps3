"""Install / uninstall the XPAD Revolution files on a HEN PS3 over FTP (webMAN MOD's FTP server,
anonymous login), and fetch + interpret the plugin's own log.

What is written on the console:
  /dev_hdd0/plugins/ps3xpad/        the five plugin files (old copies backed up locally first)
Cleaned up if found (never added): an XPAD line in boot_plugins.txt, and the `# >>> 8bitdo-ps3`
block an early version of this tool put in /dev_hdd0/autoexec.bat. The plugin is not started from
either place: it is started at runtime by `8bitdo-ps3 ps3 start` (see ps3mapi.py).
"""
from __future__ import annotations

import ftplib
import hashlib
import io
import json
import socket
import time
from pathlib import Path

from . import BACKUPS, CAPTURES, PS3_PAYLOAD, ROOT
from .xpad_files import PAYLOAD_FILES, SIZES

PLUGIN_DIR = "/dev_hdd0/plugins/ps3xpad"
PLUGIN_LINE = f"{PLUGIN_DIR}/xpad_vsh_autodiscovery.sprx"
BOOT_PLUGINS = "/dev_hdd0/boot_plugins.txt"
# HEN reads the first of these that exists (payload/modulespatch.c)
BOOT_PLUGINS_SEARCH = ["/dev_usb000/boot_plugins.txt", "/dev_usb001/boot_plugins.txt", BOOT_PLUGINS]
# Loading XPAD from boot_plugins.txt froze PS3HEN 3.4.0 (firmware 4.92) during "Enable HEN".
AUTOEXEC = "/dev_hdd0/autoexec.bat"
DISABLE_FLAG = PLUGIN_DIR + "/disabled"
AUTOEXEC_BEGIN = "# >>> 8bitdo-ps3"
AUTOEXEC_END = "# <<< 8bitdo-ps3"
# On-console auto-start (the PC-free mode). Its markers deliberately do not match AUTOEXEC_BEGIN, so
# `remove_old_autostart()` (which strips the old, broken /loadprx_ps3 block) keeps this one.
AUTORUN_BEGIN = "# >>> 8bitdo ps3 autorun"
AUTORUN_END = "# <<< 8bitdo ps3 autorun"
# webMAN MOD's /setmem.ps3mapi searches process memory (`find=`) upward from `addr` and STOPS at the
# first unmapped page, and the VSH process address space is fragmented, so one wide search cannot
# cross a gap. The plugin's segment base moves from boot to boot (~0xAF0000 seen, ~0x2F10000 seen),
# so we search the big low region in one request, then sweep the higher range in windows. Each
# command gets its own line: webMAN only splits ';' when a line starts with a letter, and these
# start with '/'.
AUTORUN_LOW = (0x100000, 0x8D0000)          # the VSH executable + main heap: one contiguous region
AUTORUN_TILE_LO, AUTORUN_TILE_HI, AUTORUN_TILE_STEP = 0x8D0000, 0x3000000, 0x20000
AUTORUN_ISLANDS = [0xB30000, 0x1750000, 0x2B50000]  # 64KB islands, too small to be caught by tiling
AUTORUN_LOG = "/dev_hdd0/tmp/8bitdo_ps3_autorun.log"
CONFIG = ROOT / "config.json"


def _autorun_find(lo: int, hi: int, find: str, offset: int, val: str, pid: int) -> str:
    return (f"/setmem.ps3mapi?proc=0x{pid:x}&addr=0x{lo:x}&stop=0x{hi:x}"
            f"&find={find}&offset={offset}&val={val}")


def autorun_block() -> bytes:
    """The autoexec.bat block that starts XPAD and applies the freeze fix on the console itself."""
    from .ps3mapi import CONTEXT, CONTEXT_OFFSET, PATCHED, PATCH_OFFSET, VSH_PID
    # The freeze-fix branch sits 4 bytes into the block we search for (the context starts at
    # CONTEXT_OFFSET; 0x8A30..0x8A34 is e8090000 2fa00000, so the unique needle starts at +4).
    find = CONTEXT[4:].hex()
    offset = PATCH_OFFSET - (CONTEXT_OFFSET + 4)
    val = PATCHED.hex()

    cmds = [_autorun_find(*AUTORUN_LOW, find, offset, val, VSH_PID)]
    lo = AUTORUN_TILE_LO
    while lo < AUTORUN_TILE_HI:
        cmds.append(_autorun_find(lo, lo + AUTORUN_TILE_STEP, find, offset, val, VSH_PID))
        lo += AUTORUN_TILE_STEP
    for base in AUTORUN_ISLANDS:
        cmds.append(_autorun_find(base, base + AUTORUN_TILE_STEP, find, offset, val, VSH_PID))

    lines = [
        AUTORUN_BEGIN,
        "# Start XPAD Revolution after each \"Enable HEN\" and apply the freeze fix in memory,",
        "# on the console itself, without a PC. Managed by `8bitdo-ps3 ps3 autorun`.",
        f"logfile {AUTORUN_LOG}",
        "log Debug",   # trace every line of this boot to the log file (support + diagnosis)
        "wait xmb",    # webMAN starts before the XMB at boot; the plugin can only load once it is up
        "wait 3",
        f"/vshplugin.ps3mapi?load_slot=0&prx={PLUGIN_LINE}",
        "wait 2",
    ]
    lines += cmds
    lines += [AUTORUN_END, "", ""]  # the blank line at the end is required by webMAN's script parser
    return "\r\n".join(lines).encode("latin-1")


def load_config() -> dict:
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {}


def save_config(**kw) -> None:
    cfg = load_config()
    cfg.update(kw)
    CONFIG.write_text(json.dumps(cfg, indent=1))


def discover(subnet: str | None = None, timeout: float = 0.4) -> list[tuple[str, str]]:
    """Find PS3 FTP servers on the local /24 (banner check, nothing is logged into)."""
    import concurrent.futures as cf

    if subnet is None:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("192.0.2.1", 9))
            subnet = s.getsockname()[0].rsplit(".", 1)[0]
        finally:
            s.close()

    def probe(ip):
        try:
            with socket.create_connection((ip, 21), timeout=timeout) as c:
                c.settimeout(1.5)
                banner = c.recv(256).decode(errors="replace").strip()
                return ip, banner
        except OSError:
            return None

    with cf.ThreadPoolExecutor(64) as ex:
        found = [r for r in ex.map(probe, [f"{subnet}.{i}" for i in range(1, 255)]) if r]
    return [(ip, b) for ip, b in found if any(k in b.lower() for k in ("ps3", "webman", "multiman", "hen", "cobra", "irisman", "rebug"))] or found


class PS3:
    def __init__(self, host: str, timeout: float = 10):
        self.host = host
        self.ftp = ftplib.FTP(encoding="latin-1")  # webMAN status lines contain non-UTF-8 bytes (e.g. the degree sign)
        self.ftp.connect(host, 21, timeout=timeout)
        self.banner = self.ftp.welcome
        self.ftp.login()  # anonymous, like webMAN MOD expects
        self.ftp.set_pasv(True)

    def close(self) -> None:
        try:
            self.ftp.quit()
        except Exception:
            self.ftp.close()

    # -- primitives -----------------------------------------------------------------------
    def read(self, path: str) -> bytes | None:
        buf = io.BytesIO()
        try:
            self.ftp.retrbinary(f"RETR {path}", buf.write)
        except ftplib.error_perm:
            return None
        return buf.getvalue()

    def write(self, path: str, data: bytes) -> None:
        self.ftp.storbinary(f"STOR {path}", io.BytesIO(data))

    def exists(self, path: str) -> bool:
        return self.read(path) is not None if path.endswith(".txt") else self._size(path) is not None

    def _size(self, path: str) -> int | None:
        try:
            self.ftp.voidcmd("TYPE I")
            return self.ftp.size(path)
        except ftplib.error_perm:
            return None

    def mkdirs(self, path: str) -> None:
        cur = ""
        for part in path.strip("/").split("/"):
            cur += "/" + part
            try:
                self.ftp.mkd(cur)
            except ftplib.error_perm:
                pass

    # -- boot_plugins.txt -----------------------------------------------------------------
    def mounted(self) -> set[str]:
        try:
            return {n.strip("/").split("/")[-1] for n in self.ftp.nlst("/")}
        except ftplib.all_errors:
            return {"dev_hdd0"}

    def active_boot_plugins(self) -> tuple[str, list[str]]:
        devices = self.mounted()
        for p in BOOT_PLUGINS_SEARCH:
            if p.split("/")[1] not in devices:
                continue  # asking webMAN for a file on an unmounted device can hang the session
            data = self.read(p)
            if data is not None:
                return p, [ln.strip() for ln in data.decode(errors="replace").splitlines() if ln.strip()]
        return BOOT_PLUGINS, []

    def remove_from_boot_plugins(self, log=print) -> list[str]:
        """Drop the XPAD line from every boot_plugins.txt HEN may read (a USB stick's one too).
        Other lines and the line endings are kept; each file is backed up locally and on the PS3."""
        devices, changed = self.mounted(), []
        for path in BOOT_PLUGINS_SEARCH:
            if path.split("/")[1] not in devices:
                continue  # asking webMAN for a file on an unmounted device can hang the session
            raw = self.read(path)
            if raw is None or PLUGIN_LINE.encode() not in raw:
                continue
            BACKUPS.mkdir(parents=True, exist_ok=True)
            (BACKUPS / time.strftime("boot_plugins_%Y%m%d_%H%M%S.txt")).write_bytes(raw)
            if self.read(path + ".8bitdo-backup") is None:
                self.write(path + ".8bitdo-backup", raw)
            keep = [ln for ln in raw.splitlines(keepends=True) if ln.strip() != PLUGIN_LINE.encode()]
            self.write(path, b"".join(keep))
            log(f"removed XPAD from {path} (loading it from there freezes PS3HEN)")
            changed.append(path)
        return changed

    # -- leftovers from earlier versions ---------------------------------------------------
    def remove_old_autostart(self, log=print) -> None:
        """Take XPAD out of boot_plugins.txt and drop our old autoexec.bat block, if present.
        Anything else in those files is kept byte for byte."""
        self.remove_from_boot_plugins(log=log)
        old = self.read(AUTOEXEC)
        if not old or AUTOEXEC_BEGIN.encode() not in old:
            return
        BACKUPS.mkdir(parents=True, exist_ok=True)
        (BACKUPS / time.strftime("autoexec_%Y%m%d_%H%M%S.bat")).write_bytes(old)
        head, rest = old.split(AUTOEXEC_BEGIN.encode(), 1)
        tail = rest.split(AUTOEXEC_END.encode(), 1)[1].lstrip(b"\r\n") if AUTOEXEC_END.encode() in rest else b""
        new = head + tail
        log("removing the old 8bitdo-ps3 block from /dev_hdd0/autoexec.bat")
        if new.strip():
            self.write(AUTOEXEC, new)
        else:
            self.ftp.delete(AUTOEXEC)
        try:
            self.ftp.delete(DISABLE_FLAG)
        except ftplib.error_perm:
            pass

    # -- on-console auto-start (PC-free) ---------------------------------------------------
    def _strip_autorun(self, data: bytes) -> bytes | None:
        """Return data with our autorun block removed, or None if it was not there."""
        if AUTORUN_BEGIN.encode() not in data:
            return None
        head, rest = data.split(AUTORUN_BEGIN.encode(), 1)
        tail = rest.split(AUTORUN_END.encode(), 1)[1] if AUTORUN_END.encode() in rest else b""
        return head.rstrip(b"\r\n") + (b"\r\n" + tail.lstrip(b"\r\n") if tail.strip() else b"")

    def autorun_state(self) -> dict:
        raw = self.read(AUTOEXEC) or b""
        return {"on_console_autostart": AUTORUN_BEGIN.encode() in raw}

    def autorun_install(self, log=print) -> None:
        """Put (or refresh) the autoexec.bat block that starts XPAD + the freeze fix on the console.
        Anything else already in autoexec.bat is kept byte for byte."""
        self.mkdirs(PLUGIN_DIR)
        old = self.read(AUTOEXEC) or b""
        base = self._strip_autorun(old)
        if base is None:
            base = old
        if base.strip():
            BACKUPS.mkdir(parents=True, exist_ok=True)
            (BACKUPS / time.strftime("autoexec_%Y%m%d_%H%M%S.bat")).write_bytes(old)
        new = (base.rstrip(b"\r\n") + b"\r\n" if base.strip() else b"") + autorun_block()
        self.write(AUTOEXEC, new)
        back = self.read(AUTOEXEC) or b""
        ok = AUTORUN_BEGIN.encode() in back and AUTORUN_END.encode() in back
        log(f"  {'ok ' if ok else 'BAD'} {AUTOEXEC} (on-console auto-start {'written' if ok else 'NOT written'})")
        if not ok:
            raise RuntimeError("could not write the on-console auto-start")

    def autorun_remove(self, log=print) -> bool:
        """Remove our autoexec.bat block. Returns True if it was present."""
        old = self.read(AUTOEXEC)
        if old is None:
            return False
        new = self._strip_autorun(old)
        if new is None:
            return False
        BACKUPS.mkdir(parents=True, exist_ok=True)
        (BACKUPS / time.strftime("autoexec_%Y%m%d_%H%M%S.bat")).write_bytes(old)
        if new.strip():
            self.write(AUTOEXEC, new)
        else:
            self.ftp.delete(AUTOEXEC)
        log("  removed the on-console auto-start from " + AUTOEXEC)
        return True

    # -- plugin files ---------------------------------------------------------------------
    def install(self, payload: Path = PS3_PAYLOAD, log=print) -> None:
        self.mkdirs(PLUGIN_DIR)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        for name in PAYLOAD_FILES:
            old = self.read(f"{PLUGIN_DIR}/{name}")
            if old is not None:
                dst = BACKUPS / f"ps3xpad_{stamp}"
                dst.mkdir(parents=True, exist_ok=True)
                (dst / name).write_bytes(old)
            data = (payload / name).read_bytes()
            self.write(f"{PLUGIN_DIR}/{name}", data)
            back = self.read(f"{PLUGIN_DIR}/{name}")
            ok = back is not None and hashlib.sha256(back).digest() == hashlib.sha256(data).digest()
            log(f"  {'ok ' if ok else 'BAD'} {PLUGIN_DIR}/{name} ({len(data)} bytes)")
            if not ok:
                raise RuntimeError(f"verification failed for {name}")

    def uninstall_files(self, log=print) -> None:
        for name in PAYLOAD_FILES:
            try:
                self.ftp.delete(f"{PLUGIN_DIR}/{name}")
                log(f"  removed {PLUGIN_DIR}/{name}")
            except ftplib.error_perm:
                pass

    def status(self) -> dict:
        path, lines = self.active_boot_plugins()
        files = {n: self._size(f"{PLUGIN_DIR}/{n}") for n in PAYLOAD_FILES}
        old = self.read(AUTOEXEC) or b""
        return {
            "host": self.host, "banner": self.banner, "boot_plugins_file": path, "boot_plugins": lines,
            "in_boot_plugins": PLUGIN_LINE in lines, "old_autoexec_block": AUTOEXEC_BEGIN.encode() in old,
            "on_console_autostart": AUTORUN_BEGIN.encode() in old,
            "files": files, "installed": all(files.values()),
            "files_match_pinned": all(files.get(n) == size for n, size in SIZES.items()),
        }

    def fetch_logs(self) -> Path:
        out = CAPTURES / time.strftime("ps3_logs_%Y%m%d_%H%M%S")
        out.mkdir(parents=True, exist_ok=True)
        for name in ("xpad.log", "xpad.log.1", "xpad.log.2"):
            data = self.read(f"{PLUGIN_DIR}/{name}")
            if data is not None:
                (out / name).write_bytes(data)
        return out


def explain_log(text: str) -> list[str]:
    """Turn XPAD's debug log into plain-language findings for this receiver."""
    msgs = []
    if not text:
        return ["no xpad.log yet: the plugin has not started since it was installed (run `8bitdo-ps3 ps3 start`)"]
    if "2DC8:310B" in text and "attach OK" in text:
        msgs.append("OK: the 8BitDo receiver attached in XInput mode (2DC8:310B)")
    if "first input report received from 2DC8:310B" in text:
        msgs.append("OK: input reports are flowing from the receiver")
    if "057E:2009" in text:
        msgs.append("PROBLEM: the receiver presented itself as a Switch Pro Controller (057E:2009) on the PS3; "
                    "XPAD only drives XInput. Unplug it, turn the controller OFF, plug the receiver back in, "
                    "then turn the controller on (Mode switch on 2.4G). If it persists, use the USB-C cable instead.")
    if "2DC8:3109" in text and "310B" not in text.rsplit("2DC8:3109", 1)[1]:  # the latest attach
        msgs.append("The receiver is idle (2DC8:3109): the controller is off or asleep. Press Home to wake it")
    if "LDD table full" in text and "2DC8:310B" in text.split("LDD table full", 1)[1].split("\n", 1)[0]:
        msgs.append("PROBLEM: the PS3 USB driver table filled up before the 8BitDo entry; remove other USB plugins")
    if "USB init failed" in text:
        msgs.append("PROBLEM: " + text.split("USB init failed", 1)[1].split("\n", 1)[0].strip())
    if "XPAD Rev v1.0.0 Loaded!" in text:
        msgs.append("OK: plugin loaded")
    # rumble: SELECT+L3+R3 in a running game loads xpad_game.sprx into it
    if "normalized USB combo accepted" in text:
        msgs.append("OK: SELECT+L3+R3 was recognised")
    if "hotkey pressed but no EBOOT process" in text:
        msgs.append("SELECT+L3+R3 was pressed outside a game: start the game first, then hold it ~1 s")
    tail = text.rstrip().splitlines()[-1].strip() if text.strip() else ""
    if "all hooks active" in tail:
        # the pinned XPAD build loads the game module, then hangs the console the moment it hooks in
        msgs.append("PROBLEM: the in-game rumble module activated its hooks and the console hung right there "
                    "(the log stops at this line). XPAD's game module is not safe on this PS3HEN build: restart "
                    "the PS3, then don't use SELECT+L3+R3.")
    elif "LOAD_PROC_MODULE returned OK" in text:
        msgs.append("OK: the rumble module was loaded into the game (rumble should work in that game now)")
    if "manual loader: load_module failed" in text:
        msgs.append("PROBLEM: the rumble module could not be loaded into the game: "
                    + text.split("manual loader: load_module failed", 1)[1].split("\n", 1)[0].strip())
    if "LOAD_PROC_MODULE stuck" in text:
        msgs.append("PROBLEM: loading the rumble module hung in the system (input keeps working; restart for rumble)")
    if "LED/rumble transfer submit failed" in text:
        msgs.append("PROBLEM: a rumble/LED command could not be sent to the receiver")
    return msgs or ["log present but no 8BitDo activity yet: plug the receiver into the PS3 and turn the controller on"]
