"""6.5.0.4 Practice — Router anti-spoofing.

We import the student's `host.py` (their Meeting-5 router plus the source check) and feed it
raw IPv4 packets through `process_packet`. `forward_packet` opens a real socket via
`make_socket`, which we replace with a recorder so we can tell delivery/forwarding from a
drop. Each test builds a fresh router. The docstring is the positional Codo row name.
"""
import struct

import pytest

import host as host_module
from host import Host
from routing import RouteEntry

ROUTER_IP, ROUTER_MAC = "10.0.0.1", "02:00:00:00:00:01"
DST_IP, DST_MAC = "10.0.1.5", "02:00:00:00:00:05"

_SENT = []


class _FakeSock:
    def send(self, data):
        _SENT.append(data)
        return len(data)

    def close(self):
        pass


def _ip_bytes(ip):
    return bytes(int(octet) for octet in ip.split("."))


def _ipv4(src, dst, ttl=64, payload=b"hello"):
    total_length = 20 + len(payload)
    header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, total_length, 0, 0, ttl, 6, 0,
                         _ip_bytes(src), _ip_bytes(dst))
    return header + payload


@pytest.fixture
def router(monkeypatch):
    _SENT.clear()
    monkeypatch.setattr(host_module, "make_socket", lambda interface: _FakeSock())
    h = Host(ROUTER_IP, ROUTER_MAC)
    h.routing_table.add_route(RouteEntry("10.0.1.0", "255.255.255.0", None, "eth1"))
    h.arp_table.learn(DST_IP, DST_MAC)
    return h


def test_spoofed_source_is_dropped(router, capsys):
    """A packet claiming the router's own IP as its source is dropped, not forwarded."""
    router.process_packet(_ipv4(ROUTER_IP, DST_IP))
    out = capsys.readouterr().out
    assert "[DROP]" in out and "[FORWARD]" not in out
    assert _SENT == []


def test_spoofed_source_not_delivered(router, capsys):
    """Even a spoofed packet addressed to the router is dropped, not delivered."""
    router.process_packet(_ipv4(ROUTER_IP, ROUTER_IP))
    out = capsys.readouterr().out
    assert "[DROP]" in out and "[DELIVER]" not in out


def test_normal_source_is_forwarded(router, capsys):
    """A packet with an ordinary source is forwarded as before."""
    router.process_packet(_ipv4("10.0.2.2", DST_IP))
    assert "[FORWARD]" in capsys.readouterr().out
    assert len(_SENT) == 1


def test_delivery_still_works(router, capsys):
    """A normal packet addressed to the router is still delivered."""
    router.process_packet(_ipv4("10.0.2.2", ROUTER_IP))
    assert "[DELIVER]" in capsys.readouterr().out


def test_no_route_still_dropped(router, capsys):
    """Base behaviour still holds: an ordinary packet with no route is dropped."""
    router.process_packet(_ipv4("10.0.2.2", "9.9.9.9"))
    out = capsys.readouterr().out
    assert "[DROP]" in out and "[FORWARD]" not in out
    assert _SENT == []
