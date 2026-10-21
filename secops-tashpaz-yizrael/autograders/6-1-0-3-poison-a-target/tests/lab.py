#!/usr/bin/env python3
"""Build the isolated 3-party layer-2 lab the ARP exercises are graded in.

Topology (all in 10.0.0.0/24, one broadcast domain):

    victim  (ns) --veth--+
                         +-- br0  (dumb L2 bridge, root ns; no proxy_arp)
    gateway (ns) --veth--+
                         |
    attacker(ns) --veth--+   <- the student's code runs here

Runs as root inside the grading sandbox / the per-student lab container. build() is
idempotent (tears down first). The bridge is a plain learning switch, so victim and
gateway reach each other directly and each caches the OTHER's real MAC — a shared LAN.
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass

BRIDGE = "br0"


@dataclass(frozen=True)
class Endpoint:
    name: str        # netns name, e.g. "victim"
    host_if: str     # iface inside the ns, e.g. "v_victim"
    br_if: str       # peer iface on the bridge, e.g. "b_victim"
    ip: str          # "10.0.0.2"
    mac: str         # "02:00:00:00:00:02"


VICTIM = Endpoint("victim", "v_victim", "b_victim", "10.0.0.2", "02:00:00:00:00:02")
GATEWAY = Endpoint("gateway", "v_gateway", "b_gateway", "10.0.0.1", "02:00:00:00:00:01")
ATTACKER = Endpoint("attacker", "v_attacker", "b_attacker", "10.0.0.66", "02:00:00:00:00:66")
ENDPOINTS = (VICTIM, GATEWAY, ATTACKER)
PREFIX = 24


def run(*args: str, ns: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    cmd = (["ip", "netns", "exec", ns] if ns else []) + list(args)
    return subprocess.run(cmd, check=check, capture_output=True, text=True)


def _quiet(*args: str, ns: str | None = None) -> None:
    run(*args, ns=ns, check=False)


def teardown() -> None:
    for ep in ENDPOINTS:
        _quiet("ip", "netns", "del", ep.name)
        _quiet("ip", "link", "del", ep.br_if)
    _quiet("ip", "link", "del", BRIDGE)


def build(*, suppress_attacker_arp: bool = False) -> None:
    teardown()
    run("ip", "link", "add", BRIDGE, "type", "bridge")
    run("sysctl", "-qw", f"net.ipv4.conf.{BRIDGE}.proxy_arp=0")
    run("ip", "link", "set", BRIDGE, "up")

    for ep in ENDPOINTS:
        run("ip", "netns", "add", ep.name)
        run("ip", "link", "add", ep.host_if, "type", "veth", "peer", "name", ep.br_if)
        run("ip", "link", "set", ep.br_if, "master", BRIDGE)
        run("ip", "link", "set", ep.br_if, "up")
        run("ip", "link", "set", ep.host_if, "netns", ep.name)
        run("ip", "link", "set", ep.host_if, "address", ep.mac, ns=ep.name)
        run("ip", "addr", "add", f"{ep.ip}/{PREFIX}", "dev", ep.host_if, ns=ep.name)
        run("ip", "link", "set", "lo", "up", ns=ep.name)
        run("ip", "link", "set", ep.host_if, "up", ns=ep.name)
        _quiet("ethtool", "-K", ep.host_if, "tx", "off", "rx", "off", ns=ep.name)

    # Docker leaves net.ipv4.ip_forward inherited-ON in every fresh netns; force it OFF
    # in the attacker ns so the MITM level has to enable forwarding itself. Kept here too
    # so every level's lab is identical.
    run("sysctl", "-qw", "net.ipv4.ip_forward=0", ns=ATTACKER.name)

    if suppress_attacker_arp:
        a = ATTACKER
        run("sysctl", "-qw", f"net.ipv4.conf.{a.host_if}.arp_ignore=8", ns=a.name)
        run("sysctl", "-qw", f"net.ipv4.conf.{a.host_if}.arp_announce=2", ns=a.name)


def neigh_mac(ns: str, ip: str) -> str | None:
    out = run("ip", "neigh", "show", ip, ns=ns, check=False).stdout
    for line in out.splitlines():
        parts = line.split()
        if "lladdr" in parts and parts[-1] not in ("FAILED", "INCOMPLETE"):
            return parts[parts.index("lladdr") + 1].lower()
    return None
