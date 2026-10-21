"""Tests for 8.1.2.3 Knock Knock — knock(ip, port, proto) built on Scapy.

No real network: Scapy's sr1/srp1 are replaced by a simulated "door" host. Each of its ports
behaves one way (open, closed, dropped, rejected with ICMP, ...) and answers with real Scapy
packets. Every reply is re-dissected from bytes and must pass Scapy's own reply.answers(probe),
exactly as the real sr1 matches it — so what fails in the lab fails here too.
The student's solution.py is imported fresh for every test.
"""
import importlib
import random
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
import scapy.sendrecv as scapy_sendrecv
from scapy.all import ICMP, IP, TCP, UDP, Ether, Raw

DOOR = "10.0.0.2"
KNOCK = b"knock knock"
ANSWERS = {"syn-ack", "rst", "data", "port-unreachable", "icmp-other", "none"}
MAX_PROBES = 20  # a loop that never stops dies here instead of hanging the grader

# (proto, port) -> behaviour; any port not listed is closed
PORTS = {
    ("tcp", 80): "open",
    ("tcp", 8080): "closed",
    ("tcp", 443): "drop",
    ("tcp", 25): "reject",
    ("tcp", 445): "prohibited",
    ("udp", 7777): "open",
    ("udp", 9999): "closed",
    ("udp", 53): "drop",
    ("udp", 6000): "silent",
}


def icmp_error(probe, type_, code):
    """ICMP error from the door quoting the probe's IP header + 8 bytes."""
    return IP(src=probe[IP].dst, dst=probe[IP].src) / ICMP(type=type_, code=code) / bytes(probe[IP])[:28]


class Door:
    """The simulated target host."""

    def __init__(self, ip=DOOR, ports=None):
        self.ip = ip
        self.ports = dict(PORTS if ports is None else ports)
        self.probes = []  # every packet the student sent
        self.calls = []  # the kwargs of every sr1/srp1 call

    def respond(self, probe):
        if len(self.probes) >= MAX_PROBES:
            raise AssertionError(f"knock() sent more than {MAX_PROBES} probes — does it ever stop?")
        self.probes.append(probe)
        if not probe.haslayer(IP) or probe[IP].dst != self.ip:
            return None
        if probe.haslayer(TCP):
            proto, l4 = "tcp", probe[TCP]
        elif probe.haslayer(UDP):
            proto, l4 = "udp", probe[UDP]
        else:
            return None
        state = self.ports.get((proto, l4.dport), "closed")
        if state in ("drop", "silent"):
            return None
        if state == "reject":
            return icmp_error(probe, 3, 3)
        if state == "prohibited":
            return icmp_error(probe, 3, 13)
        if proto == "tcp":
            if state == "open" and l4.flags == "S":
                return IP(src=self.ip, dst=probe[IP].src) / TCP(sport=l4.dport, dport=l4.sport, flags="SA", ack=l4.seq + 1)
            return IP(src=self.ip, dst=probe[IP].src) / TCP(sport=l4.dport, dport=l4.sport, flags="RA", ack=l4.seq + 1)
        if state == "closed":
            return icmp_error(probe, 3, 3)
        if probe.haslayer(Raw) and probe[Raw].load == KNOCK:  # open: the service only answers the knock
            return IP(src=self.ip, dst=probe[IP].src) / UDP(sport=l4.dport, dport=l4.sport) / Raw(b"who's there?")
        return None


class Lab:
    """Swaps Scapy's sr1/srp1 for a Door and runs the student's knock()."""

    def __init__(self, monkeypatch):
        self.door = Door()
        for module in (scapy_all, scapy_sendrecv):
            monkeypatch.setattr(module, "sr1", self._sr1, raising=False)
            monkeypatch.setattr(module, "srp1", self._srp1, raising=False)

    def _first_reply(self, packets, kwargs):
        self.door.calls.append(kwargs)
        for probe in packets:
            reply = self.door.respond(probe)
            if reply is None:
                continue
            reply = IP(bytes(reply))  # dissect it the way it arrives off the wire
            if reply.answers(probe):
                return reply
        return None

    def _sr1(self, packets, *args, **kwargs):
        return self._first_reply(packets, kwargs)

    def _srp1(self, packets, *args, **kwargs):
        reply = self._first_reply(packets, kwargs)
        return None if reply is None else Ether() / reply

    def load(self):
        """Import the student's solution.py fresh and return its knock()."""
        sys.modules.pop("solution", None)
        try:
            module = importlib.import_module("solution")
        except ModuleNotFoundError as e:
            if e.name == "solution":
                pytest.fail("solution.py was not found in the repo")
            raise
        function = getattr(module, "knock", None)
        assert callable(function) and function.__module__ == "solution", "solution.py does not define knock()"
        return function

    def knock(self, port, proto, ip=None):
        return self.load()(ip or self.door.ip, port, proto)


