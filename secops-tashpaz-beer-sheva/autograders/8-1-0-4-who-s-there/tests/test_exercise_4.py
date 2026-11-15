"""Tests for 8.1.0.4 Who's There - length-prefixed messages in protocol.py.

The protocol functions run on a socket pair (or a real loopback connection for a RESET), so a test
controls exactly how the bytes arrive.
"""
import importlib
import os
import secrets
import signal
import socket
import struct
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 2405


# Hard time limit per test. A student's endless loop (or a call that never returns) would otherwise
# hang the whole run until the grader is killed and no score is posted at all.
TEST_LIMIT = 10  # seconds, for every test not listed below
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
    request.config.pluginmanager.register(Guard())


@pytest.fixture(scope="session", autouse=True)
def hard_limits(request):
    request.config.pluginmanager.register(Guard())


@pytest.fixture
def pair():
    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    yield a, b
    a.close()
    b.close()


@pytest.fixture
def tcp_pair():
    """Two ends of a real TCP connection on loopback (a socketpair cannot send a RESET)."""
    listener = socket.socket()
    listener.bind((HOST, 0))
    listener.listen()
    a = socket.create_connection(listener.getsockname())
    b, _ = listener.accept()
    listener.close()
    a.settimeout(5)
    b.settimeout(5)
    yield a, b
    a.close()
    b.close()


def reset(sock):
    """Close so that the other side gets a TCP RESET instead of a normal FIN."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()
    time.sleep(0.1)


def protocol():
    import protocol as module
    return importlib.reload(module)


def frame(data):
    return struct.pack("!I", len(data)) + data


def read_exact(s, n):
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        assert chunk, "the connection closed in the middle of the data"
        data += chunk
    return data


def send_slowly(sock, data):
    for i in range(len(data)):
        sock.sendall(data[i:i + 1])
        time.sleep(0.005)


def test_recv_exact_returns_exactly_n_bytes(pair):
    """recv_exact returns exactly n bytes and leaves the rest for the next read"""
    a, b = pair
    a.sendall(b"0123456789")
    time.sleep(0.05)
    p = protocol()
    assert p.recv_exact(b, 4) == b"0123"
    assert p.recv_exact(b, 6) == b"456789"


def test_recv_exact_keeps_reading(pair):
    """recv_exact keeps reading when the bytes arrive one at a time"""
    a, b = pair
    p = protocol()
    t = threading.Thread(target=send_slowly, args=(a, b"abcdefgh"), daemon=True)
    t.start()
    assert p.recv_exact(b, 8) == b"abcdefgh"
    t.join()


def test_recv_exact_returns_none_when_closed(pair):
    """recv_exact returns None if the connection closes before n bytes arrived"""
    a, b = pair
    a.sendall(b"abcdefghij" + b"abc")
    a.close()
    p = protocol()
    assert p.recv_exact(b, 10) == b"abcdefghij"
    assert p.recv_exact(b, 10) is None


def test_send_msg_adds_a_big_endian_length(pair):
    """send_msg sends a 4-byte big-endian length and then the message"""
    a, b = pair
    protocol().send_msg(a, b"meow!")
    assert read_exact(b, 9) == b"\x00\x00\x00\x05meow!"


def test_recv_msg_reads_one_message(pair):
    """recv_msg returns the message without its length"""
    a, b = pair
    a.sendall(frame(b"hello"))
    assert protocol().recv_msg(b) == b"hello"


def test_recv_msg_handles_a_split_message(pair):
    """recv_msg handles a message that arrives one byte at a time, even the length"""
    a, b = pair
    p = protocol()
    t = threading.Thread(target=send_slowly, args=(a, frame(b"split me")), daemon=True)
    t.start()
    assert p.recv_msg(b) == b"split me"
    t.join()


def test_recv_msg_separates_two_messages(pair):
    """recv_msg separates two messages that arrived in one send"""
    a, b = pair
    a.sendall(frame(b"first") + frame(b"second"))
    time.sleep(0.05)
    p = protocol()
    assert p.recv_msg(b) == b"first"
    assert p.recv_msg(b) == b"second"


def test_recv_msg_handles_a_large_message(pair):
    """send_msg and recv_msg carry a 5 MB message whole"""
    a, b = pair
    p = protocol()
    content = secrets.token_bytes(5 * 1024 * 1024)
    t = threading.Thread(target=p.send_msg, args=(a, content), daemon=True)
    t.start()
    assert p.recv_msg(b) == content
    t.join()


def test_recv_msg_handles_an_empty_message(pair):
    """An empty message comes back as b"", not as None"""
    a, b = pair
    a.sendall(frame(b"") + frame(b"next"))
    p = protocol()
    assert p.recv_msg(b) == b""
    assert p.recv_msg(b) == b"next"


def test_recv_msg_returns_none_when_closed(pair):
    """recv_msg returns None when the connection closes between messages"""
    a, b = pair
    a.sendall(frame(b"last"))
    a.close()
    p = protocol()
    assert p.recv_msg(b) == b"last"
    assert p.recv_msg(b) is None


def test_recv_msg_returns_none_when_cut_off(pair):
    """recv_msg returns None when the connection closes in the middle of a message"""
    a, b = pair
    a.sendall(frame(b"whole") + struct.pack("!I", 100) + b"only this")
    a.close()
    p = protocol()
    assert p.recv_msg(b) == b"whole"
    assert p.recv_msg(b) is None


def test_recv_exact_returns_none_when_reset(tcp_pair):
    """recv_exact returns None if the connection is reset before n bytes arrived"""
    a, b = tcp_pair
    a.sendall(b"abcdefghij" + b"abc")
    time.sleep(0.05)
    reset(a)
    p = protocol()
    assert p.recv_exact(b, 10) == b"abcdefghij"
    assert p.recv_exact(b, 10) is None


def test_recv_msg_returns_none_when_reset(tcp_pair):
    """recv_msg returns None if the connection is reset instead of raising"""
    a, b = tcp_pair
    a.sendall(frame(b"last"))
    time.sleep(0.05)
    reset(a)
    p = protocol()
    assert p.recv_msg(b) == b"last"
    assert p.recv_msg(b) is None
