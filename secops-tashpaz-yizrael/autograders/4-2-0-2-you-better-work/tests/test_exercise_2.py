import struct

import pytest

from switch import Switch

PORTS = ["eth0", "eth1", "eth2", "eth3"]
BROADCAST_MAC = "FF:FF:FF:FF:FF:FF"
HOST_A = "A1:2F:3F:BE:6C:12"
HOST_B = "B2:CC:DD:EE:6F:71"


def _mac_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def make_frame(src: str, dst: str, ethertype: int = 0x0800, payload: bytes = b"payload") -> bytes:
    """Build a raw frame laid out as [dst mac][src mac][ethertype][payload],
    matching how the Ethernet class parses its fields."""
    return _mac_bytes(dst) + _mac_bytes(src) + struct.pack("!H", ethertype) + payload


@pytest.fixture
def switch():
    return Switch(list(PORTS))


def learned_port(switch, mac):
    """Return the port learned for `mac` in the (mac, port) tuple table, or None."""
    return next((port for entry_mac, port in switch.mac_table if entry_mac == mac), None)


# --- Learning ---------------------------------------------------------------

def test_process_frame_learns_source_mac(switch):
    switch.process_frame(make_frame(src=HOST_A, dst=HOST_B), "eth0")
    assert learned_port(switch, HOST_A) == "eth0", (
        f"process_frame should learn the source MAC on its incoming port: "
        f"expected {HOST_A} -> 'eth0', got {learned_port(switch, HOST_A)!r}"
    )


def test_process_frame_updates_port_when_host_moves(switch):
    # Host A first seen on eth0...
    switch.process_frame(make_frame(src=HOST_A, dst=HOST_B), "eth0")
    # ...then the same host appears on eth3 (moved / cable re-plugged).
    switch.process_frame(make_frame(src=HOST_A, dst=HOST_B), "eth3")
    assert learned_port(switch, HOST_A) == "eth3", (
        f"process_frame should update the learned port when a host moves: "
        f"expected {HOST_A} -> 'eth3', got {learned_port(switch, HOST_A)!r}"
    )
    entries_for_a = [entry for entry in switch.mac_table if entry[0] == HOST_A]
    assert len(entries_for_a) == 1, (
        f"a moved host should have exactly one entry (old one replaced), got {entries_for_a}"
    )


# --- Forwarding decision ----------------------------------------------------

def test_unknown_unicast_floods_all_but_incoming(switch):
    # Destination B has never been seen -> flood everywhere except the sender's port.
    result = switch.process_frame(make_frame(src=HOST_A, dst=HOST_B), "eth0")
    expected = ["eth1", "eth2", "eth3"]
    assert sorted(result) == sorted(expected), (
        f"an unknown unicast should flood all ports except the incoming one; "
        f"expected {expected}, got {result}"
    )


def test_broadcast_floods_all_but_incoming(switch):
    result = switch.process_frame(make_frame(src=HOST_A, dst=BROADCAST_MAC), "eth1")
    expected = ["eth0", "eth2", "eth3"]
    assert sorted(result) == sorted(expected), (
        f"a broadcast should flood all ports except the incoming one; "
        f"expected {expected}, got {result}"
    )


def test_known_unicast_forwards_to_learned_port(switch):
    # Teach the switch where Host B is by having B send a frame in on eth2.
    switch.process_frame(make_frame(src=HOST_B, dst=HOST_A), "eth2")
    # Now Host A (on eth0) sends to the now-known Host B.
    result = switch.process_frame(make_frame(src=HOST_A, dst=HOST_B), "eth0")
    assert result == ["eth2"], (
        f"a known unicast should be forwarded only to its learned port ['eth2'], got {result}"
    )


def test_known_unicast_on_incoming_port_is_filtered(switch):
    # Host B is learned on eth0...
    switch.process_frame(make_frame(src=HOST_B, dst=HOST_A), "eth0")
    # ...and a frame for Host B also arrives on eth0 -> must not be sent back out.
    result = switch.process_frame(make_frame(src=HOST_A, dst=HOST_B), "eth0")
    assert result == [], (
        f"a frame must never be sent back out its incoming port; expected [], got {result}"
    )
