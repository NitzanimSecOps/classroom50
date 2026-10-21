"""Tests for 8.1.2.2 Pounce — traceroute() built on Scapy.

No real network: Scapy's sr1/sr, DNS and Scapy's own traceroute() are replaced by a small
simulated internet. Routers drop a probe whose TTL runs out and answer with ICMP Time Exceeded
(type 11); the destination answers a probe that reaches it (echo-reply to ICMP, port-unreachable
to UDP, SYN/ACK to TCP). Probes and answers are real Scapy packets, so the student's code runs
here exactly as it does on the VM. It is imported fresh for every test.
"""
import importlib
import socket
import subprocess
import sys

import pytest

try:
    import scapy.all as scapy_all
except ImportError:  # the grading runner is ephemeral and has no Scapy yet
    cmd = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check", "scapy"]
    if subprocess.run(cmd, check=False).returncode != 0:
        subprocess.run(cmd + ["--break-system-packages"], check=False)
    import scapy.all as scapy_all
import scapy.layers.inet as scapy_inet
import scapy.sendrecv as scapy_sendrecv
from scapy.all import ICMP, IP, TCP, UDP

CLIENT = "192.168.1.50"
MAX_PROBES = 400  # a loop that never stops dies here instead of hanging the grader

ROUTE_A = ["192.168.1.1", "10.20.0.1", "172.16.5.9", "203.0.113.1"]
DEST_A = "198.51.100.7"
ROUTE_B = ["10.0.0.1", "100.64.3.2"]
DEST_B = "192.0.2.44"


class Net:
    """The simulated internet between the student and `dest`."""

    def __init__(self, routers, dest, silent=(), dest_silent=False):
        self.routers = list(routers)
        self.dest = dest
        self.silent = set(silent)  # indexes of routers that never answer
        self.dest_silent = dest_silent
        self.ttls = []  # the TTL of every probe the student sent

    def respond(self, probe):
        if len(self.ttls) >= MAX_PROBES:
            raise AssertionError(f"traceroute() sent more than {MAX_PROBES} probes — does it ever stop?")
        ip = probe[IP]
        self.ttls.append(ip.ttl)
        if ip.dst != self.dest:
            return None
        if ip.ttl <= len(self.routers):  # the TTL runs out at this router
            if ip.ttl - 1 in self.silent:
                return None
            return IP(src=self.routers[ip.ttl - 1], dst=CLIENT) / ICMP(type=11, code=0)
        if self.dest_silent:
            return None
        if probe.haslayer(ICMP) and probe[ICMP].type == 8:
            return IP(src=self.dest, dst=CLIENT) / ICMP(type=0, code=0, id=probe[ICMP].id, seq=probe[ICMP].seq)
        if probe.haslayer(UDP):
            return IP(src=self.dest, dst=CLIENT) / ICMP(type=3, code=3)
        if probe.haslayer(TCP):
            return IP(src=self.dest, dst=CLIENT) / TCP(sport=probe[TCP].dport, dport=probe[TCP].sport, flags="SA")
        return None


class Lab:
    """Swaps Scapy's network functions + DNS for a Net, and runs the student's traceroute()."""

    def __init__(self, monkeypatch):
        self.net = None
        self.names = {}
        self.builtin_called = False
        real_getaddrinfo = socket.getaddrinfo

        def getaddrinfo(host, *args, **kwargs):
            if host in self.names:
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (self.names[host], 0))]
            return real_getaddrinfo(host, *args, **kwargs)

        monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
        monkeypatch.setattr(socket, "gethostbyname", lambda host: self.names.get(host, host))
        for module in (scapy_all, scapy_sendrecv, scapy_inet):
            monkeypatch.setattr(module, "sr1", self._sr1, raising=False)
            monkeypatch.setattr(module, "sr", self._sr, raising=False)
        for module in (scapy_all, scapy_inet):
            monkeypatch.setattr(module, "traceroute", self._scapy_traceroute, raising=False)

    def _sr1(self, packets, *args, **kwargs):
        for probe in packets:
            reply = self.net.respond(probe)
            if reply is not None:
                return reply
        return None

    def _sr(self, packets, *args, **kwargs):
        answered, unanswered = [], []
        for probe in packets:
            reply = self.net.respond(probe)
            (answered if reply is not None else unanswered).append((probe, reply) if reply is not None else probe)
        return answered, unanswered

    def _scapy_traceroute(self, *args, **kwargs):
        self.builtin_called = True
        raise AssertionError("Scapy's own traceroute() is not allowed — implement it yourself with TTL and sr1")

    def load(self):
        """Import the student's solution.py fresh and return its traceroute()."""
        sys.modules.pop("solution", None)
        try:
            module = importlib.import_module("solution")
        except ModuleNotFoundError as e:
            if e.name == "solution":
                pytest.fail("solution.py was not found in the repo")
            raise
        function = getattr(module, "traceroute", None)
        assert callable(function) and function.__module__ == "solution", "solution.py does not define traceroute()"
        return function

    def run(self, routers, dest, name=None, target=None, **net_kwargs):
        self.net = Net(routers, dest, **net_kwargs)
        if name:
            self.names[name] = dest
        return self.load()(target or name or dest)


