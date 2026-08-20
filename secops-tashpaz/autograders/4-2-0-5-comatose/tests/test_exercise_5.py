import os
import select
import shutil
import socket
import struct
import subprocess
import threading
import time

import pytest

from switch import Switch

ETH_P_ALL = 0x0003
# Not every Python build exposes these AF_PACKET constants; fall back to the
# numeric values from linux/if_packet.h.
SOL_PACKET = getattr(socket, "SOL_PACKET", 263)
PACKET_IGNORE_OUTGOING = getattr(socket, "PACKET_IGNORE_OUTGOING", 23)

# (switch-side interface, probe-side peer). A probe both injects traffic (by
# transmitting on the peer) and observes what the switch sends out that port.
LINKS = [("swt0", "swt0p"), ("swt1", "swt1p"), ("swt2", "swt2p")]
SWITCH_PORTS = [sw for sw, _ in LINKS]

HOST_A = "A1:2F:3F:BE:6C:12"
HOST_B = "B2:CC:DD:EE:6F:71"
BROADCAST_MAC = "FF:FF:FF:FF:FF:FF"

pytestmark = pytest.mark.skipif(
    os.geteuid() != 0 or shutil.which("ip") is None,
    reason="raw sockets + veth setup require root and the `ip` command (run under sudo)",
)


def _mac_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def make_frame(src: str, dst: str, tag: bytes) -> bytes:
    return _mac_bytes(dst) + _mac_bytes(src) + struct.pack("!H", 0x0800) + b"TAG:" + tag


def _open_probe(ifname: str) -> socket.socket:
    probe = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    # Don't report a probe's own injected frames back to itself, so a probe can
    # both inject on its port and still assert "nothing came back out of it".
    probe.setsockopt(SOL_PACKET, PACKET_IGNORE_OUTGOING, 1)
    probe.bind((ifname, 0))
    return probe


def _drain(sock: socket.socket) -> None:
    sock.setblocking(False)
    try:
        while True:
            sock.recv(65535)
    except BlockingIOError:
        pass
    finally:
        sock.setblocking(True)


def _received(sock: socket.socket, frame: bytes, timeout: float = 1.0) -> bool:
    """True if `frame` shows up on `sock` within `timeout`, ignoring other traffic."""
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


def _run_quietly(switch: Switch) -> None:
    # run() loops forever on select(); on teardown we close its sockets, which
    # makes select()/recv() raise -- swallow that so the thread exits cleanly.
    try:
        switch.run()
    except Exception:
        pass


@pytest.fixture
def probes():
    """Build a veth pair per switch port, bring the links up, and open a probe
    socket on each peer end. Yields {switch_port_name: probe_socket}."""
    for sw, _ in LINKS:
        subprocess.run(["ip", "link", "del", sw], capture_output=True)

    for sw, peer in LINKS:
        subprocess.run(["ip", "link", "add", sw, "type", "veth", "peer", "name", peer], check=True)
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
def running_switch(probes):
    switch = Switch(list(SWITCH_PORTS))  # opens real raw sockets on swt0..swt2
    thread = threading.Thread(target=_run_quietly, args=(switch,), daemon=True)
    thread.start()
    yield switch
    for port in switch.ports:
        port.sock.close()  # unblock select() inside run() so the thread can exit
    thread.join(timeout=1.0)


def inject(probes, port_name: str, frame: bytes) -> None:
    """Transmit `frame` on `port_name`'s peer, so it arrives at the switch as if
    a host had sent it in on that port."""
    probes[port_name].send(frame)


def test_run_floods_unknown_unicast_out_all_but_incoming(running_switch, probes):
    for probe in probes.values():
        _drain(probe)
    frame = make_frame(HOST_A, HOST_B, b"unknown")  # HOST_B never seen -> flood

    inject(probes, "swt0", frame)

    assert _received(probes["swt1"], frame), "run() should forward the frame out swt1"
    assert _received(probes["swt2"], frame), "run() should forward the frame out swt2"
    assert not _received(probes["swt0"], frame, timeout=0.2), (
        "run() must not send the frame back out its incoming port swt0"
    )


def test_run_floods_broadcast_out_all_but_incoming(running_switch, probes):
    for probe in probes.values():
        _drain(probe)
    frame = make_frame(HOST_A, BROADCAST_MAC, b"broadcast")

    inject(probes, "swt1", frame)

    assert _received(probes["swt0"], frame), "broadcast should be forwarded out swt0"
    assert _received(probes["swt2"], frame), "broadcast should be forwarded out swt2"
    assert not _received(probes["swt1"], frame, timeout=0.2), (
        "broadcast must not be sent back out its incoming port swt1"
    )


def test_run_forwards_known_unicast_to_learned_port(running_switch, probes):
    # Teach the switch that HOST_B lives on swt2 by injecting a frame from it.
    learn = make_frame(HOST_B, HOST_A, b"learn")
    inject(probes, "swt2", learn)
    # The learn frame floods to swt0/swt1; seeing it there confirms run() has
    # processed it (and therefore learned HOST_B) before we measure.
    assert _received(probes["swt0"], learn), "switch should have processed the learn frame"
    for probe in probes.values():
        _drain(probe)

    frame = make_frame(HOST_A, HOST_B, b"unicast")
    inject(probes, "swt0", frame)

    assert _received(probes["swt2"], frame), "known unicast should be forwarded out swt2"
    assert not _received(probes["swt1"], frame, timeout=0.2), "should not go out swt1"
    assert not _received(probes["swt0"], frame, timeout=0.2), "should not go out swt0"


def test_run_does_not_send_frame_back_out_incoming_port(running_switch, probes):
    # HOST_B is learned on swt0, then a frame for it also arrives on swt0.
    learn = make_frame(HOST_B, HOST_A, b"learn")
    inject(probes, "swt0", learn)
    assert _received(probes["swt1"], learn), "switch should have processed the learn frame"
    for probe in probes.values():
        _drain(probe)

    frame = make_frame(HOST_A, HOST_B, b"hairpin")
    inject(probes, "swt0", frame)

    for name, probe in probes.items():
        assert not _received(probe, frame, timeout=0.2), f"frame should be dropped, but it went out {name}"
