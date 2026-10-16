"""8bitdo-ps3 command line.

  8bitdo-ps3 status                  receiver / PC / DualShock 3 / PS3 at a glance
  8bitdo-ps3 use ps3|pc              switch: get the PS3 side running, or check the PC side
  8bitdo-ps3 ps3 find                find the PS3 on the local network (webMAN MOD's FTP server)
  8bitdo-ps3 ps3 install             download + verify XPAD Revolution, copy it to the PS3
  8bitdo-ps3 ps3 start               start it now (after every "Enable HEN"), freeze fix applied
  8bitdo-ps3 ps3 watch               keep running: does `ps3 start` by itself after every boot
  8bitdo-ps3 ps3 status|log|live|uninstall|restore
  8bitdo-ps3 recovery-stick          make a USB stick that boots HEN with only webMAN
  8bitdo-ps3 pc-test                 live view of what Windows games receive + rumble pulse
  8bitdo-ps3 test [--real]           run the simulated PS3 (the plugin's real code under qemu-ppc)
  8bitdo-ps3 capture-descriptors     save the real receiver's USB descriptors for the simulator
"""
from __future__ import annotations

import argparse
import json
import socket
import time

from . import CAPTURES, __version__


def _p(*a):
    print(*a, flush=True)


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except OSError:
        return False


def _ftp_up(host: str) -> bool:
    return _port_open(host, 21)


def _save_capture(prefix: str, data) -> None:
    CAPTURES.mkdir(exist_ok=True)
    (CAPTURES / time.strftime(f"{prefix}_%Y%m%d_%H%M%S.json")).write_text(json.dumps(data, indent=1))


# ---- status -----------------------------------------------------------------------------------

def cmd_status(args) -> int:
    from .receiver import ds3_status, receiver_state, xinput_slots
    st = receiver_state()
    _p("8BitDo receiver (this PC)")
    if st.state == "absent":
        _p("  not plugged into this PC")
    elif st.state == "idle":
        _p(f"  plugged in, controller NOT linked (2DC8:3109 idle). Firmware {st.firmware or '?'}, {st.product or ''}")
        _p("  -> turn the controller on: press Home")
    elif st.state == "xinput":
        _p("  linked, XInput mode (2DC8:310B) = what both the PC and the PS3 plugin use"
           + (f". Firmware {st.firmware}" if st.firmware else ""))
    elif st.state == "switch":
        _p("  presenting as a Switch Pro Controller (057E:2009): replug the receiver with the controller off")
    _p("PC (Windows XInput)")
    slots = xinput_slots()
    _p("  " + (", ".join(f"slot {i + 1} connected" for i in slots) if slots else "no XInput controller connected"))
    _p("DualShock 3 (this PC)")
    _p("  " + ds3_status())
    from .ps3ftp import PS3, load_config
    host = args.host or load_config().get("ps3_host")
    _p("PS3")
    if not host:
        _p("  no PS3 address known yet: run `8bitdo-ps3 ps3 find` with the PS3 on and HEN enabled")
        return 0
    try:
        ps3 = PS3(host, timeout=4)
        s = ps3.status()
        ps3.close()
    except OSError as exc:
        _p(f"  {host} not reachable ({exc.__class__.__name__}): is the PS3 on with HEN enabled?")
        return 0
    _p(f"  {host}: XPAD files {'installed' if s['installed'] and s['files_match_pinned'] else 'NOT installed'}")
    try:
        from .ps3mapi import xpad_state
        x = xpad_state(host)
        _p("  XPAD " + ("running, freeze fix applied" if x["patched"] else
                        "running WITHOUT the freeze fix (run `8bitdo-ps3 ps3 start`)" if x["loaded"] else
                        "not running (run `8bitdo-ps3 ps3 start`)"))
    except Exception as exc:
        _p(f"  could not ask webMAN whether XPAD is running ({exc.__class__.__name__})")
    if s["in_boot_plugins"] or s["old_autoexec_block"]:
        _p("  leftover auto-start entry found: run `8bitdo-ps3 ps3 install` to clean it up")
    return 0


# ---- PS3 --------------------------------------------------------------------------------------

