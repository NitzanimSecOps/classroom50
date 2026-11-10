"""Tests for 9.0.0.2 Remote Commands - run commands on the server over one connection.

The student's server.py runs as its own process on 127.0.0.1 and is restarted only if it crashed.
These tests speak the wire format themselves. run_command is tested against a small fake server.
"""
import importlib
import json
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
TEST_LIMIT = 8  # seconds, for every test not listed below
LIMITS = {
    "test_server_stops_a_stuck_command": 21,
}
TIMED_OUT = []
# The test's last request and the server's answer to it, shown under a failing test. Only the test's
# own thread records, so fake servers and helper client threads never overwrite it.
EXCHANGE = {}


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
        if "sent" in EXCHANGE and "got" not in EXCHANGE:
            pytest.fail(f"waited {self.limit} s and the server never answered the last request "
                        "(is it stuck in a loop, or still busy with the previous client?)")
        pytest.fail(f"took longer than {self.limit} s (an endless loop or a blocking call?)")

    @pytest.hookimpl(hookwrapper=True)
    def pytest_runtest_call(self, item):
        EXCHANGE.clear()
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
            if "sent" in EXCHANGE and hasattr(report.longrepr, "addsection"):
                got = EXCHANGE.get("got", "(nothing: the test was still waiting for the answer)")
                report.longrepr.addsection("last exchange", f"sent: {EXCHANGE['sent']}\ngot:  {got}")
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


def connect(timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        s = socket.socket()
        s.settimeout(15)
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
        assert chunk, "the connection closed in the middle of a message"
        data += chunk
    return data


def record(key, value, limit=300):
    """Keep the test's last request / answer (main thread only), cut to a readable length."""
    if threading.current_thread() is not threading.main_thread():
        return
    text = value if isinstance(value, str) else (
        json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else repr(value))
    EXCHANGE[key] = text if len(text) <= limit else text[:limit] + f"... ({len(text)} characters)"
    if key == "sent":
        EXCHANGE.pop("got", None)


def send_raw(s, data):
    record("sent", data)
    s.sendall(struct.pack("!I", len(data)) + data)


def send(s, obj):
    send_raw(s, json.dumps(obj).encode())


def read_message(s):
    """One framed JSON message (used by the fake server, which reads the student's client)."""
    return json.loads(read_exact(s, struct.unpack("!I", read_exact(s, 4))[0]))


def short(data, limit=80):
    return repr(data[:limit]) + (f"... ({len(data)} bytes)" if len(data) > limit else "")


def read(s):
    """The server's answer, with a failure message that says what actually came back."""
    # pytest.fail is called outside the except blocks, so the report shows only our message
    problem = None
    try:
        size = struct.unpack("!I", read_exact(s, 4))[0]
        body = read_exact(s, size)
    except socket.timeout:
        problem = "waited 15 s for the server's answer and nothing came"
    except (AssertionError, ConnectionError):
        time.sleep(0.3)  # give a crashing server a moment to exit and write its error
        problem = "the server closed the connection instead of answering"
    if problem:
        pytest.fail(problem + server_crashed())
    try:
        reply = json.loads(body)
    except ValueError:
        reply = problem = (
            f"expected the answer to be one JSON message, but got {short(body)}\n"
            "the server must answer each request with exactly one JSON message and send nothing else"
        )
    if problem:
        pytest.fail(problem + server_crashed())
    if not isinstance(reply, dict):
        pytest.fail(f"expected the answer to be a JSON object {{...}}, but got {short(body)}")
    record("got", reply)
    return reply


def execute(s, cmd):
    send(s, {"type": "exec", "cmd": cmd})
    return read(s)


def fake_server(answer):
    """Answers each message with answer(request). Returns (port, requests, listening socket)."""
    listener = socket.socket()
    listener.bind((HOST, 0))
    listener.listen()
    requests = []

    def serve():
        conn, _ = listener.accept()
        with conn:
            conn.settimeout(5)
            try:
                while True:
                    request = read_message(conn)
                    requests.append(request)
                    send(conn, answer(request))
            except (OSError, AssertionError):
                pass

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1], requests, listener


def check_is_dict(result):
    if isinstance(result, (bytes, str)):
        pytest.fail(f"run_command returned {type(result).__name__} {short(result)}; "
                    "it should return the answer as a dictionary (json.loads it)")


def run_prompt(text):
    return subprocess.run(
        [sys.executable, os.path.join(HERE, "client.py")],
        input=text, capture_output=True, text=True, timeout=20, cwd=HERE,
    )


