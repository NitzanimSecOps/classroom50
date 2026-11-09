import os
import select
import signal
import socket
import struct
import sys
import threading

import pytest

from switch import Switch

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


# The socket layer and select() are faked: every socket.socket(...) the Switch/Port
# creates is a FakeSocket that records what is sent out of it and hands back the
# frames the test "receives" on it. No root, no `ip`, no real NIC, and no AF_PACKET
# are needed, so this runs the same on Linux and Windows.
class FakeSocket:
    def __init__(self, *args, **kwargs):
        self.bound = None
        self.sent: list[bytes] = []
        self.inbox: list[bytes] = []  # frames waiting to be read by recv()

    def bind(self, address):
        self.bound = address

    def recv(self, *args):
        return self.inbox.pop(0)

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


class _StopRun(BaseException):
    """Raised by the fake select() when the scripted traffic is used up, to end run()."""


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


def _mac_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def make_frame(src: str, dst: str, tag: bytes) -> bytes:
    return _mac_bytes(dst) + _mac_bytes(src) + struct.pack("!H", 0x0800) + b"TAG:" + tag


@pytest.fixture
def probes(monkeypatch):
    """The fake socket behind each switch port: {port_name: FakeSocket}.
    Built before the switch so that Switch(...) gets fake sockets."""
    # Off-Linux Python has no AF_PACKET; the fake ignores the values anyway.
    monkeypatch.setattr(socket, "AF_PACKET", 17, raising=False)
    monkeypatch.setattr(socket, "SOCK_RAW", 3, raising=False)
    monkeypatch.setattr(socket, "htons", lambda x: x, raising=False)
    monkeypatch.setattr(socket, "socket", FakeSocket)
    return {}


@pytest.fixture
def running_switch(probes):
    switch = Switch(list(SWITCH_PORTS))  # opens fake sockets on swt0..swt2
    probes.update({port.name: port.sock for port in switch.ports})
    return switch


def _run(switch: Switch, probes, monkeypatch, script) -> None:
    """Drive switch.run(): `script` is a list of (port_name, frame). Each pass
    through the run() loop, the fake select() reports that one port readable and
    its recv() returns that frame; when the script is used up the loop is stopped."""
    pending = list(script)

    def fake_select(rlist, wlist, xlist, *args):
        if not pending:
            raise _StopRun()
        port_name, frame = pending.pop(0)
        sock = probes[port_name]
        sock.inbox.append(frame)
        return [sock], [], []

    monkeypatch.setattr(select, "select", fake_select)
    outcome = _in_thread(switch.run)
    if outcome and not isinstance(outcome[0], _StopRun):
        raise outcome[0]


def _clear(probes) -> None:
    for sock in probes.values():
        sock.sent.clear()


def _sent(sock: FakeSocket, frame: bytes) -> bool:
    return frame in sock.sent


def test_run_floods_unknown_unicast_out_all_but_incoming(running_switch, probes, monkeypatch):
    frame = make_frame(HOST_A, HOST_B, b"unknown")  # HOST_B never seen -> flood

    _run(running_switch, probes, monkeypatch, [("swt0", frame)])

    assert _sent(probes["swt1"], frame), "run() should forward the frame out swt1"
    assert _sent(probes["swt2"], frame), "run() should forward the frame out swt2"
    assert not _sent(probes["swt0"], frame), (
        "run() must not send the frame back out its incoming port swt0"
    )


def test_run_floods_broadcast_out_all_but_incoming(running_switch, probes, monkeypatch):
    frame = make_frame(HOST_A, BROADCAST_MAC, b"broadcast")

    _run(running_switch, probes, monkeypatch, [("swt1", frame)])

    assert _sent(probes["swt0"], frame), "broadcast should be forwarded out swt0"
    assert _sent(probes["swt2"], frame), "broadcast should be forwarded out swt2"
    assert not _sent(probes["swt1"], frame), (
        "broadcast must not be sent back out its incoming port swt1"
    )


def test_run_forwards_known_unicast_to_learned_port(running_switch, probes, monkeypatch):
    # Teach the switch that HOST_B lives on swt2 by feeding in a frame from it.
    learn = make_frame(HOST_B, HOST_A, b"learn")
    frame = make_frame(HOST_A, HOST_B, b"unicast")

    _run(running_switch, probes, monkeypatch, [("swt2", learn), ("swt0", frame)])

    assert _sent(probes["swt2"], frame), "known unicast should be forwarded out swt2"
    assert not _sent(probes["swt1"], frame), "should not go out swt1"
    assert not _sent(probes["swt0"], frame), "should not go out swt0"


def test_run_does_not_send_frame_back_out_incoming_port(running_switch, probes, monkeypatch):
    # HOST_B is learned on swt0, then a frame for it also arrives on swt0.
    learn = make_frame(HOST_B, HOST_A, b"learn")
    frame = make_frame(HOST_A, HOST_B, b"hairpin")

    _run(running_switch, probes, monkeypatch, [("swt0", learn), ("swt0", frame)])

    for name, probe in probes.items():
        assert not _sent(probe, frame), f"frame should be dropped, but it went out {name}"