def _host(args) -> str:
    from .ps3ftp import discover, load_config, save_config
    if args.host:
        save_config(ps3_host=args.host)
        return args.host
    host = load_config().get("ps3_host")
    if host and _ftp_up(host):
        return host
    _p("looking for the PS3 on the local network (FTP port 21)...")
    found = discover()
    if not found:
        raise SystemExit("PS3 not found. Turn it on, enable HEN, check that webMAN MOD is running, "
                         "or pass --host <PS3 IP address>")
    host, banner = found[0]
    _p(f"found {host}: {banner}")
    save_config(ps3_host=host)
    return host


NEXT_STEPS_PS3 = """
Every time you turn the PS3 on:
  1. Enable HEN as usual and wait until the XMB is back (webMAN shows its welcome message).
  2. On this PC run `8bitdo-ps3 ps3 start` (or keep `8bitdo-ps3 ps3 watch` running and it does
     it for you). About 10 s later the PS3 shows "XPAD Rev v1.0.0 Loaded!".
  3. Plug the 8BitDo receiver into a PS3 USB port and turn the controller on (Home).
     It becomes the next free controller (turn the DualShock 3 off first to make it player 1).
  4. Rumble in games: hold SELECT + L3 + R3 for about 1 s once the game is running.
If something is off, `8bitdo-ps3 ps3 log` explains what the plugin saw."""


def cmd_install(host: str) -> None:
    from .ps3ftp import PS3
    from .xpad_files import XPAD_COMMIT, fetch
    _p(f"XPAD Revolution @ {XPAD_COMMIT[:7]}: checking the files (SHA-256 pinned)")
    payload = fetch(log=_p)
    ps3 = PS3(host)
    try:
        _p(f"copying to {host}:")
        ps3.install(payload, log=_p)
        ps3.remove_old_autostart(log=_p)
    finally:
        ps3.close()
    _p("installed. Nothing was added to boot_plugins.txt: the plugin only runs when started from this PC.")


def _installed(host: str) -> bool:
    from .ps3ftp import PS3
    ps3 = PS3(host, timeout=8)
    try:
        s = ps3.status()
        return s["installed"] and s["files_match_pinned"]
    finally:
        ps3.close()


def cmd_start(host: str) -> int:
    from .ps3mapi import start_safe
    if not _installed(host):
        _p("XPAD is not installed on the PS3 yet: run `8bitdo-ps3 ps3 install` first")
        return 2
    report = start_safe(host, log=_p)
    _save_capture("ps3_start", report)
    if report.get("patched"):
        _p("XPAD is running with the freeze fix. Plug the receiver into the PS3 and turn the controller on.")
        return 0
    _p("the freeze fix could NOT be applied: restart the PS3 before using it (see README, Troubleshooting)")
    return 1


def cmd_watch(args) -> int:
    """Start XPAD (safely) every time the PS3 comes up with HEN enabled, until stopped."""
    from .ps3ftp import discover, load_config, save_config
    from .ps3mapi import start_safe, xpad_state
    host = args.host or load_config().get("ps3_host")
    end = time.monotonic() + args.minutes * 60 if args.minutes > 0 else None
    _p("watching for the PS3; XPAD is started each time HEN is enabled. Ctrl+C to stop.")
    state, up_since, last_scan = "down", None, 0.0
    try:
        while end is None or time.monotonic() < end:
            if not host or not _port_open(host, 80):
                if state != "down":
                    _p(f"[{time.strftime('%H:%M:%S')}] PS3 went away (restart / power off / HEN not enabled yet)")
                state, up_since = "down", None
                if not args.host and time.monotonic() - last_scan > 30:  # DHCP may give it a new address
                    last_scan = time.monotonic()
                    found = discover(timeout=0.3)
                    if found and found[0][0] != host:
                        host = found[0][0]
                        save_config(ps3_host=host)
                        _p(f"[{time.strftime('%H:%M:%S')}] PS3 found at {host}")
                time.sleep(3)
                continue
            if up_since is None:
                up_since = time.monotonic()
                _p(f"[{time.strftime('%H:%M:%S')}] webMAN is up on {host} (HEN enabled)")
            if state in ("running", "failed"):
                time.sleep(3)
                continue
            if time.monotonic() - up_since < args.settle:  # let HEN / the XMB finish starting
                time.sleep(1)
                continue
            try:
                x = xpad_state(host)
                if x["patched"]:
                    _p(f"[{time.strftime('%H:%M:%S')}] XPAD already running with the freeze fix")
                    state = "running"
                    continue
                if not _installed(host):
                    _p("XPAD is not installed on this PS3: run `8bitdo-ps3 ps3 install`, then start watch again")
                    return 2
                report = start_safe(host, log=_p)
                _save_capture("ps3_start", report)
                state = "running" if report.get("patched") else "failed"
                _p(f"[{time.strftime('%H:%M:%S')}] " + ("XPAD running: plug in the receiver and turn the controller on"
                                                        if state == "running" else "freeze fix NOT applied: restart the PS3"))
            except Exception as exc:
                _p(f"[{time.strftime('%H:%M:%S')}] start failed: {exc}")
                state = "failed"
    except KeyboardInterrupt:
        _p("stopped")
    return 0


