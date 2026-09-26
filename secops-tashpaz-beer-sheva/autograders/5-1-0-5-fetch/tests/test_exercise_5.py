"""Tests for 5.1.0.5 Fetch — Host.process_packet decides DELIVER / FORWARD / DROP.

drop_packet / deliver_packet / forward_packet are replaced on the host with recorders, so the
tests see which action process_packet chose and with what arguments (however it passes them).
host.py imports the student's ipv4.py, routing.py and arp.py, so those must be in the repo.
"""
import inspect
import struct

import pytest

from host import Host
from routing import RouteEntry

HOST_IP = "192.168.1.10"
HOST_MAC = "D4:92:5E:B8:99:37"
PEER_IP, PEER_MAC = "192.168.1.20", "AA:AA:AA:AA:AA:20"          # same LAN
ROUTER_IP, ROUTER_MAC = "192.168.1.254", "AA:AA:AA:AA:AA:FE"     # gateway to 10.0.0.0/8
LAB_IP, LAB_MAC = "10.1.2.30", "BB:BB:BB:BB:BB:30"               # directly on eth1
LOST_ROUTER_IP = "192.168.1.253"                                  # gateway with no ARP entry
PAYLOAD = b"a bone for Muj"
ACTIONS = ("drop_packet", "deliver_packet", "forward_packet")


def ip_to_bytes(ip: str) -> bytes:
    return bytes(int(part) for part in ip.split("."))


def build_packet(dst: str, payload: bytes = PAYLOAD, src: str = "172.20.0.5",
                 version: int = 4) -> bytes:
    total_length = 20 + len(payload)
    header = struct.pack("!BBHHHBBH4s4s", (version << 4) | 5, 0, total_length, 0x1c46, 0x4000,
                         64, 17, 0, ip_to_bytes(src), ip_to_bytes(dst))
    return header + payload


@pytest.fixture
def host(monkeypatch):
    host = Host(HOST_IP, HOST_MAC)
    for entry in (RouteEntry("192.168.1.0", "255.255.255.0", None, "eth0"),
                  RouteEntry("10.0.0.0", "255.0.0.0", ROUTER_IP, "eth0"),
                  RouteEntry("10.1.2.0", "255.255.255.0", None, "eth1"),
                  RouteEntry("172.16.0.0", "255.255.0.0", LOST_ROUTER_IP, "eth0")):
        host.routing_table.add_route(entry)
    for ip, mac in ((PEER_IP, PEER_MAC), (ROUTER_IP, ROUTER_MAC), (LAB_IP, LAB_MAC)):
        host.arp_table.learn(ip, mac)

    host.calls = []
    for name in ACTIONS:
        signature = inspect.signature(getattr(host, name))

        def record(*args, _name=name, _signature=signature, **kwargs):
            bound = _signature.bind(*args, **kwargs)
            bound.apply_defaults()
            host.calls.append((_name, dict(bound.arguments)))

        monkeypatch.setattr(host, name, record)
    return host


def only_call(host) -> tuple[str, dict]:
    assert len(host.calls) == 1, (
        f"expected exactly one of {ACTIONS} to be called, got {[name for name, _ in host.calls]}"
    )
    return host.calls[0]


def assert_forwarded(host, raw: bytes, next_mac: str, interface: str) -> None:
    name, args = only_call(host)
    assert name == "forward_packet", f"expected forward_packet, got {name}"
    got = (args["raw_bytes"], args["next_mac"], args["interface"])
    assert got == (raw, next_mac, interface), (
        f"forward_packet(raw_bytes, next_mac, interface): expected the original packet, "
        f"{next_mac!r}, {interface!r}; got {got[1]!r}, {got[2]!r} "
        f"(raw_bytes {'unchanged' if got[0] == raw else 'CHANGED'})"
    )


def assert_dropped(host) -> None:
    name, _ = only_call(host)
    assert name == "drop_packet", f"expected drop_packet, got {name}"


def test_delivers_packet_for_this_host(host):
    """A packet addressed to the host's own IP is delivered with its payload."""
    host.process_packet(build_packet(HOST_IP))
    name, args = only_call(host)
    assert name == "deliver_packet", f"expected deliver_packet, got {name}"
    assert args["payload"] == PAYLOAD, f"payload: expected {PAYLOAD!r}, got {args['payload']!r}"


def test_forwards_to_neighbor_on_same_network(host):
    """A packet for a directly connected neighbor is forwarded to the neighbor's own MAC."""
    raw = build_packet(PEER_IP)
    host.process_packet(raw)
    assert_forwarded(host, raw, PEER_MAC, "eth0")


def test_forwards_through_gateway(host):
    """A packet for a remote network is forwarded to the gateway's MAC (next_hop), not the destination's."""
    raw = build_packet("10.9.9.9")
    host.process_packet(raw)
    assert_forwarded(host, raw, ROUTER_MAC, "eth0")


def test_forwards_by_longest_prefix(host):
    """The most specific matching route decides the interface and next hop."""
    raw = build_packet(LAB_IP)
    host.process_packet(raw)
    assert_forwarded(host, raw, LAB_MAC, "eth1")


def test_uses_default_route(host):
    """With a default route, a packet to an unknown network goes to the default gateway."""
    host.routing_table.add_route(RouteEntry("0.0.0.0", "0.0.0.0", "192.168.1.1", "eth0"))
    host.arp_table.learn("192.168.1.1", "AA:AA:AA:AA:AA:01")
    raw = build_packet("8.8.8.8")
    host.process_packet(raw)
    assert_forwarded(host, raw, "AA:AA:AA:AA:AA:01", "eth0")


def test_drops_without_route(host):
    """A packet with no matching route is dropped."""
    host.process_packet(build_packet("8.8.8.8"))
    assert_dropped(host)


def test_drops_when_neighbor_mac_unknown(host):
    """A packet for a neighbor missing from the ARP table is dropped."""
    host.process_packet(build_packet("192.168.1.99"))
    assert_dropped(host)


def test_drops_when_gateway_mac_unknown(host):
    """A packet whose gateway is missing from the ARP table is dropped."""
    host.process_packet(build_packet("172.16.5.5"))
    assert_dropped(host)


def test_drops_non_ipv4_packet(host):
    """A packet whose version is not 4 is dropped."""
    host.process_packet(build_packet(PEER_IP, version=6))
    assert_dropped(host)


def test_drops_too_short_packet(host):
    """A packet shorter than an IPv4 header (20 bytes) is dropped without crashing."""
    host.process_packet(build_packet(PEER_IP)[:12])
    assert_dropped(host)
