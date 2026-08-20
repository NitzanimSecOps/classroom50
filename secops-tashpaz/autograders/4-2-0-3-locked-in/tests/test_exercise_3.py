import struct

import pytest

from switch import MacEntry, Port, Switch

PORTS = ["eth0", "eth1", "eth2", "eth3"]
BROADCAST_MAC = "FF:FF:FF:FF:FF:FF"
HOST_A = "A1:2F:3F:BE:6C:12"
HOST_B = "B2:CC:DD:EE:6F:71"


def _mac_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def make_frame(src: str, dst: str, ethertype: int = 0x0800, payload: bytes = b"payload") -> bytes:
    """Build a raw frame laid out as [dst mac][src mac][ethertype][payload]."""
    return _mac_bytes(dst) + _mac_bytes(src) + struct.pack("!H", ethertype) + payload


@pytest.fixture
def switch():
    return Switch(list(PORTS))


# --- Part 1: getting comfortable with @dataclass ----------------------------
# A dataclass writes __init__, __repr__ and __eq__ for you from the fields you
# declare. These tests show what that buys us compared to a plain tuple.

def test_port_is_built_from_a_name():
    # The @dataclass gives Port an __init__ that takes its fields in order.
    port = Port("eth0")
    assert port.name == "eth0", (
        f"Port should expose its interface name via .name, got {port.name!r}"
    )


def test_mac_entry_exposes_named_fields():
    # Instead of entry[0] / entry[1] (a tuple), a dataclass gives real names.
    entry = MacEntry(HOST_A, "eth0")
    assert entry.mac == HOST_A, f"expected .mac == {HOST_A!r}, got {entry.mac!r}"
    assert entry.port == "eth0", f"expected .port == 'eth0', got {entry.port!r}"


def test_dataclass_equality_is_by_value():
    # @dataclass generates __eq__, so two entries with the same field values
    # are equal -- something plain objects do NOT give you.
    assert MacEntry(HOST_A, "eth0") == MacEntry(HOST_A, "eth0"), (
        "two MacEntry values with the same fields should be equal"
    )
    assert MacEntry(HOST_A, "eth0") != MacEntry(HOST_A, "eth1"), (
        "MacEntry values differing in port should not be equal"
    )


# --- Part 2: the switch now uses those dataclasses --------------------------

def test_switch_ports_are_port_objects(switch):
    assert switch.ports == [Port(name) for name in PORTS], (
        f"switch.ports should be a list of Port objects, got {switch.ports!r}"
    )


def test_learning_stores_a_mac_entry(switch):
    switch.process_frame(make_frame(src=HOST_A, dst=HOST_B), Port("eth0"))
    assert MacEntry(HOST_A, Port("eth0")) in switch.mac_table, (
        f"process_frame should learn a MacEntry(mac, port); mac_table = {switch.mac_table!r}"
    )


def test_moved_host_leaves_one_entry(switch):
    switch.process_frame(make_frame(src=HOST_A, dst=HOST_B), Port("eth0"))
    switch.process_frame(make_frame(src=HOST_A, dst=HOST_B), Port("eth3"))
    entries_for_a = [entry for entry in switch.mac_table if entry.mac == HOST_A]
    assert entries_for_a == [MacEntry(HOST_A, Port("eth3"))], (
        f"a moved host should leave exactly one updated entry, got {entries_for_a!r}"
    )


# --- Part 3: forwarding behavior is unchanged from exercise 2 ---------------

def test_unknown_unicast_still_floods(switch):
    # process_frame now returns Port objects, not name strings.
    result = switch.process_frame(make_frame(src=HOST_A, dst=HOST_B), Port("eth0"))
    assert sorted([port for port in result], key=lambda port: port.name) == [Port("eth1"), Port("eth2"), Port("eth3")], (
        f"an unknown unicast should still flood all ports except the incoming one, got {result}"
    )


def test_known_unicast_still_forwards_to_learned_port(switch):
    switch.process_frame(make_frame(src=HOST_B, dst=HOST_A), Port("eth2"))
    result = switch.process_frame(make_frame(src=HOST_A, dst=HOST_B), Port("eth0"))
    assert result == [Port("eth2")], (
        f"a known unicast should still forward only to its learned port [Port('eth2')], got {result}"
    )
