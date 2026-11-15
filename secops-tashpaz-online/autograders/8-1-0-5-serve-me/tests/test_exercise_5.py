"""Tests for 8.1.0.5 Serve Me - an echo server and a prompt client over length-prefixed messages.

The student's server.py runs as its own process on 127.0.0.1:2405 and is restarted only if it
crashed. client.py runs as its own process, fed through stdin, against the real server or a fake one.
"""
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


def frame(data):
    return struct.pack("!I", len(data)) + data


def assert_server_alive():
    """Fail now if the server died, instead of letting connect() quietly start it again."""
    for _ in range(5):
        if SERVER.proc.poll() is not None:
            break
        time.sleep(0.1)
    if SERVER.proc.poll() is not None:
        pytest.fail("the server stopped after that client" + server_crashed())


def reset(sock):
    """Close so that the other side gets a TCP RESET instead of a normal FIN."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


def run_prompt(text):
    return subprocess.run(
        [sys.executable, os.path.join(HERE, "client.py")],
        input=text, capture_output=True, text=True, timeout=8, cwd=HERE,
    )


def fake_server(replies):
    """Stands in for the student's server on port 2405: records what the client sends.

    Returns (messages, state, listener). state["closed"] is set when the client closes the connection.
    """
    if SERVER:
        SERVER.stop()  # the port must be free
    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((HOST, PORT))
    listener.listen()
    messages = []
    state = {"closed": False}

    def serve():
        conn, _ = listener.accept()
        with conn:
            conn.settimeout(5)
            try:
                while True:
                    size = struct.unpack("!I", read_exact(conn, 4))[0]
                    messages.append(read_exact(conn, size))
                    conn.sendall(frame(replies(messages[-1])))
            except (OSError, AssertionError):
                state["closed"] = True

    threading.Thread(target=serve, daemon=True).start()
    return messages, state, listener


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
    assert_server_alive()
    with connect() as s:
        s.sendall(frame(b"next"))
        assert read_frame(s) == b"next"


def test_server_survives_a_reset(server):
    """The server survives a client that resets the connection (RESET)"""
    s = connect()
    s.sendall(frame(b"hi"))
    read_frame(s)
    reset(s)
    time.sleep(0.1)
    assert_server_alive()
    with connect() as s:
        s.sendall(frame(b"next"))
        assert read_frame(s) == b"next"


def test_server_survives_a_client_that_never_reads(server):
    """The server survives writing the echo to a socket the client already closed"""
    content = secrets.token_bytes(3 * 1024 * 1024)
    s = connect()
    s.sendall(frame(content))
    reset(s)
    time.sleep(0.3)
    assert_server_alive()
    with connect() as s:
        s.sendall(frame(b"next"))
        assert read_frame(s) == b"next"


def test_client_prompt_sends_and_prints_the_reply(server):
    """python3 client.py sends "send data" to the server, prints the reply, and ends on exit"""
    token = secrets.token_hex(8)
    result = run_prompt(f"send {token}\nexit\n")
    assert result.returncode == 0, result.stderr
    assert token in result.stdout


def test_client_sends_the_data_after_send():
    """The client sends only the data after "send " as one length-prefixed message"""
    messages, state, listener = fake_server(lambda m: b"got:" + m)
    try:
        result = run_prompt("send hello world\nexit\n")
        assert result.returncode == 0, result.stderr
        time.sleep(0.2)
        assert messages == [b"hello world"]
        assert "got:hello world" in result.stdout
    finally:
        listener.close()


def test_client_handles_several_sends():
    """The client handles several send commands on one connection"""
    messages, state, listener = fake_server(lambda m: m.upper())
    try:
        result = run_prompt("send one\nsend two\nsend three\nexit\n")
        assert result.returncode == 0, result.stderr
        time.sleep(0.2)
        assert messages == [b"one", b"two", b"three"]
        assert "ONE" in result.stdout and "TWO" in result.stdout and "THREE" in result.stdout
    finally:
        listener.close()


def test_client_closes_the_connection_on_exit():
    """On exit the client closes the connection and ends without waiting for more input"""
    messages, state, listener = fake_server(lambda m: m)
    try:
        result = run_prompt("send hi\nexit\nsend ignored\n")
        assert result.returncode == 0, result.stderr
        time.sleep(0.3)
        assert state["closed"], "the client ended without closing the connection"
        assert messages == [b"hi"], "the client kept going after exit"
    finally:
        listener.close()
