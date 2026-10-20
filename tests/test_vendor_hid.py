import hid

from bitdo_ps3 import vendor_hid


def test_crc16_matches_the_receiver():
    assert vendor_hid.crc16_modbus(bytes.fromhex("e803ffff0000")) == 0x1753
    assert vendor_hid.crc16_modbus(bytes(6)) == 0x1B00


def test_reportstate_packet_is_the_short_form():
    assert vendor_hid.reportstate_packet(1) == bytes.fromhex(
        "81 04 07 00 01 00 00 00 00 00 00 00 00 00 00 00 00 00")


def test_readkeys_packet_is_the_short_form():
    assert vendor_hid.readkeys_packet() == bytes.fromhex(
        "81 04 03 00 23 01 00 00 00 00 00 00 00 00 00 00 00 00")


def test_rumble_packet_matches_the_console_capture():
    assert vendor_hid.rumble_packet(255, 255, 1000).hex(" ") == (
        "81 04 08 00 00 00 06 00 53 17 06 00 00 00 00 00 00 00 e8 03 ff ff 00 00")


def test_stop_packet_has_the_zero_crc():
    assert vendor_hid.stop_packet().hex(" ") == (
        "81 04 08 00 00 00 06 00 00 1b 06 00 00 00 00 00 00 00 00 00 00 00 00 00")


class _FakeDevice:
    def __init__(self):
        self.pending = None
        self.writes = []

    def open_path(self, path):
        assert path == b"fake"

    def write(self, data):
        self.writes.append(bytes(data))
        command = bytes(data)[2:4]
        ack = bytes([0x02, 0x04, 0x04, 0x00]) + command + bytes(58)
        self.pending = ack
        return len(data)

    def read(self, length, timeout):
        if self.pending is None:
            return []
        out, self.pending = self.pending, None
        return list(out)

    def close(self):
        pass


def test_verify_rumble_requires_every_ack(monkeypatch):
    fake = _FakeDevice()
    monkeypatch.setattr(vendor_hid, "find_vendor_path", lambda: b"fake")
    monkeypatch.setattr(hid, "device", lambda: fake)
    monkeypatch.setattr(vendor_hid.time, "sleep", lambda _: None)

    result = vendor_hid.verify_rumble(pulses=2, duration_ms=10, strength=128, gap_ms=0)

    assert result.ok
    assert result.state_ack and result.stop_ack
    assert len(result.pulses) == 2 and all(p.ok for p in result.pulses)
    assert len(fake.writes) == 4


def test_verify_rumble_reports_a_missing_ack(monkeypatch):
    fake = _FakeDevice()
    monkeypatch.setattr(vendor_hid, "find_vendor_path", lambda: b"fake")
    monkeypatch.setattr(hid, "device", lambda: fake)
    monkeypatch.setattr(vendor_hid, "_read_ack", lambda h, sub, timeout: None)
    monkeypatch.setattr(vendor_hid.time, "sleep", lambda _: None)

    result = vendor_hid.verify_rumble(pulses=1, duration_ms=10)

    assert not result.ok
    assert result.state_ack is None
    assert result.pulses and result.pulses[0].ack is None
