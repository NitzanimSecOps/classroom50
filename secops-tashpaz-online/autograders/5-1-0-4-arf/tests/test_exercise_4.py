"""Tests for 5.1.0.4 Arf — ARPTable and Host.

host.py imports the student's own routing.py (from 5.1.0.3), so that file must be in the repo.
"""
from arp import ARPTable
from host import Host
from routing import RouteEntry, RoutingTable

GATEWAY_IP = "192.168.1.1"
GATEWAY_MAC = "AA:BB:CC:DD:EE:01"
HOST_IP = "192.168.1.10"
HOST_MAC = "D4:92:5E:B8:99:37"


def test_new_arp_table_is_empty():
    """A new ARPTable starts with an empty table dict."""
    table = ARPTable().table
    assert table == {}, f"table: expected {{}}, got {table!r}"


def test_learn_then_lookup():
    """lookup() returns the MAC that learn() stored for an IP."""
    arp = ARPTable()
    arp.learn(GATEWAY_IP, GATEWAY_MAC)
    assert arp.lookup(GATEWAY_IP) == GATEWAY_MAC


def test_learn_stores_ip_to_mac():
    """learn() stores the entry in the table dict as ip -> mac."""
    arp = ARPTable()
    arp.learn(GATEWAY_IP, GATEWAY_MAC)
    assert arp.table == {GATEWAY_IP: GATEWAY_MAC}, (
        f"table: expected {{{GATEWAY_IP!r}: {GATEWAY_MAC!r}}}, got {arp.table!r}"
    )


def test_lookup_unknown_ip_returns_none():
    """lookup() returns None for an IP that was never learned (no exception)."""
    arp = ARPTable()
    arp.learn(GATEWAY_IP, GATEWAY_MAC)
    assert arp.lookup(GATEWAY_IP) == GATEWAY_MAC
    assert arp.lookup("192.168.1.99") is None


def test_learn_overwrites_old_mac():
    """Learning an IP again replaces its old MAC."""
    arp = ARPTable()
    arp.learn(GATEWAY_IP, GATEWAY_MAC)
    arp.learn(GATEWAY_IP, "AA:BB:CC:DD:EE:02")
    assert arp.lookup(GATEWAY_IP) == "AA:BB:CC:DD:EE:02"


def test_arp_tables_do_not_share_entries():
    """Two ARPTable objects keep separate tables."""
    first, second = ARPTable(), ARPTable()
    first.learn(GATEWAY_IP, GATEWAY_MAC)
    assert first.lookup(GATEWAY_IP) == GATEWAY_MAC
    assert second.lookup(GATEWAY_IP) is None, (
        "learning in one table changed another — create the dict in __init__, not on the class"
    )


def test_host_keeps_its_addresses():
    """Host(ip, mac) stores its own ip and mac."""
    host = Host(HOST_IP, HOST_MAC)
    assert (host.ip, host.mac) == (HOST_IP, HOST_MAC), (
        f"(ip, mac): expected {(HOST_IP, HOST_MAC)}, got {(host.ip, host.mac)}"
    )


def test_host_has_empty_routing_table():
    """A new Host has an empty RoutingTable in routing_table."""
    host = Host(HOST_IP, HOST_MAC)
    assert isinstance(host.routing_table, RoutingTable), (
        f"routing_table: expected a RoutingTable, got {type(host.routing_table).__name__}"
    )
    assert host.routing_table.entries == []


def test_host_has_empty_arp_table():
    """A new Host has an empty ARPTable in arp_table."""
    host = Host(HOST_IP, HOST_MAC)
    assert isinstance(host.arp_table, ARPTable), (
        f"arp_table: expected an ARPTable, got {type(host.arp_table).__name__}"
    )
    assert host.arp_table.table == {}


def test_hosts_do_not_share_tables():
    """Each Host gets its own routing table and ARP table."""
    first, second = Host(HOST_IP, HOST_MAC), Host("192.168.1.11", "D4:92:5E:B8:99:38")
    assert first.routing_table is not second.routing_table
    assert first.arp_table is not second.arp_table


def test_host_tables_work_together():
    """A host can route to a gateway and find the gateway's MAC."""
    host = Host(HOST_IP, HOST_MAC)
    host.routing_table.add_route(RouteEntry("0.0.0.0", "0.0.0.0", GATEWAY_IP, "eth0"))
    host.arp_table.learn(GATEWAY_IP, GATEWAY_MAC)
    route = host.routing_table.lookup("8.8.8.8")
    assert route is not None and host.arp_table.lookup(route.next_hop) == GATEWAY_MAC