def cmd_restore(args) -> int:
    """Take XPAD out of every boot_plugins.txt (and our old autoexec block) as soon as the PS3's FTP
    answers. With --wait it polls fast, to catch the few seconds webMAN is up before a boot plugin
    can stall the console."""
    from .ps3ftp import PS3, discover, load_config
    hosts = [h for h in [args.host, load_config().get("ps3_host")] if h]
    end = time.monotonic() + args.minutes * 60
    last_scan = 0.0
    while time.monotonic() < end:
        for host in list(dict.fromkeys(hosts)):
            if not _ftp_up(host):
                continue
            try:
                ps3 = PS3(host, timeout=5)
                ps3.remove_old_autostart(log=_p)
                ok = not ps3.status()["in_boot_plugins"]
                ps3.close()
                _p(f"[{time.strftime('%H:%M:%S')}] {host}: " + ("no XPAD auto-start left; restart the PS3 normally"
                                                               if ok else "XPAD is still listed in boot_plugins.txt"))
                if ok:
                    return 0
            except Exception as exc:
                _p(f"[{time.strftime('%H:%M:%S')}] {host}: FTP up but {exc.__class__.__name__}: {exc}")
        if not args.wait:
            break
        if time.monotonic() - last_scan > 20:
            last_scan = time.monotonic()
            hosts += [ip for ip, _ in discover(timeout=0.3)]
        time.sleep(0.5)
    _p("PS3 FTP not reachable; nothing changed")
    return 1


RECOVERY_LINE = b"/dev_hdd0/plugins/webftp_server.sprx\r\n"


def cmd_recovery_stick(args) -> int:
    """Wait for a USB stick on this PC and put a boot_plugins.txt on it that loads only webMAN.
    HEN reads /dev_usb000 (then usb001) before /dev_hdd0, so booting with the stick in skips every
    other boot plugin while keeping webMAN's FTP for the remote fix. Nothing on the stick is deleted."""
    import subprocess
    from pathlib import Path
    ps = ("Get-Volume | Where-Object { $_.DriveType -eq 'Removable' -and $_.DriveLetter } | "
          "ForEach-Object { \"$($_.DriveLetter)|$($_.FileSystem)|$($_.FileSystemLabel)\" }")
    end = time.monotonic() + args.minutes * 60
    seen_msg = False
    while time.monotonic() < end:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True).stdout
        vols = [ln.strip().split("|") for ln in out.splitlines() if ln.strip()]
        if vols:
            letter, fs, label = vols[0][0], vols[0][1], vols[0][2] if len(vols[0]) > 2 else ""
            root = f"{letter}:\\"
            if fs.upper() != "FAT32":
                _p(f"USB stick {root} ({label}) is {fs}; the PS3 only reads FAT32 sticks. Use a FAT32 stick.")
                return 2
            target = Path(root) / "boot_plugins.txt"
            if target.exists():
                bak = Path(root) / "boot_plugins.txt.bak"
                if not bak.exists():
                    bak.write_bytes(target.read_bytes())
                _p(f"existing {target} kept as {bak}")
            target.write_bytes(RECOVERY_LINE)
            ok = target.read_bytes() == RECOVERY_LINE
            _p(f"recovery stick ready on {root} ({label}, {fs}): boot_plugins.txt loads only webMAN" if ok else "write failed")
            return 0 if ok else 1
        if not seen_msg:
            _p("waiting for a USB stick to be plugged into this PC...")
            seen_msg = True
        time.sleep(1)
    return 1


