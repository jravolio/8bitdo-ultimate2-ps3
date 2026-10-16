"""`ps3 start` against a fake webMAN MOD HTTP server: the module list, segment and getmem pages
answer in the formats webMAN 1.47.48 uses, and VSH memory is a bytearray."""
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from bitdo_ps3 import ps3mapi

BASE = 0xA40000
XPAD_ID = 0x5D0B1C00


class FakeWebMAN:
    def __init__(self, context=ps3mapi.CONTEXT):
        self.mem = bytearray(0x9000)
        self.mem[ps3mapi.CONTEXT_OFFSET:ps3mapi.CONTEXT_OFFSET + len(context)] = context
        self.loaded = False
        self.writes = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                q = urllib.parse.parse_qs(url.query)
                body = fake.handle(url.path, urllib.parse.unquote(url.query), q)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(body.encode("latin-1"))

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.host = f"127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def handle(self, path, query, q):
        if path == "/ps3mapi.ps3":
            if query.startswith("MODULE GETALLPRXID"):
                ids = [0x11110000, 0x22220000] + ([XPAD_ID] if self.loaded else [])
                resp = "".join(f"0x{i:x}" for i in ids)
            elif query.startswith("MODULE GETNAME"):
                resp = "XPADD" if int(query.split()[-1]) == XPAD_ID else "other"
            elif query.startswith("MODULE GETSEGMENTS"):
                resp = f"seg0|0x{BASE:016x}|0x{0x8000:016x}|0x{0x9000:016x}"
            else:
                resp = "?"
            return f'{{"command":"{query}","response": "{resp}"}}'
        if path == "/vshplugin.ps3mapi":
            self.loaded = True
            return "<html>ok</html>"
        if path == "/getmem.ps3mapi":
            addr = int(q["addr"][0], 16) - BASE
            if "val" in q:
                data = bytes.fromhex(q["val"][0])
                self.writes.append((addr, data))
                self.mem[addr:addr + len(data)] = data
                return "<html>written</html>"
            n = int(q["len"][0])
            return f'<html><textarea id="output" style="display:none">{self.mem[addr:addr + n].hex()}</textarea></html>'
        return "404"

    def close(self):
        self.srv.shutdown()


@pytest.fixture()
def webman():
    w = FakeWebMAN()
    yield w
    w.close()


def test_start_loads_and_patches_once(webman):
    assert ps3mapi.xpad_state(webman.host) == {"loaded": False, "patched": False}
    rep = ps3mapi.start_safe(webman.host, log=lambda *a: None)
    assert rep["patched"] and rep["base"] == BASE and rep["prx_id"] == XPAD_ID
    assert webman.writes == [(ps3mapi.PATCH_OFFSET, ps3mapi.PATCHED)]
    assert ps3mapi.xpad_state(webman.host)["patched"]
    again = ps3mapi.start_safe(webman.host, log=lambda *a: None)
    assert again["already"] and len(webman.writes) == 1


def test_other_build_is_never_patched():
    w = FakeWebMAN(context=bytes(24))
    try:
        with pytest.raises(RuntimeError, match="NOT patching"):
            ps3mapi.start_safe(w.host, log=lambda *a: None)
        assert w.writes == []
    finally:
        w.close()


def test_patch_matches_the_pinned_binary_layout():
    # b 0x8cfc from 0x8a38: opcode 18, LI = (0x8cfc - 0x8a38) >> 2, AA = LK = 0
    assert ps3mapi.PATCHED == (0x48000000 | (0x8CFC - 0x8A38)).to_bytes(4, "big")
    assert ps3mapi.CONTEXT[8:12] == bytes.fromhex("419e0010")  # beq cr7, +0x10 (the original branch)
