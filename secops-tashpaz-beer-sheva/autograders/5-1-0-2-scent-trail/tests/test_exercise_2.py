"""Tests for 5.1.0.2 Scent Trail — RouteEntry and RoutingTable.

__str__ is free-form ("a readable format"), so its tests only check that every value of
every route shows up, one route per line.
"""
import pytest

from routing import RouteEntry, RoutingTable

# (network, mask, next_hop, interface) — built into RouteEntry objects inside the tests, so a
# broken RouteEntry fails each test on its own instead of the whole file.
LAN = ("192.168.1.0", "255.255.255.0", None, "eth0")
DEFAULT = ("0.0.0.0", "0.0.0.0", "192.168.1.1", "eth0")
BRANCH = ("172.16.0.0", "255.255.0.0", "192.168.1.254", "eth1")


def make_entry(fields: tuple) -> RouteEntry:
    network, mask, next_hop, interface = fields
    return RouteEntry(network=network, mask=mask, next_hop=next_hop, interface=interface)


@pytest.fixture
def routes():
    return [make_entry(fields) for fields in (LAN, DEFAULT, BRANCH)]


@pytest.fixture
def table(routes):
    table = RoutingTable()
    for entry in routes:
        table.add_route(entry)
    return table


def test_route_entry_fields():
    """RouteEntry keeps network, mask, next_hop and interface."""
    entry = RouteEntry(network="10.0.0.0", mask="255.0.0.0", next_hop="192.168.1.1",
                       interface="eth2")
    got = (entry.network, entry.mask, entry.next_hop, entry.interface)
    expected = ("10.0.0.0", "255.0.0.0", "192.168.1.1", "eth2")
    assert got == expected, f"(network, mask, next_hop, interface): expected {expected}, got {got}"


def test_route_entry_positional_order():
    """RouteEntry takes its fields in the order network, mask, next_hop, interface."""
    entry = RouteEntry("10.0.0.0", "255.0.0.0", "192.168.1.1", "eth2")
    got = (entry.network, entry.mask, entry.next_hop, entry.interface)
    expected = ("10.0.0.0", "255.0.0.0", "192.168.1.1", "eth2")
    assert got == expected, f"(network, mask, next_hop, interface): expected {expected}, got {got}"


def test_route_entry_without_next_hop():
    """A directly connected route has next_hop None."""
    entry = make_entry(LAN)
    assert entry.next_hop is None, f"next_hop: expected None, got {entry.next_hop!r}"


def test_new_table_is_empty():
    """A new RoutingTable starts with an empty entries list."""
    entries = RoutingTable().entries
    assert entries == [], f"entries: expected [], got {entries!r}"


def test_add_route_keeps_order(table, routes):
    """add_route() appends each RouteEntry to entries, in the order added."""
    assert table.entries == routes, f"entries: expected {routes!r}, got {table.entries!r}"


def test_tables_do_not_share_entries():
    """Two RoutingTable objects keep separate entries."""
    first, second = RoutingTable(), RoutingTable()
    first.add_route(make_entry(LAN))
    assert second.entries == [], (
        "adding to one table changed another — create the list in __init__, not on the class"
    )


def test_str_returns_a_string(table):
    """str(table) returns a string."""
    assert isinstance(str(table), str)


def test_str_shows_every_value(table):
    """str(table) shows the network, mask, next hop and interface of every route."""
    text = str(table)
    for value in ("192.168.1.0", "255.255.255.0", "eth0", "0.0.0.0", "192.168.1.1",
                  "172.16.0.0", "255.255.0.0", "192.168.1.254", "eth1"):
        assert value in text, f"{value!r} is missing from the printed table:\n{text}"


def test_str_one_route_per_line(table):
    """str(table) prints each route on its own line."""
    lines = str(table).splitlines()
    for network, _, _, interface in (LAN, BRANCH):
        matching = [line for line in lines if network in line]
        assert len(matching) == 1 and interface in matching[0], (
            f"expected one line with {network} and {interface}, got {matching!r}"
        )