def cmd_ps3(args) -> int:
    from .ps3ftp import PS3, discover, explain_log, save_config
    if args.action == "restore":
        return cmd_restore(args)
    if args.action == "live":
        return cmd_live(args)
    if args.action == "watch":
        return cmd_watch(args)
    if args.action == "find":
        found = discover()
        for ip, banner in found:
            _p(f"{ip}: {banner}")
        if not found:
            _p("no PS3 FTP server found: turn the PS3 on, enable HEN, check webMAN MOD is loaded (or pass --host)")
        else:
            save_config(ps3_host=found[0][0])
        return 0 if found else 1
    host = _host(args)
    if args.action == "install":
        cmd_install(host)
        _p(NEXT_STEPS_PS3)
        return 0
    if args.action == "start":
        return cmd_start(host)
    ps3 = PS3(host)
    try:
        if args.action == "uninstall":
            ps3.remove_old_autostart(log=_p)
            ps3.uninstall_files(log=_p)
            _p("uninstalled (a copy that is running right now stays until the PS3 restarts)")
        elif args.action == "status":
            s = ps3.status()
            try:
                from .ps3mapi import xpad_state
                s["xpad"] = xpad_state(host)
            except Exception as exc:
                s["xpad"] = f"unknown ({exc.__class__.__name__})"
            _p(json.dumps(s, indent=1))
        elif args.action == "log":
            out = ps3.fetch_logs()
            text = (out / "xpad.log").read_text(errors="replace") if (out / "xpad.log").exists() else ""
            for m in explain_log(text):
                _p(" - " + m)
            _p(f"(full logs saved to {out})")
    finally:
        ps3.close()
    return 0


# ---- use ps3 / use pc -------------------------------------------------------------------------

def cmd_use(args) -> int:
    from .receiver import receiver_state, xinput_slots
    _p("The receiver works the same way on both (XInput, 2DC8:310B); nothing on it is changed.")
    if args.target == "ps3":
        try:
            host = _host(args)
            if not _installed(host):
                cmd_install(host)
            rc = cmd_start(host)
        except (OSError, SystemExit) as exc:
            _p(f"PS3 not reachable right now ({exc}).")
            _p("Turn it on, enable HEN, then run: 8bitdo-ps3 use ps3   (or keep `8bitdo-ps3 ps3 watch` running)")
            return 1
        if rc == 0:
            _p("Now plug the receiver into the PS3. To go back to the PC, just plug it into the PC.")
        return rc
    st = receiver_state()
    if st.state == "xinput":
        slots = xinput_slots()
        _p(f"PC ready: Windows sees the 8BitDo as an Xbox 360 controller ({', '.join(f'slot {i + 1}' for i in slots) or 'no slot?'}).")
        return 0
    if st.state == "idle":
        _p("Receiver plugged into the PC but the controller is off or not linked: press Home on the controller.")
    else:
        _p("Plug the receiver into this PC (or connect the controller with its USB-C cable).")
    _p("Nothing needs to be turned off on the PS3: the plugin only runs when you start it.")
    return 1


# ---- simulator ---------------------------------------------------------------------------------

def cmd_test(args) -> int:
    from .simrun import run_test
    device = None
    if args.real:
        from . import PID_RECEIVER_XINPUT, VID_8BITDO
        from .bridge import RealUsbDevice
        from .receiver import receiver_state, wait_for
        if receiver_state().state != "xinput":
            _p("Turn the controller on (press Home)... waiting up to 60 s")
            if wait_for("xinput", 60).state != "xinput":
                _p("receiver not linked; aborting")
                return 2
        _p("The receiver is handed to the simulated PS3 via UsbDk; Windows gets it back afterwards.")
        device = RealUsbDevice(VID_8BITDO, PID_RECEIVER_XINPUT)
    _p("booting the simulated PS3 (XPAD Revolution VSH plugin, PowerPC big-endian under qemu)...")
    rep = run_test(device=device, interactive=args.real, log=_p, native_ds3=args.with_ds3)
    for r in rep.rows:
        if not r["ok"] or args.verbose:
            _p(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['check']}  {r['detail'][:110]}")
    _p(f"{'PASS' if rep.ok else 'FAIL'}: {rep.summary()} (report: {sorted(CAPTURES.glob('sim_*'))[-1] / 'report.md'})")
    return 0 if rep.ok else 1