@pytest.fixture
def lab(monkeypatch):
    return Lab(monkeypatch)


def test_defines_traceroute(lab):
    """solution.py defines a function named traceroute()."""
    lab.load()


def test_returns_list_of_strings(lab):
    """traceroute() returns a list of IP address strings."""
    hops = lab.run(ROUTE_A, DEST_A, name="pounce.test")
    assert isinstance(hops, list), f"expected a list, got {type(hops).__name__}"
    assert hops and all(isinstance(h, str) for h in hops), f"expected IP strings, got {hops!r}"


def test_lists_every_router_in_order(lab):
    """The list holds the IP of every router on the way, in the order the packet meets them."""
    hops = lab.run(ROUTE_A, DEST_A, name="pounce.test")
    assert hops[: len(ROUTE_A)] == ROUTE_A, f"expected the routers {ROUTE_A} first, got {hops}"


def test_ends_with_destination(lab):
    """The destination itself is the last item in the list — exactly once."""
    hops = lab.run(ROUTE_A, DEST_A, name="pounce.test")
    assert hops[len(ROUTE_A):] == [DEST_A], f"expected the list to end with [{DEST_A!r}], got {hops}"


def test_other_route(lab):
    """It works on a different, shorter route too — nothing is hard-coded."""
    hops = lab.run(ROUTE_B, DEST_B, name="cats.test")
    assert hops == ROUTE_B + [DEST_B], f"expected {ROUTE_B + [DEST_B]}, got {hops}"


def test_probes_every_ttl(lab):
    """TTL starts at 1 and grows one hop at a time — every TTL up to the destination is probed."""
    lab.run(ROUTE_A, DEST_A, name="pounce.test")
    wanted = set(range(1, len(ROUTE_A) + 2))
    assert wanted <= set(lab.net.ttls), f"TTLs probed: {sorted(set(lab.net.ttls))}, expected at least {sorted(wanted)}"


def test_silent_hop_is_a_star(lab):
    """A router that does not answer shows up as "*" and the trace continues past it."""
    hops = lab.run(ROUTE_A, DEST_A, name="pounce.test", silent=[1])
    expected = [ROUTE_A[0], "*", ROUTE_A[2], ROUTE_A[3], DEST_A]
    assert hops == expected, f"expected {expected}, got {hops}"


def test_gives_up_after_30_hops(lab):
    """If the destination never answers, it stops after 30 hops instead of running forever."""
    hops = lab.run(["192.168.1.1"], DEST_A, name="pounce.test", dest_silent=True)
    expected = ["192.168.1.1"] + ["*"] * 29
    assert hops == expected, f"expected 30 hops ({expected[0]} then 29 stars), got {len(hops)}: {hops}"


def test_accepts_plain_ip(lab):
    """The address can be a plain IP, not only a name like google.com."""
    hops = lab.run(ROUTE_A, DEST_A)
    assert hops == ROUTE_A + [DEST_A], f"expected {ROUTE_A + [DEST_A]}, got {hops}"


def test_does_not_use_scapys_traceroute(lab):
    """It is built from probes you send yourself, not from Scapy's built-in traceroute()."""
    lab.run(ROUTE_A, DEST_A, name="pounce.test")
    assert not lab.builtin_called, "Scapy's own traceroute() was called"
