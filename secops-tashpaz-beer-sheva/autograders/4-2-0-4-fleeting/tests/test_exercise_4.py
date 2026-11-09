import os
import signal
import socket
import struct
import sys
import threading

import pytest

from switch import Switch, Port

SWITCH_PORTS = ["swt0", "swt1", "swt2"]

HOST_A = "A1:2F:3F:BE:6C:12"
HOST_B = "B2:CC:DD:EE:6F:71"
BROADCAST_MAC = "FF:FF:FF:FF:FF:FF"


# Hard time limit per test. A student's endless loop (or a call that never returns) would otherwise
# hang the whole run until the grader is killed and no score is posted at all.
TEST_LIMIT = 10  # seconds, for every test
JOIN_LIMIT = 3   # seconds a single call into the student's code may take
LIMITS = {}
TIMED_OUT = []


class Guard:
    """Hard time limit per test, and a clean exit even if a student thread never ends.

    The timer only runs while the test function itself runs (not during setup, teardown or
    pytest's own reporting), so a late alarm can never fail pytest instead of the test.
    """

    status = 0
    armed = False

    def on_alarm(self, signum, frame):
        if not self.armed:
            return
        TIMED_OUT.append(self.name)
        # Fire again soon: a bare "except:" in the student's code could swallow this failure.
        signal.setitimer(signal.ITIMER_REAL, 0.5)
        pytest.fail(f"took longer than {self.limit} s (an endless loop or a blocking call?)")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        self.name = item.name
        self.limit = LIMITS.get(item.name, TEST_LIMIT)
        old = signal.signal(signal.SIGALRM, self.on_alarm)
        self.armed = True
        signal.setitimer(signal.ITIMER_REAL, self.limit)
        try:
            yield
        finally:
            self.armed = False
            signal.setitimer(signal.ITIMER_REAL, 0)
            signal.signal(signal.SIGALRM, old)

    def pytest_sessionfinish(self, exitstatus):
        self.status = int(exitstatus)

    @pytest.hookimpl(trylast=True)
    def pytest_unconfigure(self, config):
        strays = [t for t in threading.enumerate() if t.is_alive() and not t.daemon and t is not threading.main_thread()]
        if strays:
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(self.status)


@pytest.fixture(scope="session", autouse=True)
def hard_limits(request):
    # SIGALRM does not exist on Windows (local runs only); the runner is Linux.
    if hasattr(signal, "SIGALRM"):
        request.config.pluginmanager.register(Guard())


# The socket layer is faked: every socket.socket(...) the Switch/Port creates is a
# FakeSocket that records what is sent out of it. No root, no `ip`, no real NIC,
# and no AF_PACKET are needed, so this runs the same on Linux and Windows.
class FakeSocket:
    def __init__(self, *args, **kwargs):
        self.bound = None
        self.sent: list[bytes] = []

    def bind(self, address):
        self.bound = address

    def send(self, data, *args):
        self.sent.append(bytes(data))
        return len(data)

    def sendto(self, data, *args):
        self.sent.append(bytes(data))
        return len(data)

    def setsockopt(self, *args, **kwargs):
        pass

    def setblocking(self, *args, **kwargs):
        pass

    def close(self):
        pass


def _in_thread(fn, *args):
    """Run student code in a daemon thread and give it a few seconds. Guard's alarm alone can be
    swallowed forever by a bare `except:` around an endless loop; a thread that is still alive
    after the wait cannot hold the test hostage, so the row fails and the run goes on."""
    outcome = []

    def target():
        try:
            fn(*args)
        except BaseException as exc:
            outcome.append(exc)

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(JOIN_LIMIT)
    if worker.is_alive():
        pytest.fail("your code did not return within %s s (an endless loop?)" % JOIN_LIMIT)
    return outcome


def _process(switch, frame, port):
    outcome = _in_thread(switch.process_frame, frame, port)
    if outcome:
        raise outcome[0]


def _mac_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def make_frame(src: str, dst: str, tag: bytes) -> bytes:
    # `tag` keeps each frame's bytes unique so a sent frame can be told apart.
    payload = b"TAG:" + tag
    return _mac_bytes(dst) + _mac_bytes(src) + struct.pack("!H", 0x0800) + payload


@pytest.fixture
def switch(monkeypatch):
    # Off-Linux Python has no AF_PACKET; the fake ignores the values anyway.
    monkeypatch.setattr(socket, "AF_PACKET", 17, raising=False)
    monkeypatch.setattr(socket, "SOCK_RAW", 3, raising=False)
    monkeypatch.setattr(socket, "htons", lambda x: x, raising=False)
    monkeypatch.setattr(socket, "socket", FakeSocket)
    return Switch(list(SWITCH_PORTS))


@pytest.fixture
def probes(switch):
    """The fake socket behind each switch port: {port_name: FakeSocket}."""
    return {port.name: port.sock for port in switch.ports}


def _clear(probes) -> None:
    for sock in probes.values():
        sock.sent.clear()


def _sent(sock: FakeSocket, frame: bytes) -> bool:
    return frame in sock.sent


def test_unknown_unicast_is_transmitted_out_all_but_incoming(switch, probes):
    _clear(probes)
    frame = make_frame(HOST_A, HOST_B, b"unknown")  # HOST_B never seen -> flood

    _process(switch, frame, Port("swt0"))

    assert _sent(probes["swt1"], frame), "frame should be transmitted out swt1"
    assert _sent(probes["swt2"], frame), "frame should be transmitted out swt2"
    assert not _sent(probes["swt0"], frame), (
        "frame must not be transmitted back out its incoming port swt0"
    )


def test_broadcast_is_transmitted_out_all_but_incoming(switch, probes):
    _clear(probes)
    frame = make_frame(HOST_A, BROADCAST_MAC, b"broadcast")

    _process(switch, frame, Port("swt1"))

    assert _sent(probes["swt0"], frame), "broadcast should be transmitted out swt0"
    assert _sent(probes["swt2"], frame), "broadcast should be transmitted out swt2"
    assert not _sent(probes["swt1"], frame), (
        "broadcast must not be transmitted back out its incoming port swt1"
    )


def test_known_unicast_is_transmitted_only_to_learned_port(switch, probes):
    # Teach the switch that HOST_B lives on swt2 by having it speak first.
    _process(switch, make_frame(HOST_B, HOST_A, b"learn"), Port("swt2"))
    _clear(probes)
    frame = make_frame(HOST_A, HOST_B, b"unicast")

    _process(switch, frame, Port("swt0"))

    assert _sent(probes["swt2"], frame), "known unicast should be transmitted out swt2"
    assert not _sent(probes["swt1"], frame), "should not be transmitted out swt1"
    assert not _sent(probes["swt0"], frame), "should not be transmitted out swt0"


def test_frame_is_not_transmitted_back_out_incoming_port(switch, probes):
    # HOST_B is learned on swt0, and a frame for it also arrives on swt0.
    _process(switch, make_frame(HOST_B, HOST_A, b"learn"), Port("swt0"))
    _clear(probes)
    frame = make_frame(HOST_A, HOST_B, b"hairpin")

    _process(switch, frame, Port("swt0"))

    for name, probe in probes.items():
        assert not _sent(probe, frame), f"frame should be dropped, but it went out {name}"
