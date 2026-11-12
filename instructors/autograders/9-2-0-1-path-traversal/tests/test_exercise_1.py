"""Tests for 9.2.0.1 Path Traversal - a download may only reach real files inside files/.

The student's server.py runs as its own process on 127.0.0.1 and is restarted only if it crashed.
Every test builds its own files: files/ (with a subfolder), a secret file next to server.py, a
sibling folder files_secret/ and a symlink inside files/ that points at the secret. They are removed
again after the test.
"""
import json
import os
import secrets
import shutil
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
FILES = os.path.join(HERE, "files")
HOST = "127.0.0.1"
PORT = 2405
CHUNK_SIZE = 65536


# Hard time limit per test. A student's endless loop (or a call that never returns) would otherwise
# hang the whole run until the grader is killed and no score is posted at all.
TEST_LIMIT = 10  # seconds, for every test not listed below
LIMITS = {}
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
    os.makedirs(FILES, exist_ok=True)
    SERVER = Server()
    yield SERVER
    SERVER.stop()
    SERVER = None
    shutil.rmtree(FILES, ignore_errors=True)


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


def read_raw(s):
    """One framed message from the server, with a failure message that says what went wrong."""
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


def read(s):
    """A JSON answer from the server, with a failure message that says what actually came back."""
    body = read_raw(s)
    try:
        reply = json.loads(body)
    except ValueError:
        reply = None
    if reply is None or not isinstance(reply, dict):
        pytest.fail(
            f"expected a JSON message {{...}} here, but got {short(body)}\n"
            "(is the server sending something extra, or in a different order?)" + server_crashed()
        )
    record("got", reply)
    return reply


def fetch(s, name):
    """Ask for a file and return (file_info, list of chunks)."""
    send(s, {"type": "download", "path": name})
    info = read(s)
    assert info["type"] == "file_info", info
    chunks = []
    received = 0
    while received < info["size"]:
        chunks.append(read_raw(s))
        received += len(chunks[-1])
    return info, chunks



SECRET = b"SECRET-" + secrets.token_hex(8).encode()


@pytest.fixture
def scene(server):
    """Files inside files/ and the places a traversal would reach, removed again afterwards."""
    sub = os.path.join(FILES, "sub")
    outer = os.path.join(HERE, "secret.txt")
    sibling = os.path.join(HERE, "files_secret")
    made = {
        "plain": (os.path.join(FILES, "plain.txt"), b"plain " + secrets.token_bytes(40)),
        "sub": (os.path.join(sub, "a.txt"), b"in the subfolder " + secrets.token_bytes(40)),
        "dots": (os.path.join(FILES, "a..b.txt"), b"dots " + secrets.token_bytes(40)),
        "secret": (outer, SECRET),
        "sibling": (os.path.join(sibling, "x.txt"), SECRET),
    }
    try:
        for path, content in made.values():
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as f:
                f.write(content)
        os.symlink(outer, os.path.join(FILES, "link"))
        yield made
    finally:
        for name in ("link",):
            try:
                os.remove(os.path.join(FILES, name))
            except OSError:
                pass
        shutil.rmtree(sub, ignore_errors=True)
        shutil.rmtree(sibling, ignore_errors=True)
        for path in (outer, os.path.join(FILES, "plain.txt"), os.path.join(FILES, "a..b.txt")):
            try:
                os.remove(path)
            except OSError:
                pass


def downloads(s, name, content):
    _, chunks = fetch(s, name)
    assert b"".join(chunks) == content, "the file arrived with different content"


def refused(s, *names):
    """Every name must get exactly the not found error."""
    for name in names:
        send(s, {"type": "download", "path": name})
        assert read(s) == {"type": "error", "message": "not found"}, f"{name!r} was not refused"


def test_normal_file_downloads(scene):
    """A normal file in files still downloads"""
    with connect() as s:
        downloads(s, "plain.txt", scene["plain"][1])


def test_file_in_subfolder_downloads(scene):
    """A file in a subfolder of files (sub/a.txt) downloads"""
    with connect() as s:
        downloads(s, "sub/a.txt", scene["sub"][1])


def test_name_with_dots_downloads(scene):
    """A legal name with dots in it (a..b.txt) downloads"""
    with connect() as s:
        downloads(s, "a..b.txt", scene["dots"][1])


def test_parent_folder_is_refused(scene):
    """A name that climbs out with ../ gets a not found error"""
    with connect() as s:
        refused(s, "../secret.txt", "../server.py")


def test_absolute_path_is_refused(scene):
    """A full path gets a not found error"""
    with connect() as s:
        refused(s, os.path.join(HERE, "secret.txt"), "/etc/passwd")


def test_leaving_through_a_subfolder_is_refused(scene):
    """A name that goes into a subfolder and climbs out (sub/../../) gets a not found error"""
    with connect() as s:
        refused(s, "sub/../../secret.txt")


def test_sibling_folder_is_refused(scene):
    """A folder next to files whose name starts with files gets a not found error"""
    with connect() as s:
        refused(s, "../files_secret/x.txt")


def test_symlink_out_of_the_folder_is_refused(scene):
    """A symlink inside files that points outside it gets a not found error"""
    with connect() as s:
        refused(s, "link")


def test_connection_works_after_a_refusal(scene):
    """After a refused request the connection keeps working"""
    with connect() as s:
        refused(s, "../secret.txt")
        send(s, {"type": "exec", "cmd": "echo alive"})
        assert read(s)["stdout"].strip() == "alive"
        downloads(s, "plain.txt", scene["plain"][1])
        refused(s, "/etc/passwd")
