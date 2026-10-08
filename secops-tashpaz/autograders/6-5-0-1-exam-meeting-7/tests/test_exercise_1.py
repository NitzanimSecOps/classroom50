"""6.5.0.1 Exam — ARP-poisoning detection on the student's own switch.

We import the student's `switch.py` (the Meeting-4 switch they finished, plus their new
`_verify_arp_frame`) and feed it crafted frames through `_process_frame`. The contract:
a poisoned frame makes the switch print the token ``ARP-POISONING`` and drop the frame
(never forwarded); a legitimate frame does neither.

`Port` opens a real AF_PACKET socket, which we never want in a grader, so we replace
`socket.socket` with a recorder: `bind` is a no-op and `send` records the frame, which
lets us assert both "did it alert?" and "did it forward or drop?". Each test builds a
fresh switch so the three detection checks score independently. Every test is one
positional Codo row (point values live in the Codo task); the docstring is the row name.
"""
import socket
import struct

import pytest

from switch import Switch

# The segment, same addresses as the Unit-6 ARP lab.
GW_IP, GW_MAC = "10.0.0.1", "02:00:00:00:00:01"
VIC_IP, VIC_MAC = "10.0.0.2", "02:00:00:00:00:02"
ATK_MAC = "02:00:00:00:00:66"
BROADCAST, ZERO = "ff:ff:ff:ff:ff:ff", "00:00:00:00:00:00"


def _mac(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def _ip(ip: str) -> bytes:
    return bytes(int(octet) for octet in ip.split("."))


def _arp_frame(eth_src, eth_dst, opcode, sender_mac, sender_ip, target_mac, target_ip) -> bytes:
    """A full Ethernet+ARP frame. eth_src/eth_dst are the Ethernet header; the rest is
    the ARP message, so a test can forge the two source MACs independently."""
    arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, opcode,
                      _mac(sender_mac), _ip(sender_ip), _mac(target_mac), _ip(target_ip))
    eth = _mac(eth_dst) + _mac(eth_src) + struct.pack("!H", 0x0806)
    return eth + arp


class _FakeSock:
    def __init__(self, *args, **kwargs):
        self.sent = []

    def bind(self, addr):
        self.addr = addr

    def send(self, data):
        self.sent.append(data)
        return len(data)

    def setsockopt(self, *args, **kwargs):
        pass

    def close(self):
        pass


@pytest.fixture
def sw(monkeypatch):
    monkeypatch.setattr(socket, "socket", lambda *a, **k: _FakeSock())
    # ports[0] = gateway, ports[1] = victim, ports[2] = attacker
    return Switch(["p_gw", "p_victim", "p_attacker"])


def _forwarded(switch) -> int:
    return sum(len(port.sock.sent) for port in switch.ports)


def _clear(switch) -> None:
    for port in switch.ports:
        port.sock.sent.clear()


# --- legitimate traffic must NOT alert ---------------------------------------
def test_clean_request_is_forwarded(sw, capsys):
    """A normal broadcast ARP request is not an attack — forwarded, no alert."""
    frame = _arp_frame(VIC_MAC, BROADCAST, 1, VIC_MAC, VIC_IP, ZERO, GW_IP)
    sw._process_frame(frame, sw.ports[1])
    assert "ARP-POISONING" not in capsys.readouterr().out
    assert _forwarded(sw) > 0


def test_solicited_reply_is_forwarded(sw, capsys):
    """A reply that answers a request we saw go past is legitimate — no alert."""
    sw._process_frame(_arp_frame(VIC_MAC, BROADCAST, 1, VIC_MAC, VIC_IP, ZERO, GW_IP), sw.ports[1])
    capsys.readouterr()
    reply = _arp_frame(GW_MAC, VIC_MAC, 2, GW_MAC, GW_IP, VIC_MAC, VIC_IP)
    sw._process_frame(reply, sw.ports[0])
    assert "ARP-POISONING" not in capsys.readouterr().out


# --- poisoning must alert AND be dropped -------------------------------------
def test_unsolicited_reply_alerts_and_drops(sw, capsys):
    """An ARP reply that answers no request we saw is the classic poisoning signature."""
    reply = _arp_frame(GW_MAC, VIC_MAC, 2, GW_MAC, GW_IP, VIC_MAC, VIC_IP)
    sw._process_frame(reply, sw.ports[2])
    assert "ARP-POISONING" in capsys.readouterr().out
    assert _forwarded(sw) == 0


def test_known_ip_from_another_port_alerts_and_drops(sw, capsys):
    """An IP already known on one port, now claimed from another port = impersonation."""
    sw._process_frame(_arp_frame(GW_MAC, BROADCAST, 1, GW_MAC, GW_IP, ZERO, GW_IP), sw.ports[0])
    capsys.readouterr()
    _clear(sw)
    spoof = _arp_frame(ATK_MAC, BROADCAST, 1, ATK_MAC, GW_IP, ZERO, VIC_IP)
    sw._process_frame(spoof, sw.ports[2])
    assert "ARP-POISONING" in capsys.readouterr().out
    assert _forwarded(sw) == 0


def test_known_ip_same_port_is_clean(sw, capsys):
    """The gateway re-announcing from its own port is a normal refresh — no alert."""
    announce = _arp_frame(GW_MAC, BROADCAST, 1, GW_MAC, GW_IP, ZERO, GW_IP)
    sw._process_frame(announce, sw.ports[0])
    capsys.readouterr()
    sw._process_frame(announce, sw.ports[0])
    assert "ARP-POISONING" not in capsys.readouterr().out


def test_src_mac_mismatch_alerts_and_drops(sw, capsys):
    """The Ethernet source and the ARP 'sender MAC' disagree — a forged sender address."""
    frame = _arp_frame(ATK_MAC, BROADCAST, 1, GW_MAC, GW_IP, ZERO, VIC_IP)
    sw._process_frame(frame, sw.ports[2])
    assert "ARP-POISONING" in capsys.readouterr().out
    assert _forwarded(sw) == 0


def test_matching_src_mac_is_clean(sw, capsys):
    """A request whose Ethernet source matches its ARP sender is fine — no false alarm."""
    frame = _arp_frame(VIC_MAC, BROADCAST, 1, VIC_MAC, VIC_IP, ZERO, GW_IP)
    sw._process_frame(frame, sw.ports[1])
    assert "ARP-POISONING" not in capsys.readouterr().out


def test_non_arp_frame_passes_through(sw, capsys):
    """Detection only inspects ARP — a non-ARP frame flows untouched, no alert."""
    eth = _mac(VIC_MAC) + _mac(GW_MAC) + struct.pack("!H", 0x0800)  # IPv4 ethertype
    frame = eth + b"\x45\x00" + b"\x00" * 30
    sw._process_frame(frame, sw.ports[1])
    assert "ARP-POISONING" not in capsys.readouterr().out
    assert _forwarded(sw) > 0