def cmd_pc_test(args) -> int:
    from .receiver import xinput_rumble, xinput_slots
    slots = xinput_slots()
    if not slots:
        _p("no XInput controller: turn the 8BitDo on (press Home)")
        return 1
    slot = next(iter(slots))
    _p(f"XInput slot {slot + 1}: short rumble pulse, then {args.seconds:g} s of live input (press things)")
    xinput_rumble(slot, 0.6, 0.6)
    time.sleep(0.4)
    xinput_rumble(slot, 0, 0)
    last = None
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        s = xinput_slots().get(slot)
        if s and s["packet"] != (last or {}).get("packet"):
            _p(f"  {s['buttons']} LT={s['LT']} RT={s['RT']} L=({s['LX']},{s['LY']}) R=({s['RX']},{s['RY']})")
            last = s
        time.sleep(0.03)
    return 0


def cmd_capture_descriptors(args) -> int:
    from . import PID_RECEIVER_XINPUT, VID_8BITDO
    from .bridge import RealUsbDevice
    dev = RealUsbDevice(VID_8BITDO, PID_RECEIVER_XINPUT)
    d = dev.descriptors
    CAPTURES.mkdir(exist_ok=True)
    out = CAPTURES / "310b_descriptors.json"
    out.write_text(json.dumps({"device": d[:18].hex(), "configuration": d[18:].hex(),
                               "captured": time.strftime("%Y-%m-%d %H:%M:%S")}, indent=1))
    dev.close()
    _p(f"saved {len(d)} bytes of descriptors to {out}")
    return 0


LIVE_STEPS = ["A", "B", "X", "Y", "LB", "RB", "BACK", "START", "GUIDE", "LS", "RS", "UP", "DOWN", "LEFT", "RIGHT"]


def _live_walkthrough(tel, log=_p) -> list[dict]:
    from .xinput import XINPUT_TO_DS3, XState
    from .telemetry import matches
    steps = [(f"press and hold {b} (PS3: {XINPUT_TO_DS3[b]})", XState({b})) for b in LIVE_STEPS]
    steps += [("pull LT (L2) all the way", XState(lt=255)), ("pull RT (R2) all the way", XState(rt=255)),
              ("left stick fully LEFT", XState(lx=-32768)), ("left stick fully RIGHT", XState(lx=32767)),
              ("left stick fully UP", XState(ly=32767)), ("left stick fully DOWN", XState(ly=-32768)),
              ("right stick fully LEFT", XState(rx=-32768)), ("right stick fully RIGHT", XState(rx=32767)),
              ("right stick fully UP", XState(ry=32767)), ("right stick fully DOWN", XState(ry=-32768))]
    rows = []
    for name, st in steps:
        log(f" >> {name}")
        tol_ok = lambda t: not [d for d in matches(st, t, tol=20) if not (d.startswith(("LT", "RT")) and
                                                                         int(d.split()[1]) >= 245)]
        hit = tel.wait(tol_ok, 20)
        rows.append({"step": name, "ok": hit is not None, "seen": {k: (sorted(v) if isinstance(v, set) else v)
                                                                    for k, v in (hit or {}).items()}})
        log(f"    {'OK' if hit else 'not seen'}")
        tel.wait(lambda t: not t["buttons"] and t["LT"] < 10 and t["RT"] < 10 and 100 < t["LX"] < 156 and 100 < t["LY"] < 156
                 and 100 < t["RX"] < 156 and 100 < t["RY"] < 156, 10)
    return rows


