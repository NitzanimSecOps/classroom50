import os
import select
import shutil
import socket
import struct
import subprocess
import time

import pytest

from switch import Switch, Port

ETH_P_ALL = 0x0003

# Each tuple is (switch-side interface, probe-side peer). The switch binds to
# the first; the test opens a probe socket on the second, so it sees exactly
# what the switch transmits out of that port.
LINKS = [("swt0", "swt0p"), ("swt1", "swt1p"), ("swt2", "swt2p")]
SWITCH_PORTS = [sw for sw, _ in LINKS]

HOST_A = "A1:2F:3F:BE:6C:12"
HOST_B = "B2:CC:DD:EE:6F:71"
BROADCAST_MAC = "FF:FF:FF:FF:FF:FF"

# The whole file is an integration test: it needs root + the `ip` tool.
pytestmark = pytest.mark.skipif(
    os.geteuid() != 0 or shutil.which("ip") is None,
    reason="raw sockets + veth setup require root and the `ip` command (run under sudo)",
)


def _mac_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def make_frame(src: str, dst: str, tag: bytes) -> bytes:
    # `tag` keeps each frame's bytes unique so probes can tell them apart.
    payload = b"TAG:" + tag
    return _mac_bytes(dst) + _mac_bytes(src) + struct.pack("!H", 0x0800) + payload


def _open_probe(ifname: str) -> socket.socket:
    probe = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    probe.bind((ifname, 0))
    return probe


def _drain(sock: socket.socket) -> None:
    """Discard anything already buffered (e.g. interface start-up chatter)."""
    sock.setblocking(False)
    try:
        while True:
            sock.recv(65535)
    except BlockingIOError:
        pass
    finally:
        sock.setblocking(True)


def _received(sock: socket.socket, frame: bytes, timeout: float = 1.0) -> bool:
    """True if `frame` shows up on `sock` within `timeout`, ignoring other
    traffic. Short frames get zero-padded on TX, so match on the prefix."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        readable, _, _ = select.select([sock], [], [], remaining)
        if not readable:
            return False
        if sock.recv(65535).startswith(frame):
            return True


@pytest.fixture
def probes():
    """Build a veth pair per switch port, bring the links up, and open a probe
    socket on each peer end. Yields {switch_port_name: probe_socket}."""
    # tear down any leftovers from a previous crashed run
    for sw, _ in LINKS:
        subprocess.run(["ip", "link", "del", sw], capture_output=True)

    for sw, peer in LINKS:
        subprocess.run(["ip", "link", "add", sw, "type", "veth", "peer", "name", peer], check=True)
        # cut IPv6 background chatter before the links come up
        for iface in (sw, peer):
            subprocess.run(["sysctl", "-qw", f"net.ipv6.conf.{iface}.disable_ipv6=1"], capture_output=True)
        subprocess.run(["ip", "link", "set", sw, "up"], check=True)
        subprocess.run(["ip", "link", "set", peer, "up"], check=True)

    sockets = {sw: _open_probe(peer) for sw, peer in LINKS}
    yield sockets

    for sock in sockets.values():
        sock.close()
    for sw, _ in LINKS:
        subprocess.run(["ip", "link", "del", sw], capture_output=True)


@pytest.fixture
def switch(probes):
    sw = Switch(list(SWITCH_PORTS))  # opens real raw sockets on swt0..swt2
    yield sw
    for port in sw.ports:
        port.sock.close()


def test_unknown_unicast_is_transmitted_out_all_but_incoming(switch, probes):
    for probe in probes.values():
        _drain(probe)
    frame = make_frame(HOST_A, HOST_B, b"unknown")  # HOST_B never seen -> flood

    switch.process_frame(frame, Port("swt0"))

    assert _received(probes["swt1"], frame), "frame should be transmitted out swt1"
    assert _received(probes["swt2"], frame), "frame should be transmitted out swt2"
    assert not _received(probes["swt0"], frame, timeout=0.2), (
        "frame must not be transmitted back out its incoming port swt0"
    )


def test_broadcast_is_transmitted_out_all_but_incoming(switch, probes):
    for probe in probes.values():
        _drain(probe)
    frame = make_frame(HOST_A, BROADCAST_MAC, b"broadcast")

    switch.process_frame(frame, Port("swt1"))

    assert _received(probes["swt0"], frame), "broadcast should be transmitted out swt0"
    assert _received(probes["swt2"], frame), "broadcast should be transmitted out swt2"
    assert not _received(probes["swt1"], frame, timeout=0.2), (
        "broadcast must not be transmitted back out its incoming port swt1"
    )


def test_known_unicast_is_transmitted_only_to_learned_port(switch, probes):
    # Teach the switch that HOST_B lives on swt2 by having it speak first.
    switch.process_frame(make_frame(HOST_B, HOST_A, b"learn"), Port("swt2"))
    for probe in probes.values():
        _drain(probe)
    frame = make_frame(HOST_A, HOST_B, b"unicast")

    switch.process_frame(frame, Port("swt0"))

    assert _received(probes["swt2"], frame), "known unicast should be transmitted out swt2"
    assert not _received(probes["swt1"], frame, timeout=0.2), "should not be transmitted out swt1"
    assert not _received(probes["swt0"], frame, timeout=0.2), "should not be transmitted out swt0"


def test_frame_is_not_transmitted_back_out_incoming_port(switch, probes):
    # HOST_B is learned on swt0, and a frame for it also arrives on swt0.
    switch.process_frame(make_frame(HOST_B, HOST_A, b"learn"), Port("swt0"))
    for probe in probes.values():
        _drain(probe)
    frame = make_frame(HOST_A, HOST_B, b"hairpin")

    switch.process_frame(frame, Port("swt0"))

    for name, probe in probes.items():
        assert not _received(probe, frame, timeout=0.2), f"frame should be dropped, but it went out {name}"