def test_server_runs_a_command(server):
    """exec runs the command and returns its stdout in a result message"""
    token = secrets.token_hex(8)
    with connect() as s:
        reply = execute(s, f"echo {token}")
    assert reply["type"] == "result"
    assert reply["stdout"].strip() == token


def test_server_returns_stderr_separately(server):
    """stderr comes back in its own field, separate from stdout"""
    with connect() as s:
        reply = execute(s, "echo out; echo oops 1>&2")
    assert reply["stdout"].strip() == "out"
    assert reply["stderr"].strip() == "oops"


def test_server_returns_the_exit_code(server):
    """exit_code is 0 on success and the command's own code on failure"""
    with connect() as s:
        assert execute(s, "true")["exit_code"] == 0
        assert execute(s, "exit 3")["exit_code"] == 3


def test_server_uses_a_shell(server):
    """Commands run in a shell, so pipes work"""
    with connect() as s:
        assert execute(s, "echo abc | tr a-z A-Z")["stdout"].strip() == "ABC"


def test_server_handles_several_commands(server):
    """Several commands run one after another on one connection"""
    with connect() as s:
        for _ in range(5):
            token = secrets.token_hex(6)
            assert execute(s, f"echo {token}")["stdout"].strip() == token


def test_server_returns_large_output_whole(server):
    """A command that prints 3 MB returns all of it"""
    with connect() as s:
        reply = execute(s, "head -c 3000000 /dev/zero | tr '\\0' x")
    assert reply["stdout"] == "x" * 3000000


def test_server_survives_output_that_is_not_text(server):
    """Output that is not valid text still gets an answer"""
    with connect() as s:
        assert execute(s, "printf '\\377\\376'")["type"] == "result"
        assert execute(s, "echo fine")["stdout"].strip() == "fine"


def test_server_stops_a_stuck_command(server):
    """A command stuck for over 5 seconds gets a timeout error and the connection keeps working"""
    with connect() as s:
        start = time.time()
        reply = execute(s, "sleep 30")
        assert reply == {"type": "error", "message": "timeout"}
        assert time.time() - start < 10
        assert execute(s, "echo alive")["stdout"].strip() == "alive"


def test_server_rejects_an_unknown_type(server):
    """An unknown type gets a bad request error and the connection keeps working"""
    with connect() as s:
        send(s, {"type": "dance"})
        assert read(s) == {"type": "error", "message": "bad request"}
        assert execute(s, "echo alive")["stdout"].strip() == "alive"


def test_server_rejects_broken_json(server):
    """A message that is not JSON gets a bad request error and the connection keeps working"""
    with connect() as s:
        send_raw(s, b"this is not json {")
        assert read(s) == {"type": "error", "message": "bad request"}
        assert execute(s, "echo alive")["stdout"].strip() == "alive"


def test_server_serves_the_next_client(server):
    """The server serves the next client after one disconnects"""
    with connect() as s:
        execute(s, "true")
    with connect() as s:
        assert execute(s, "echo next")["stdout"].strip() == "next"


def test_client_sends_an_exec_message():
    """run_command sends an exec message and returns the answer as a dictionary"""
    import client
    importlib.reload(client)
    answer = {"type": "result", "stdout": "hi\n", "stderr": "", "exit_code": 0}
    port, requests, listener = fake_server(lambda request: answer)
    try:
        with socket.create_connection((HOST, port)) as s:
            s.settimeout(5)
            result = client.run_command(s, "echo hi")
            check_is_dict(result)
            assert result == answer
        assert requests == [{"type": "exec", "cmd": "echo hi"}]
    finally:
        listener.close()


def test_client_returns_a_large_answer():
    """run_command reads a 3 MB answer whole"""
    import client
    importlib.reload(client)
    answer = {"type": "result", "stdout": "x" * 3000000, "stderr": "", "exit_code": 0}
    port, _, listener = fake_server(lambda request: answer)
    try:
        with socket.create_connection((HOST, port)) as s:
            s.settimeout(5)
            result = client.run_command(s, "big")
            check_is_dict(result)
            assert result == answer, "the dictionary is not the 3 MB answer the server sent"
    finally:
        listener.close()


def test_client_prompt_runs_commands(server):
    """python3 client.py reads commands, prints stdout, and ends on exit"""
    token = secrets.token_hex(8)
    result = run_prompt(f"echo {token}\nexit\n")
    assert result.returncode == 0, result.stderr
    assert token in result.stdout
