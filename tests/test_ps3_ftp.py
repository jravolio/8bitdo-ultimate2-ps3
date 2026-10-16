"""Deployment against a local FTP server that mimics a HEN PS3 running webMAN MOD (anonymous FTP,
/dev_hdd0 tree, an existing boot_plugins.txt with webMAN in it)."""
import threading
import urllib.error

import pytest
from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import FTPServer

from bitdo_ps3 import ps3ftp, xpad_files

WEBMAN_LINE = b"/dev_hdd0/plugins/webftp_server.sprx\r\n"
XPAD_LINE = ps3ftp.PLUGIN_LINE.encode() + b"\r\n"


@pytest.fixture()
def fake_ps3(tmp_path, monkeypatch):
    root = tmp_path / "ps3"
    (root / "dev_hdd0" / "plugins").mkdir(parents=True)
    (root / "dev_hdd0" / "boot_plugins.txt").write_bytes(WEBMAN_LINE)
    auth = DummyAuthorizer()
    auth.add_anonymous(str(root), perm="elradfmwMT")
    handler = type("H", (FTPHandler,), {"authorizer": auth, "banner": "webMAN MOD FTP"})
    srv = FTPServer(("127.0.0.1", 0), handler)
    port = srv.socket.getsockname()[1]
    t = threading.Thread(target=srv.serve_forever, kwargs={"timeout": 0.2}, daemon=True)
    t.start()
    monkeypatch.setattr(ps3ftp, "BACKUPS", tmp_path / "backups")
    orig = ps3ftp.ftplib.FTP.connect
    monkeypatch.setattr(ps3ftp.ftplib.FTP, "connect", lambda self, host, p=21, timeout=10: orig(self, host, port, timeout))
    yield root
    srv.close_all()


@pytest.fixture()
def payload(tmp_path):
    try:
        return xpad_files.fetch(tmp_path / "payload", log=lambda *a: None)
    except urllib.error.URLError as exc:
        pytest.skip(f"cannot download XPAD Revolution files: {exc}")


def test_install_and_uninstall(fake_ps3, payload):
    ps3 = ps3ftp.PS3("127.0.0.1")
    ps3.install(payload, log=lambda *a: None)
    ps3.remove_old_autostart(log=lambda *a: None)
    st = ps3.status()
    assert st["installed"] and st["files_match_pinned"] and not st["in_boot_plugins"]
    assert (fake_ps3 / "dev_hdd0" / "boot_plugins.txt").read_bytes() == WEBMAN_LINE  # untouched
    assert not (fake_ps3 / "dev_hdd0" / "autoexec.bat").exists()
    ps3.uninstall_files(log=lambda *a: None)
    assert not (fake_ps3 / "dev_hdd0" / "plugins" / "ps3xpad" / "xpad_vsh_autodiscovery.sprx").exists()
    ps3.close()


def test_install_cleans_stale_boot_plugins_lines_everywhere(fake_ps3):
    hdd = fake_ps3 / "dev_hdd0" / "boot_plugins.txt"
    hdd.write_bytes(WEBMAN_LINE + XPAD_LINE + b"/dev_hdd0/plugins/other.sprx\r\n")
    (fake_ps3 / "dev_usb000").mkdir()
    usb = fake_ps3 / "dev_usb000" / "boot_plugins.txt"
    usb.write_bytes(XPAD_LINE.replace(b"\r\n", b"\n"))
    ps3 = ps3ftp.PS3("127.0.0.1")
    ps3.remove_old_autostart(log=lambda *a: None)
    assert hdd.read_bytes() == WEBMAN_LINE + b"/dev_hdd0/plugins/other.sprx\r\n"  # CRLF and order kept
    assert usb.read_bytes() == b""
    assert (fake_ps3 / "dev_hdd0" / "boot_plugins.txt.8bitdo-backup").read_bytes().count(b"xpad_vsh") == 1
    assert ps3.remove_from_boot_plugins(log=lambda *a: None) == []  # second pass: nothing left to change
    assert not ps3.status()["in_boot_plugins"]
    ps3.close()


def test_old_autoexec_block_removed_and_user_lines_kept(fake_ps3):
    bat = fake_ps3 / "dev_hdd0" / "autoexec.bat"
    mine = b"popup hello\r\nwait xmb\r\n"
    bat.write_bytes(mine + ps3ftp.AUTOEXEC_BEGIN.encode() + b"\nwait xmb\n/loadprx_ps3?prx=" + ps3ftp.PLUGIN_LINE.encode()
                    + b"\n" + ps3ftp.AUTOEXEC_END.encode() + b"\n")
    ps3 = ps3ftp.PS3("127.0.0.1")
    assert ps3.status()["old_autoexec_block"]
    ps3.remove_old_autostart(log=lambda *a: None)
    assert bat.read_bytes() == mine
    bat.write_bytes(ps3ftp.AUTOEXEC_BEGIN.encode() + b"\nwait xmb\n" + ps3ftp.AUTOEXEC_END.encode() + b"\n")
    ps3.remove_old_autostart(log=lambda *a: None)
    assert not bat.exists()  # only our block was in it
    bat.write_bytes(mine)
    ps3.remove_old_autostart(log=lambda *a: None)
    assert bat.read_bytes() == mine  # a user script without our block is never rewritten
    ps3.close()


def test_restore_removes_xpad_from_boot_plugins(fake_ps3, monkeypatch):
    import argparse
    from bitdo_ps3 import cli
    (fake_ps3 / "dev_hdd0" / "boot_plugins.txt").write_bytes(WEBMAN_LINE + XPAD_LINE)
    monkeypatch.setattr(cli, "_ftp_up", lambda host: True)
    monkeypatch.setattr(ps3ftp, "load_config", lambda: {"ps3_host": "127.0.0.1"})
    args = argparse.Namespace(host="127.0.0.1", wait=False, minutes=0.1)
    assert cli.cmd_restore(args) == 0
    assert (fake_ps3 / "dev_hdd0" / "boot_plugins.txt").read_bytes() == WEBMAN_LINE


def test_pinned_files_verification(tmp_path, monkeypatch, payload):
    txt = (payload / "xpad_devices.txt").read_bytes()
    assert xpad_files.verified("xpad_devices.txt", txt.replace(b"\n", b"\r\n"))  # autocrlf checkout is fine
    sprx = (payload / "xpad_vsh_autodiscovery.sprx").read_bytes()
    assert not xpad_files.verified("xpad_vsh_autodiscovery.sprx", sprx[:-1] + b"\x00")

    class Tampered:
        def __init__(self, *a, **k):
            pass
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self):
            return b"not the plugin"
    monkeypatch.setattr(xpad_files, "XPAD_REPO", tmp_path / "no-clone")
    monkeypatch.setattr(xpad_files.urllib.request, "urlopen", Tampered)
    with pytest.raises(RuntimeError, match="does not match"):
        xpad_files.fetch(tmp_path / "fresh", log=lambda *a: None)
    assert not (tmp_path / "fresh" / "xpad_vsh_autodiscovery.sprx").exists()
