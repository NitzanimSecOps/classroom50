"""6.5.0.3 Practice — Switch port security.

We import the student's `switch.py` (their Meeting-4 switch plus the port-security rule) and
feed it Ethernet frames through `_process_frame`. `Port` opens a real AF_PACKET socket, so we
replace `socket.socket` with a recorder (bind no-op, send records) to check both "did it print
PORT-SECURITY?" and "did it forward or drop?". Each test builds a fresh switch. The docstring
is the positional Codo row name.
"""
import socket
import struct

import pytest

from switch import Switch

MAC_A = "02:00:00:00:00:0a"
MAC_B = "02:00:00:00:00:0b"
MAC_C = "02:00:00:00:00:0c"
BROADCAST = "ff:ff:ff:ff:ff:ff"


def _mac(mac):
    return bytes.fromhex(mac.replace(":", ""))


def _frame(src, dst=BROADCAST):
    # A plain (non-ARP) broadcast frame is enough: port security looks only at the source.
    return _mac(dst) + _mac(src) + struct.pack("!H", 0x0800) + b"payload"


class _FakeSock:
    def __init__(self, *a, **k):
        self.sent = []

    def bind(self, addr):
        self.addr = addr

    def send(self, data):
        self.sent.append(data)
        return len(data)

    def setsockopt(self, *a, **k):
        pass

    def close(self):
        pass


@pytest.fixture
def sw(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: _FakeSock())
    return Switch(["p0", "p1", "p2"])


def _forwarded(switch):
    return sum(len(port.sock.sent) for port in switch.ports)


def _clear(switch):
    for port in switch.ports:
        port.sock.sent.clear()


def test_first_mac_is_forwarded(sw, capsys):
    """The first MAC a port sees is accepted and forwarded, no alert."""
    sw._process_frame(_frame(MAC_A), sw.ports[0])
    assert "PORT-SECURITY" not in capsys.readouterr().out
    assert _forwarded(sw) > 0


def test_same_mac_repeats_ok(sw, capsys):
    """The locked MAC may keep using its port."""
    sw._process_frame(_frame(MAC_A), sw.ports[0])
    capsys.readouterr()
    sw._process_frame(_frame(MAC_A), sw.ports[0])
    assert "PORT-SECURITY" not in capsys.readouterr().out


def test_second_mac_on_locked_port_blocked(sw, capsys):
    """A different MAC on a port that already learned one is blocked and dropped."""
    sw._process_frame(_frame(MAC_A), sw.ports[0])
    capsys.readouterr()
    _clear(sw)
    sw._process_frame(_frame(MAC_B), sw.ports[0])
    assert "PORT-SECURITY" in capsys.readouterr().out
    assert _forwarded(sw) == 0


def test_third_mac_also_blocked(sw, capsys):
    """The port stays locked to its first MAC — any other MAC is blocked too."""
    sw._process_frame(_frame(MAC_A), sw.ports[0])
    capsys.readouterr()
    _clear(sw)
    sw._process_frame(_frame(MAC_C), sw.ports[0])
    assert "PORT-SECURITY" in capsys.readouterr().out
    assert _forwarded(sw) == 0


def test_other_port_is_independent(sw, capsys):
    """Each port locks to its own first MAC — MAC-B is fine on a different port."""
    sw._process_frame(_frame(MAC_A), sw.ports[0])
    capsys.readouterr()
    _clear(sw)
    sw._process_frame(_frame(MAC_B), sw.ports[1])
    assert "PORT-SECURITY" not in capsys.readouterr().out
    assert _forwarded(sw) > 0


def test_legit_mac_still_ok_after_violation(sw, capsys):
    """A blocked intruder does not lock out the port's real owner."""
    sw._process_frame(_frame(MAC_A), sw.ports[0])
    sw._process_frame(_frame(MAC_B), sw.ports[0])  # blocked
    capsys.readouterr()
    _clear(sw)
    sw._process_frame(_frame(MAC_A), sw.ports[0])
    assert "PORT-SECURITY" not in capsys.readouterr().out
    assert _forwarded(sw) > 0
