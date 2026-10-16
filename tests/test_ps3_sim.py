"""End-to-end: XPAD Revolution's unmodified VSH plugin source, built for 32-bit big-endian PowerPC and
run under qemu-ppc in WSL, with the virtual 8BitDo receiver plugged into the simulated PS3."""
import shutil
import subprocess

import pytest

from bitdo_ps3.simrun import WSL_DISTRO, run_test


def _wsl_ready():
    if not shutil.which("wsl.exe"):
        return False
    r = subprocess.run(["wsl.exe", "-d", WSL_DISTRO, "--", "which", "qemu-ppc", "powerpc-linux-gnu-gcc"],
                       capture_output=True, text=True)
    return r.returncode == 0


pytestmark = pytest.mark.skipif(not _wsl_ready(), reason="needs WSL Ubuntu with qemu-user + gcc-powerpc-linux-gnu")


def test_ps3_console_with_8bitdo_receiver():
    rep = run_test(log=lambda *a: None)
    failed = [r for r in rep.rows if not r["ok"]]
    assert not failed, failed


def test_freeze_fix_keeps_rumble_loader_and_stops_libpad_polling():
    """The default run uses the plugin as `ps3 start` leaves it on the console. The unpatched
    plugin is run too, to show the libpad check is meaningful: only it polls libpad from VSH."""
    fixed = run_test(log=lambda *a: None)
    checks = {r["check"]: r["ok"] for r in fixed.rows}
    assert checks["SELECT+L3+R3 loads the in-game module (rumble) into the running game"]
    assert checks["game rumble reaches the controller motors"]
    assert checks["freeze fix: the loader fired from XPAD's own USB data"]
    assert checks["freeze fix: XPAD never polls libpad from VSH (the cause of the XMB freeze)"]
    orig = run_test(log=lambda *a: None, freeze_fix=False)
    assert orig.ok, [r for r in orig.rows if not r["ok"]]
    assert "libpad monitoring ready" in (orig.out_dir / "xpad.log").read_text(errors="replace")


def test_with_dualshock3_already_on_port_1():
    rep = run_test(log=lambda *a: None, native_ds3=True)
    port_row = next(r for r in rep.rows if r["check"].startswith("virtual pad registered"))
    assert port_row["ok"] and port_row["detail"].startswith("port 2"), port_row
    assert rep.ok, [r for r in rep.rows if not r["ok"]]


def test_real_console_tools_against_simulated_console():
    """`ps3 live --walkthrough` (the real-PS3 verification) driven by a scripted player, with the
    plugin's own UDP telemetry thread running in the simulator."""
    import threading

    from bitdo_ps3.cli import _live_walkthrough
    from bitdo_ps3.simrun import SimConsole
    from bitdo_ps3.telemetry import Telemetry
    from bitdo_ps3.virtual_receiver import VirtualReceiver
    from bitdo_ps3.xinput import XState

    vr = VirtualReceiver()
    con = SimConsole(vr, log=lambda *a: None).start()
    try:
        assert con.wait_for(lambda e: e.get("ev") == "notify" and "Loaded" in e.get("msg", ""), 60)
        tel = Telemetry("127.0.0.1")
        assert tel.wait(lambda t: t["connected"], 10)
        keys = {"LEFT": dict(lx=-32768), "RIGHT": dict(lx=32767), "UP": dict(ly=32767), "DOWN": dict(ly=-32768)}

        def player(msg):
            if not msg.startswith(" >> "):
                return
            m = msg[4:]
            if m.startswith("press and hold "):
                st = XState({m.split()[3]})
            elif m.startswith("pull LT"):
                st = XState(lt=255)
            elif m.startswith("pull RT"):
                st = XState(rt=255)
            else:
                d = keys[m.split()[-1]]
                if m.startswith("right"):
                    d = {"r" + k[1:]: v for k, v in d.items()}
                st = XState(**d)
            vr.set_state(st)
            threading.Timer(0.3, lambda: vr.set_state(XState())).start()

        rows = _live_walkthrough(tel, log=player)
        tel.close()
        assert all(r["ok"] for r in rows), [r for r in rows if not r["ok"]]
    finally:
        con.stop()
