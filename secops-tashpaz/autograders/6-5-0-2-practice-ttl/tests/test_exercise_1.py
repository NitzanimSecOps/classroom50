"""6.5.0.2 Practice — Router TTL.

We import the student's `host.py` (their Meeting-5 router, plus the new TTL handling) and
feed it raw IPv4 packets through `process_packet`. `forward_packet` opens a real socket via
`make_socket`, which we replace with a recorder so we can both see whether the packet went
out ([FORWARD]) and inspect the bytes it sent — the TTL must come out one lower. Each test
builds a fresh router. The docstring is the positional Codo row name.
"""
import struct

import pytest

import host as host_module
from host import Host
from routing import RouteEntry

ROUTER_IP, ROUTER_MAC = "10.0.0.1", "02:00:00:00:00:01"
DST_IP, DST_MAC = "10.0.1.5", "02:00:00:00:00:05"
ETH_HEADER_LEN = 14
TTL_OFFSET = 8

_SENT = []


class _FakeSock:
    def send(self, data):
        _SENT.append(data)
        return len(data)

    def close(self):
        pass


def _ip_bytes(ip):
    return bytes(int(octet) for octet in ip.split("."))


def _ipv4(src, dst, ttl, payload=b"hello"):
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


def _forwarded_ttl():
    """The TTL byte of the single frame the router sent, or None if it sent nothing."""
    if not _SENT:
        return None
    return _SENT[-1][ETH_HEADER_LEN + TTL_OFFSET]


def test_high_ttl_forwarded_and_decremented(router, capsys):
    """A packet with TTL to spare is forwarded with its TTL reduced by one."""
    router.process_packet(_ipv4("10.0.2.2", DST_IP, ttl=64))
    assert "[FORWARD]" in capsys.readouterr().out
    assert _forwarded_ttl() == 63


def test_ttl_two_becomes_one(router, capsys):
    """Decrement is exactly one: a TTL of 2 leaves as 1."""
    router.process_packet(_ipv4("10.0.2.2", DST_IP, ttl=2))
    assert "[FORWARD]" in capsys.readouterr().out
    assert _forwarded_ttl() == 1


def test_ttl_one_is_dropped(router, capsys):
    """A packet on its last hop (TTL 1) is dropped, not forwarded."""
    router.process_packet(_ipv4("10.0.2.2", DST_IP, ttl=1))
    out = capsys.readouterr().out
    assert "[FORWARD]" not in out and "[DROP]" in out
    assert _SENT == []


def test_local_delivery_ignores_ttl(router, capsys):
    """A packet addressed to the router is delivered even with TTL 1 — TTL is a transit rule."""
    router.process_packet(_ipv4("10.0.2.2", ROUTER_IP, ttl=1))
    out = capsys.readouterr().out
    assert "[DELIVER]" in out
    assert "[FORWARD]" not in out and _SENT == []


def test_no_route_still_dropped(router, capsys):
    """Base behaviour still holds: a packet with no route is dropped, not forwarded."""
    router.process_packet(_ipv4("10.0.2.2", "9.9.9.9", ttl=64))
    out = capsys.readouterr().out
    assert "[DROP]" in out and "[FORWARD]" not in out
    assert _SENT == []
