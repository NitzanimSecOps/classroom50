"""Tests for 5.1.0.3 Longest Leash — ip_matches_route and longest-prefix lookup.

Routes are built inside a fixture (not at import time), so a broken RouteEntry fails each
test on its own instead of the whole file.
"""
import pytest

from routing import RouteEntry, RoutingTable

ROUTES = {
    "default": ("0.0.0.0", "0.0.0.0", "192.168.1.254", "eth0"),
    "net_8": ("10.0.0.0", "255.0.0.0", None, "eth1"),
    "net_16": ("10.1.0.0", "255.255.0.0", "10.0.0.2", "eth1"),
    "net_24": ("10.1.2.0", "255.255.255.0", "10.0.0.3", "eth2"),
    # A mask that doesn't end on a byte boundary: 172.16.0.0 - 172.16.15.255.
    "net_20": ("172.16.0.0", "255.255.240.0", None, "eth3"),
}


def make_table(*entries: RouteEntry) -> RoutingTable:
    table = RoutingTable()
    for entry in entries:
        table.add_route(entry)
    return table


@pytest.fixture
def r():
    """The routes by name, as RouteEntry objects."""
    return {name: RouteEntry(*fields) for name, fields in ROUTES.items()}


@pytest.fixture
def table(r):
    return make_table(*r.values())


def test_matches_ip_inside_network(table, r):
    """ip_matches_route() returns True for an IP inside the route's network."""
    assert table.ip_matches_route("10.1.2.77", r["net_24"]) is True


def test_does_not_match_ip_outside_network(table, r):
    """ip_matches_route() returns False for an IP outside the route's network."""
    assert table.ip_matches_route("10.1.3.77", r["net_24"]) is False


def test_matches_with_short_masks(table, r):
    """ip_matches_route() works with /8 and /16 masks."""
    assert table.ip_matches_route("10.200.3.4", r["net_8"]) is True
    assert table.ip_matches_route("10.1.99.4", r["net_16"]) is True
    assert table.ip_matches_route("10.2.99.4", r["net_16"]) is False
    assert table.ip_matches_route("11.0.0.1", r["net_8"]) is False


def test_matches_mask_not_on_byte_boundary(table, r):
    """ip_matches_route() works with a mask like 255.255.240.0 (bitmasking, not string compare)."""
    assert table.ip_matches_route("172.16.15.200", r["net_20"]) is True
    assert table.ip_matches_route("172.16.16.1", r["net_20"]) is False


def test_default_route_matches_everything(table, r):
    """The default route 0.0.0.0/0.0.0.0 matches any IP."""
    for ip in ("8.8.8.8", "10.1.2.3", "255.255.255.255", "0.0.0.0"):
        assert table.ip_matches_route(ip, r["default"]) is True, f"{ip} should match the default route"


def test_lookup_picks_longest_prefix(table, r):
    """lookup() returns the matching route with the longest mask."""
    assert table.lookup("10.1.2.50") is r["net_24"]


def test_lookup_falls_back_to_shorter_prefix(table, r):
    """lookup() falls back to a shorter mask when the longer ones don't match."""
    assert table.lookup("10.1.9.9") is r["net_16"]
    assert table.lookup("10.9.8.7") is r["net_8"]


def test_lookup_falls_back_to_default_route(table, r):
    """lookup() returns the default route when nothing more specific matches."""
    assert table.lookup("8.8.8.8") is r["default"]


def test_lookup_ignores_insertion_order(r):
    """lookup() finds the longest prefix whatever order the routes were added in."""
    backwards = make_table(r["net_24"], r["net_16"], r["net_8"], r["default"])
    assert backwards.lookup("10.1.2.50") is r["net_24"]
    assert backwards.lookup("10.9.8.7") is r["net_8"]


def test_lookup_returns_none_without_match(r):
    """lookup() returns None when no route matches (no default route, or an empty table)."""
    no_default = make_table(r["net_8"], r["net_24"])
    assert no_default.lookup("10.1.2.50") is r["net_24"]
    assert no_default.lookup("192.168.7.7") is None
    assert RoutingTable().lookup("10.0.0.1") is None