@pytest.fixture
def lab(monkeypatch):
    return Lab(monkeypatch)


def expect(lab, port, proto, wanted):
    got = lab.knock(port, proto)
    assert got == wanted, f"knock({lab.door.ip!r}, {port}, {proto!r}) returned {got!r}, expected {wanted!r}"


def test_defines_knock(lab):
    """solution.py defines a function named knock()."""
    lab.load()


def test_returns_allowed_string(lab):
    """knock() returns one of: syn-ack, rst, data, port-unreachable, icmp-other, none."""
    got = lab.knock(80, "tcp")
    assert got in ANSWERS, f"knock() returned {got!r}, expected one of {sorted(ANSWERS)}"


def test_tcp_open(lab):
    """TCP, open port → "syn-ack"."""
    expect(lab, 80, "tcp", "syn-ack")


def test_tcp_closed(lab):
    """TCP, closed port → "rst"."""
    expect(lab, 8080, "tcp", "rst")


def test_tcp_filtered(lab):
    """TCP, dropped port → "none"."""
    expect(lab, 443, "tcp", "none")


def test_tcp_rejected(lab):
    """TCP, port rejected with ICMP port unreachable → "port-unreachable"."""
    expect(lab, 25, "tcp", "port-unreachable")


def test_icmp_other(lab):
    """Any other ICMP error → "icmp-other"."""
    expect(lab, 445, "tcp", "icmp-other")


def test_udp_open(lab):
    """UDP, open port with a service → "data"."""
    expect(lab, 7777, "udp", "data")


def test_udp_closed(lab):
    """UDP, closed port → "port-unreachable"."""
    expect(lab, 9999, "udp", "port-unreachable")


def test_udp_silent(lab):
    """UDP, port that never answers → "none"."""
    expect(lab, 6000, "udp", "none")


def test_probe_and_timeout(lab):
    """The probe is a SYN / a UDP "knock knock" to the requested ip:port, sent with a timeout."""
    lab.knock(80, "tcp")
    lab.knock(7777, "udp")
    probes = lab.door.probes
    assert probes, "knock() sent nothing through sr1"
    tcp = [p for p in probes if p.haslayer(TCP)]
    udp = [p for p in probes if p.haslayer(UDP)]
    assert tcp and all(p[IP].dst == DOOR and p[TCP].dport == 80 and p[TCP].flags == "S" for p in tcp), \
        f"expected a SYN to {DOOR}:80, got {[p.summary() for p in tcp]}"
    assert udp and all(p[IP].dst == DOOR and p[UDP].dport == 7777 for p in udp), \
        f"expected UDP to {DOOR}:7777, got {[p.summary() for p in udp]}"
    assert all(p.haslayer(Raw) and p[Raw].load == KNOCK for p in udp), f"the UDP payload must be {KNOCK!r}"
    assert all(c.get("timeout") is not None for c in lab.door.calls), "sr1 was called without a timeout"


def test_nothing_hard_coded(lab):
    """It works on another IP and other port numbers too."""
    rng = random.Random(8123)
    ip = f"10.{rng.randint(1, 254)}.{rng.randint(1, 254)}.{rng.randint(1, 254)}"
    ports = rng.sample(range(1024, 65000), 6)
    cases = {
        ("tcp", ports[0]): ("open", "syn-ack"),
        ("tcp", ports[1]): ("closed", "rst"),
        ("tcp", ports[2]): ("prohibited", "icmp-other"),
        ("udp", ports[3]): ("open", "data"),
        ("udp", ports[4]): ("closed", "port-unreachable"),
        ("udp", ports[5]): ("drop", "none"),
    }
    lab.door = Door(ip, {key: state for key, (state, _) in cases.items()})
    for (proto, port), (_, wanted) in cases.items():
        got = lab.knock(port, proto)
        assert got == wanted, f"knock({ip!r}, {port}, {proto!r}) returned {got!r}, expected {wanted!r}"
