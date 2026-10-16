import struct

from bitdo_ps3.ps3ftp import explain_log
from bitdo_ps3.virtual_receiver import _model_descriptors
from bitdo_ps3.xinput import XState, axis_x, axis_y, expected_pad, grade


def test_xinput_report_layout_matches_xpad_big_endian_view():
    # XPAD reads the LE button word with a big-endian load: btnDigiUp=0x0100, btnA=0x0010, btnShoulderLeft=0x0001
    rep = XState({"UP", "A", "LB"}).report()
    be_word = struct.unpack(">H", rep[2:4])[0]
    assert be_word & 0x0100 and be_word & 0x0010 and be_word & 0x0001
    assert len(rep) == 20 and rep[0] == 0 and rep[1] == 0x14


def test_axes_follow_ds3_conventions():
    assert axis_x(-32768) == 0 and axis_x(32767) == 255 and 127 <= axis_x(0) <= 128
    assert axis_y(32767) == 0 and axis_y(-32768) == 255  # stick up = 0 on a DS3


def test_reference_model_positional_faces_and_pressure():
    e = expected_pad(XState({"A", "Y"}, lt=77))
    assert e["digital2"] == 0x40 | 0x10 | 0x01  # CROSS | TRIANGLE | L2
    assert e["pressure"][18] == 77 and e["pressure"][14] == 255


def test_grade_detects_wrong_button():
    b = [0] * 24
    b[4:8] = [128, 128, 128, 128]
    b[3] = 0x20  # CIRCLE while CROSS is pressed
    b[13] = 255
    assert grade(XState({"A"}), b)


def test_model_descriptors_have_xinput_interface_first():
    d = _model_descriptors()
    assert d[8:12] == bytes([0xC8, 0x2D, 0x0B, 0x31])
    cfg = d[18:]
    assert cfg[9 + 5:9 + 8] == bytes([0xFF, 0x5D, 0x01])


def test_explain_log_switch_mode_problem():
    msgs = explain_log("[1.0] probe rejected: VID/PID not in wired table 057E:2009 (code 0x00000000)")
    assert any("Switch Pro" in m for m in msgs)


def test_explain_log_rumble_loader():
    log = ("[3.096] manual loader: normalized USB combo accepted on port (code 0x00000001)\n"
           "[3.105] manual loader: loading xpad_game.sprx into BLUS30443_main_EBOOT.BIN (pid 16843264)\n"
           "[3.110] manual loader: LOAD_PROC_MODULE returned OK for BLUS30443_main_EBOOT.BIN (pid 16843264)\n")
    msgs = explain_log(log)
    assert any("SELECT+L3+R3 was recognised" in m for m in msgs)
    assert any("rumble module was loaded" in m for m in msgs)
    msgs = explain_log("[9.0] manual loader: hotkey pressed but no EBOOT process was found\n")
    assert any("start the game first" in m for m in msgs)


def test_explain_log_ok():
    msgs = explain_log("XPAD Rev v1.0.0 Loaded!\nattach starting for 2DC8:310B\nattach OK, LDD pad port 0\n"
                       "first input report received from 2DC8:310B (code 0x00000014)")
    assert any("XInput mode" in m for m in msgs) and any("flowing" in m for m in msgs)