def cmd_live(args) -> int:
    """Real-console view: what the PS3 plugin itself reads from the receiver."""
    from .ps3ftp import load_config
    from .telemetry import Telemetry
    host = args.host or load_config().get("ps3_host") or "255.255.255.255"
    tel = Telemetry(host)
    _p(f"asking the PS3 plugin ({host}) for live telemetry...")
    if tel.wait(lambda t: t["connected"], 10) is None:
        _p("no telemetry: plugin not running, receiver not attached on the PS3, or the PS3 is unreachable")
        tel.close()
        return 1
    _p(f"receiving from {tel.source}")
    if args.walkthrough:
        rows = _live_walkthrough(tel)
        out = CAPTURES / time.strftime("real_ps3_walkthrough_%Y%m%d_%H%M%S.json")
        CAPTURES.mkdir(exist_ok=True)
        out.write_text(json.dumps(rows, indent=1))
        ok = sum(r["ok"] for r in rows)
        _p(f"{ok}/{len(rows)} inputs confirmed on the real PS3 (saved {out})")
        tel.close()
        return 0 if ok == len(rows) else 1
    last = None
    end = time.monotonic() + args.seconds
    while time.monotonic() < end:
        t = tel.wait(lambda t: t["seq"] != (last or {}).get("seq"), 1)
        if t and (last is None or {k: t[k] for k in t if k != "seq"} != {k: last[k] for k in last if k != "seq"}):
            _p(f"  pad {t['pad'] + 1}: {sorted(t['buttons'])} L2={t['LT']} R2={t['RT']} "
               f"L=({t['LX']},{t['LY']}) R=({t['RX']},{t['RY']})")
        last = t or last
    tel.close()
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="8bitdo-ps3", description=f"8bitdo-ps3 {__version__}: 8BitDo Ultimate 2 "
                                 "Bluetooth controller (2.4G receiver) on a HEN PS3 and on PC")
    ap.add_argument("--version", action="version", version=f"8bitdo-ps3 {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="command")
    s = sub.add_parser("status", help="receiver / PC / DualShock 3 / PS3 at a glance")
    s.add_argument("--host", help="PS3 IP address (found automatically otherwise)")
    s = sub.add_parser("use", help="switch: `use ps3` starts the PS3 side, `use pc` checks the PC side")
    s.add_argument("target", choices=["ps3", "pc"])
    s.add_argument("--host", help="PS3 IP address")
    s = sub.add_parser("ps3", help="PS3 side: find, install, start, watch, status, log, live, uninstall, restore")
    s.add_argument("action", choices=["find", "install", "start", "watch", "status", "log", "live", "uninstall", "restore"])
    s.add_argument("--host", help="PS3 IP address (found automatically otherwise)")
    s.add_argument("--minutes", type=float, default=0,
                   help="watch: stop after N minutes (default: run until Ctrl+C); restore: how long to keep trying")
    s.add_argument("--settle", type=float, default=15,
                   help="watch: seconds to wait after webMAN comes up before starting XPAD (default 15)")
    s.add_argument("--wait", action="store_true", help="restore: keep polling until the PS3 answers")
    s.add_argument("--walkthrough", action="store_true", help="live: guided check of every button and axis")
    s.add_argument("--seconds", type=float, default=30, help="live: how long to show input")
    s = sub.add_parser("recovery-stick", help="write a boot_plugins.txt that loads only webMAN to a FAT32 USB stick")
    s.add_argument("--minutes", type=float, default=10)
    s = sub.add_parser("pc-test", help="live view of what Windows games receive (XInput) + rumble pulse")
    s.add_argument("--seconds", type=float, default=15)
    s = sub.add_parser("test", help="run the simulated PS3 (needs WSL, see README)")
    s.add_argument("--real", action="store_true", help="use your real receiver instead of the virtual one (UsbDk)")
    s.add_argument("--with-ds3", action="store_true", help="simulate a DualShock 3 already on port 1")
    s.add_argument("-v", "--verbose", action="store_true")
    sub.add_parser("capture-descriptors", help="save the real receiver's USB descriptors for the simulator")
    args = ap.parse_args(argv)
    if args.cmd == "ps3" and args.action == "restore" and not args.minutes:
        args.minutes = 10
    return {"status": cmd_status, "use": cmd_use, "test": cmd_test, "pc-test": cmd_pc_test,
            "capture-descriptors": cmd_capture_descriptors, "ps3": cmd_ps3,
            "recovery-stick": cmd_recovery_stick}[args.cmd](args)
