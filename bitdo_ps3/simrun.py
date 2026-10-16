"""Run the simulated PS3 (XPAD Revolution's real VSH plugin code on qemu-ppc) against a USB device
and grade what a game would see."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import CAPTURES, PS3_PAYLOAD, SIM_BINARY, SIM_BINARY_FIXED, SIM_DIR, SIM_ROOT, XPAD_REPO
from .bridge import Bridge
from .xinput import XState, decode_pad, grade, walkthrough

WSL_DISTRO = os.environ.get("BITDO_WSL_DISTRO", "Ubuntu")


def wsl_path(p: Path) -> str:
    p = Path(p).resolve()
    return "/mnt/" + p.drive[0].lower() + p.as_posix()[2:]


def _newest(paths) -> float:
    return max(Path(p).stat().st_mtime for p in paths)


def ensure_built(log=print) -> None:
    from .xpad_files import ensure_source
    ensure_source(log)
    sources = list((SIM_DIR / "shim").rglob("*.[ch]")) + [XPAD_REPO / "src" / "ps3" / "main.c", SIM_DIR / "build.sh"]
    built = [SIM_BINARY, SIM_BINARY_FIXED]
    if all(b.exists() for b in built) and min(b.stat().st_mtime for b in built) >= _newest(sources):
        return
    log("building the PS3 simulator (powerpc-linux-gnu-gcc in WSL)...")
    r = subprocess.run(["wsl.exe", "-d", WSL_DISTRO, "--", "bash", wsl_path(SIM_DIR / "build.sh")],
                       capture_output=True, text=True)
    if r.returncode != 0 or not all(b.exists() for b in built):
        raise RuntimeError("simulator build failed:\n" + r.stdout + r.stderr)


def prepare_root() -> Path:
    plug = SIM_ROOT / "dev_hdd0" / "plugins" / "ps3xpad"
    plug.mkdir(parents=True, exist_ok=True)
    src = PS3_PAYLOAD if (PS3_PAYLOAD / "xpad_devices.txt").exists() else XPAD_REPO / "plugin" / "ps3xpad"
    for name in ("xpad_devices.txt", "xpad_remap.txt", "xpad_analog.txt"):
        shutil.copyfile(src / name, plug / name)
    for f in plug.glob("xpad.log*"):
        f.unlink()
    for f in ("xpad_game.ready", "xpad_auto_inject.txt"):
        (plug / f).unlink(missing_ok=True)
    return plug


@dataclass
class SimConsole:
    device: object
    port: int = 0
    time_scale: float = 0.1
    native_ds3: bool = False
    freeze_fix: bool = True   # run the plugin as `ps3 start` leaves it on the console
    out_dir: Path | None = None
    log: callable = print
    events: list = field(default_factory=list)

    def start(self) -> "SimConsole":
        ensure_built(self.log)
        self.plugin_dir = prepare_root()
        self.out_dir = self.out_dir or CAPTURES / time.strftime("sim_%Y%m%d_%H%M%S")
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.bridge = Bridge(self.device, self.port, self.out_dir / "ps3_usb.pcap", log=self.log)
        env = [f"PS3SIM_ROOT={wsl_path(SIM_ROOT)}", f"PS3SIM_BRIDGE=127.0.0.1:{self.bridge.port}",
               f"PS3SIM_TIME_SCALE={self.time_scale}", f"PS3SIM_NATIVE_DS3={1 if self.native_ds3 else 0}"]
        self.proc = subprocess.Popen(["wsl.exe", "-d", WSL_DISTRO, "--", "env", *env, "qemu-ppc",
                                      wsl_path(SIM_BINARY_FIXED if self.freeze_fix else SIM_BINARY)],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1)
        self._cv = threading.Condition()
        self._raw = open(self.out_dir / "console_events.jsonl", "w", encoding="utf-8")
        threading.Thread(target=self._reader, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()
        return self

    def _reader(self) -> None:
        for line in self.proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            self._raw.write(line + "\n")
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            with self._cv:
                ev["_i"] = len(self.events)
                self.events.append(ev)
                self._cv.notify_all()
            if ev.get("ev") == "notify":
                self.log(f"  [PS3 notification] {ev['msg']}")

    def _stderr(self) -> None:
        for line in self.proc.stderr:
            if line.strip():
                self.log(f"  [qemu] {line.rstrip()}")

    def wait_for(self, pred, timeout: float, since: int = 0):
        deadline = time.monotonic() + timeout
        with self._cv:
            while True:
                for ev in self.events[since:]:
                    if pred(ev):
                        return ev
                left = deadline - time.monotonic()
                if left <= 0 or self.proc.poll() is not None:
                    return None
                self._cv.wait(left)

    def mark(self) -> int:
        with self._cv:
            return len(self.events)

    def command(self, line: str) -> None:
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()

    def stop(self) -> None:
        try:
            self.command("quit")
            self.proc.wait(timeout=15)
        except Exception:
            self.proc.kill()
        self.bridge.close()
        self._raw.close()
        for f in self.plugin_dir.glob("xpad.log*"):
            shutil.copyfile(f, self.out_dir / f.name)


class Report:
    def __init__(self):
        self.rows: list[dict] = []

    def add(self, name: str, ok: bool, detail: str = "", group: str = "") -> bool:
        self.rows.append({"group": group, "check": name, "ok": bool(ok), "detail": detail})
        return ok

    @property
    def ok(self) -> bool:
        return all(r["ok"] for r in self.rows)

    def summary(self) -> str:
        n = sum(r["ok"] for r in self.rows)
        return f"{n}/{len(self.rows)} checks passed"

    def write(self, out_dir: Path, extra: dict) -> None:
        (out_dir / "report.json").write_text(json.dumps({"ok": self.ok, "rows": self.rows, **extra}, indent=1))
        lines = [f"# PS3 simulator test: {'PASS' if self.ok else 'FAIL'} ({self.summary()})", ""]
        for k, v in extra.items():
            lines.append(f"- **{k}**: {v}")
        lines += ["", "| | check | detail |", "|---|---|---|"]
        for r in self.rows:
            lines.append(f"| {'PASS' if r['ok'] else 'FAIL'} | {r['check']} | {r['detail']} |")
        (out_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _pad_ev(port):
    return lambda e: e.get("ev") == "pad" and e.get("port") == port


def run_test(device=None, interactive: bool = False, log=print, native_ds3: bool = False,
             freeze_fix: bool = True) -> Report:
    """Full console scenario. With the VirtualReceiver it is fully automatic; with the real receiver
    (`interactive=True`) the user performs each input when prompted."""
    from .virtual_receiver import VirtualReceiver

    device = device or VirtualReceiver()
    virtual = isinstance(device, VirtualReceiver)
    rep = Report()
    con = SimConsole(device, native_ds3=native_ds3, freeze_fix=freeze_fix, log=log).start()
    extra = {"device": f"{device.vid:04x}:{device.pid:04x}", "artifacts": str(con.out_dir)}
    try:
        g = "boot"
        rep.add("simulator connected to USB bridge", con.bridge.connected.wait(30), group=g)
        ev = con.wait_for(lambda e: e.get("ev") == "notify" and "Loaded" in e.get("msg", ""), 60)
        rep.add("plugin boots and shows 'XPAD Rev v1.0.0 Loaded!'", ev is not None, ev and ev["msg"] or "", g)
        reg = con.wait_for(lambda e: e.get("ev") == "usbd_register" and e.get("vid") == "2dc8"
                           and e.get("pid") == f"{device.pid:04x}", 5)
        rep.add(f"USB driver registered for 2dc8:{device.pid:04x} within the retail ~22-slot LDD budget",
                reg is not None and reg.get("result") == "0x0", json.dumps(reg) if reg else "not registered", g)

        g = "attach"
        att = con.wait_for(lambda e: e.get("ev") == "usbd_attach", 10)
        rep.add("receiver probed + attached by the plugin", att is not None and att.get("result") == 0,
                json.dumps(att) if att else "no attach", g)
        ldd = con.wait_for(lambda e: e.get("ev") == "ldd_register", 5)
        port = ldd["port"] if ldd else -1
        rep.add("virtual pad registered with libpad (syscall 574)", ldd is not None,
                f"port {port + 1} (libpad index {port}), capability {ldd and ldd['capability']}", g)
        ins = con.wait_for(lambda e: e.get("ev") == "syscall" and e.get("num") == 573, 5)
        rep.add("pad data routed into games (syscall 573 insert mode ON)", ins is not None and ins.get("mode") == 1 and ins.get("ok"),
                json.dumps(ins) if ins else "", g)
        led = con.wait_for(lambda e: e.get("ev") == "usb_out" and e.get("data", "").startswith("0103"), 5)
        rep.add("player LED command sent to the receiver", led is not None,
                f"OUT {led['data']} (XInput LED pattern {int(led['data'][4:6], 16)})" if led else "", g)

        g = "input"
        if port < 0:
            raise RuntimeError("no virtual pad was registered; see console_events.jsonl")
        last_b = None
        for name, st in walkthrough():
            m = con.mark()
            if interactive:
                if name in ("release", "centre", "rest"):
                    log("   ...let go of everything")
                else:
                    log(f" >> {name}")
            else:
                device.set_state(st)
            deadline = time.monotonic() + (15 if interactive else 1.5)
            diffs = ["no pad data reached the game"]
            while time.monotonic() < deadline:
                ev = con.wait_for(_pad_ev(port), max(0.05, deadline - time.monotonic()), since=m)
                if ev is None:
                    if last_b is not None and not (diffs := grade(st, last_b)):
                        break
                    continue
                m = ev["_i"] + 1
                last_b = ev["b"]
                diffs = grade(st, last_b)
                if not diffs:
                    break
            rep.add(name, not diffs, "; ".join(diffs) if diffs else json.dumps(decode_pad(last_b)["buttons"]) +
                    f" L2={last_b[18]} R2={last_b[19]} LX={last_b[6]} LY={last_b[7]} RX={last_b[4]} RY={last_b[5]}", g)
        if last_b:
            rep.add("Sixaxis motion data", last_b[20:24] == [512, 512, 512, 512],
                    f"{last_b[20:24]} = neutral (XInput carries no motion; games see a still pad)", "limits")

        g = "game"
        con.command("game start BLUS30443_main_EBOOT.BIN")
        m = con.mark()
        if interactive:
            log(" >> hold SELECT + L3 + R3 (both stick clicks) for 1 second")
        else:
            device.set_state(XState({"BACK", "LS", "RS"}))
        ld = con.wait_for(lambda e: e.get("ev") == "ps3mapi_load_module", 15 if interactive else 4, since=m)
        rep.add("SELECT+L3+R3 loads the in-game module (rumble) into the running game", ld is not None,
                ld["path"] if ld else "loader did not fire", g)
        if not interactive:
            device.set_state(XState())
        m = con.mark()
        con.command(f"rumble {port} 200 100")
        rb = con.wait_for(lambda e: e.get("ev") == "usb_out" and e.get("data", "").startswith("0008"), 4, since=m)
        rep.add("game rumble reaches the controller motors", rb is not None and rb["data"][6:10] == "c864",
                f"OUT {rb['data']} (large=0xc8 small=0x64)" if rb else "no rumble packet", g)
        if virtual:
            t_end = time.monotonic() + 2
            while time.monotonic() < t_end and not any(d.hex().startswith("000800c864") for _, _, d in device.out_log):
                time.sleep(0.05)
            outs = [d.hex() for _, ep, d in device.out_log]
            rep.add("receiver actually received the rumble packet", any(o.startswith("000800c864") for o in outs),
                    f"{len(outs)} OUT packets", g)

        plog = (con.plugin_dir / "xpad.log").read_text(errors="replace") if (con.plugin_dir / "xpad.log").exists() else ""
        if freeze_fix:
            rep.add("freeze fix: the loader fired from XPAD's own USB data", "normalized USB combo accepted" in plog,
                    "xpad.log: 'manual loader: normalized USB combo accepted'", g)
            rep.add("freeze fix: XPAD never polls libpad from VSH (the cause of the XMB freeze)",
                    "libpad monitoring ready" not in plog and "libpad init attempt" not in plog,
                    "no 'manual loader: libpad ...' lines in xpad.log, as on the real console", g)

        g = "hotplug"
        m = con.mark()
        con.bridge.unplug()
        un = con.wait_for(lambda e: e.get("ev") == "ldd_unregister", 5, since=m)
        rep.add("unplugging removes the virtual pad", un is not None, "", g)
        m = con.mark()
        con.bridge.plug()
        re_ = con.wait_for(lambda e: e.get("ev") == "ldd_register", 8, since=m)
        rep.add("re-plugging brings the pad back", re_ is not None, f"port {re_['port'] + 1}" if re_ else "", g)

        rates = [e["inserts_per_s"] for e in con.events if e.get("ev") == "pad_rate"]
        extra.update({
            "device": f"{device.vid:04x}:{device.pid:04x} ({'virtual' if virtual else 'real'}, descriptors {device.descriptor_source})",
            "plugin source": "refs/XPAD-Revolution/src/ps3/main.c on qemu-ppc, 32-bit big-endian, "
                             + ("with the freeze fix (as after `ps3 start`)" if freeze_fix else "unmodified"),
            "pad updates/s seen by the game thread (avg)": round(sum(rates) / len(rates), 1) if rates else "n/a",
            "USB OUT/IN transfers": f"{con.bridge.stats['out']}/{con.bridge.stats['in']}",
        })
    except RuntimeError as exc:
        rep.add("scenario aborted", False, str(exc), "abort")
    finally:
        con.stop()
    rep.write(con.out_dir, extra)
    rep.out_dir = con.out_dir
    return rep
