"""6.5.0.4 Practice — Detect ARP poisoning (unsolicited replies).

We import the student's `switch.py` (their Meeting-4 switch plus the one detection check) and
feed it ARP frames through `_process_frame`. `Port` opens a real AF_PACKET socket, so we replace
`socket.socket` with a recorder (bind no-op, send records) to check both "did it print
ARP-POISONING?" and "did it forward or drop?". Each test builds a fresh switch. The docstring is
the positional Codo row name.
"""
import socket
import struct

import pytest

from switch import Switch

GW_IP, GW_MAC = "10.0.0.1", "02:00:00:00:00:01"
VIC_IP, VIC_MAC = "10.0.0.2", "02:00:00:00:00:02"
ATK_MAC = "02:00:00:00:00:66"
BROADCAST, ZERO = "ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"


def _mac(mac):
    return bytes.fromhex(mac.replace(":", ""))


def _ip(ip):
    return bytes(int(octet) for octet in ip.split("."))


def _arp_frame(eth_src, eth_dst, opcode, sender_mac, sender_ip, target_mac, target_ip):
    arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, opcode,
                      _mac(sender_mac), _ip(sender_ip), _mac(target_mac), _ip(target_ip))
    eth = _mac(eth_dst) + _mac(eth_src) + struct.pack("!H", 0x0806)
    return eth + arp


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
    return Switch(["p_gw", "p_victim", "p_attacker"])


def _forwarded(switch):
    return sum(len(port.sock.sent) for port in switch.ports)


def test_clean_request_is_forwarded(sw, capsys):
    """A normal ARP request is not an attack — forwarded, no alert."""
    sw._process_frame(_arp_frame(VIC_MAC, BROADCAST, 1, VIC_MAC, VIC_IP, ZERO, GW_IP), sw.ports[1])
    assert "ARP-POISONING" not in capsys.readouterr().out
    assert _forwarded(sw) > 0


def test_solicited_reply_is_forwarded(sw, capsys):
    """A reply that answers a request the switch saw is legitimate — no alert."""
    sw._process_frame(_arp_frame(VIC_MAC, BROADCAST, 1, VIC_MAC, VIC_IP, ZERO, GW_IP), sw.ports[1])
    capsys.readouterr()
    sw._process_frame(_arp_frame(GW_MAC, VIC_MAC, 2, GW_MAC, GW_IP, VIC_MAC, VIC_IP), sw.ports[0])
    assert "ARP-POISONING" not in capsys.readouterr().out


def test_unsolicited_reply_alerts_and_drops(sw, capsys):
    """A reply the switch never saw a request for is the poisoning signature — alert + drop."""
    sw._process_frame(_arp_frame(GW_MAC, VIC_MAC, 2, GW_MAC, GW_IP, VIC_MAC, VIC_IP), sw.ports[2])
    assert "ARP-POISONING" in capsys.readouterr().out
    assert _forwarded(sw) == 0


def test_second_unsolicited_reply_also_alerts(sw, capsys):
    """Another unsolicited reply (different sender) is caught too."""
    sw._process_frame(_arp_frame(ATK_MAC, VIC_MAC, 2, ATK_MAC, VIC_IP, VIC_MAC, GW_IP), sw.ports[2])
    assert "ARP-POISONING" in capsys.readouterr().out
    assert _forwarded(sw) == 0


def test_non_arp_frame_passes_through(sw, capsys):
    """Detection only inspects ARP — a non-ARP frame flows untouched, no alert."""
    eth = _mac(VIC_MAC) + _mac(GW_MAC) + struct.pack("!H", 0x0800)  # IPv4 ethertype
    sw._process_frame(eth + b"\x45\x00" + b"\x00" * 30, sw.ports[1])
    assert "ARP-POISONING" not in capsys.readouterr().out
    assert _forwarded(sw) > 0
