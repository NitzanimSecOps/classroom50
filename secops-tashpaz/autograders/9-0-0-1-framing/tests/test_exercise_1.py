"""Tests for 9.0.0.1 Framing - length-prefixed messages and an echo server.

The protocol functions run on a socket pair, so a test controls exactly how the bytes arrive. The
student's server.py runs as its own process on 127.0.0.1 and is restarted only if it crashed.
"""
import importlib
import os
import secrets
import signal
import socket
import struct
import subprocess
import sys
import tempfile
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

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_makereport(self, item, call):
        report = (yield).get_result()
        if report.when == "call" and report.failed:
            # A wrong answer is often a crash in disguise: say so in the feedback the student sees.
            if SERVER and SERVER.proc is not None:
                for _ in range(5):
                    if SERVER.proc.poll() is not None:
                        break
                    time.sleep(0.1)
                text = str(report.longrepr)
                if "your server.py " not in text and server_crashed():
                    note = server_crashed().strip()
                    if hasattr(report.longrepr, "addsection"):
                        report.longrepr.addsection("server.py", note)
                    else:
                        report.longrepr = text + "\n" + note

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


class Server:
    def __init__(self):
        self.proc = None
        self.log = None
        self.crash = None  # why the previous server process died, for the next test's message
        self.lock = threading.Lock()  # tests with many client threads may all ask for a restart

    def ensure_running(self):
        with self.lock:
            self.crash = self.why_it_died()
            if self.proc is None or self.proc.poll() is not None:
                self.close_log()
                self.log = tempfile.TemporaryFile()
                self.proc = subprocess.Popen(
                    [sys.executable, os.path.join(HERE, "server.py")],
                    cwd=HERE, stdout=subprocess.DEVNULL, stderr=self.log,
                )
                time.sleep(0.5)

    def why_it_died(self):
        """The end of the server's error output, if it has stopped."""
        if self.proc is None or self.proc.poll() is None:
            return None
        self.log.seek(0)
        lines = self.log.read().decode(errors="replace").strip().splitlines()
        if not lines:
            return (f"your server.py stopped by itself (exit code {self.proc.returncode}, no error), so "
                    "nothing is listening any more: does it keep waiting for clients in a loop?")
        # the last line of the student's own code in the traceback, then the error itself
        own = [i for i, line in enumerate(lines) if line.strip().startswith('File "')
               and os.path.dirname(line.split('"')[1]) == HERE]
        shown = lines[own[-1]:own[-1] + 2] if own else []
        text = "your server.py crashed:\n" + "\n".join(shown + [lines[-1]])
        if "Address already in use" in text:
            text += ("\n(the port is still held by the previous run; set "
                     "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1) before bind)")
        return text

    def close_log(self):
        if self.log is not None:
            self.log.close()
            self.log = None

    def stop(self):
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait()
            self.proc = None
        self.close_log()


SERVER = None  # the running Server, so the helpers below can say why a connection failed


@pytest.fixture(scope="module")
def running():
    global SERVER
    SERVER = Server()
    yield SERVER
    SERVER.stop()
    SERVER = None


def server_crashed():
    """A sentence about the server process dying, or "" if it is still running."""
    why = SERVER.why_it_died() if SERVER else None
    return f"\n{why}" if why else ""


@pytest.fixture
def server(running):
    if TIMED_OUT:  # the last test timed out, maybe stuck in the server: start it fresh
        TIMED_OUT.clear()
        running.stop()
    running.ensure_running()
    return running


@pytest.fixture
def pair():
    a, b = socket.socketpair()
    a.settimeout(5)
    b.settimeout(5)
    yield a, b
    a.close()
    b.close()


def protocol():
    import protocol as module
    return importlib.reload(module)


def frame(data):
    return struct.pack("!I", len(data)) + data


def connect(timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        s = socket.socket()
        s.settimeout(10)
        try:
            s.connect((HOST, PORT))
            return s
        except OSError:
            s.close()
            if SERVER and SERVER.proc is not None and SERVER.proc.poll() is not None and not SERVER.crash:
                # It died just after the previous test ended: start it once more, so a crash is
                # graded in the test that caused it and not in this one too.
                SERVER.ensure_running()
            time.sleep(0.1)
    pytest.fail("nothing is listening on port 2405" + server_crashed())


def read_exact(s, n):
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        assert chunk, "the server closed the connection in the middle of an answer"
        data += chunk
    return data


def read_frame(s):
    """One framed answer from the server, with a failure message that says what went wrong."""
    # pytest.fail is called outside the except blocks, so the report shows only our message
    problem = None
    try:
        size = struct.unpack("!I", read_exact(s, 4))[0]
        body = read_exact(s, size)
    except socket.timeout:
        problem = f"waited {s.gettimeout():g} s for the server's answer and nothing came"
    except (AssertionError, ConnectionError):
        time.sleep(0.3)  # give a crashing server a moment to exit and write its error
        problem = "the server closed the connection instead of answering"
    if problem:
        pytest.fail(problem + server_crashed())
    return body


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
    a.sendall(b"abc")
    a.close()
    assert protocol().recv_exact(b, 10) is None


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
    a.close()
    assert protocol().recv_msg(b) is None


def test_recv_msg_returns_none_when_cut_off(pair):
    """recv_msg returns None when the connection closes in the middle of a message"""
    a, b = pair
    a.sendall(struct.pack("!I", 100) + b"only this")
    a.close()
    assert protocol().recv_msg(b) is None


def test_server_echoes_a_message(server):
    """The server listens on port 2405 and sends a message back in the same format"""
    content = secrets.token_hex(10).encode()
    with connect() as s:
        s.sendall(frame(content))
        assert read_frame(s) == content


def test_server_keeps_the_connection_open(server):
    """The server answers several messages on one connection"""
    with connect() as s:
        for _ in range(5):
            content = secrets.token_hex(10).encode()
            s.sendall(frame(content))
            assert read_frame(s) == content


def test_server_handles_a_split_message(server):
    """The server handles a message that arrives one byte at a time"""
    s = connect()
    with s:
        s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        send_slowly(s, frame(b"slowly"))
        assert read_frame(s) == b"slowly"


def test_server_answers_two_messages_sent_together(server):
    """The server answers both messages when two arrive in one send"""
    with connect() as s:
        s.sendall(frame(b"one") + frame(b"two"))
        assert read_frame(s) == b"one"
        assert read_frame(s) == b"two"


def test_server_echoes_a_large_message(server):
    """The server echoes a 5 MB message whole"""
    content = secrets.token_bytes(5 * 1024 * 1024)
    with connect() as s:
        s.sendall(frame(content))
        assert read_frame(s) == content


def test_server_survives_a_client_leaving(server):
    """The server serves the next client after one disconnects"""
    with connect() as s:
        s.sendall(frame(b"bye"))
        read_frame(s)
    with connect() as s:
        s.sendall(frame(b"next"))
        assert read_frame(s) == b"next"


def test_server_survives_a_cut_off_message(server):
    """The server survives a client that disconnects in the middle of a message"""
    with connect() as s:
        s.sendall(struct.pack("!I", 1000) + b"partial")
    time.sleep(0.1)
    with connect() as s:
        s.sendall(frame(b"next"))
        assert read_frame(s) == b"next"
