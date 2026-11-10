"""Tests for 9.0.0.3 File Download - send files in chunks and verify them with a hash.

The student's server.py runs as its own process on 127.0.0.1 and is restarted only if it crashed.
File names and contents are random. download is tested against a small fake server, which is the
only way to hand it a corrupted file.
"""
import hashlib
import importlib
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


def put_file(content):
    name = secrets.token_hex(6) + ".bin"
    with open(os.path.join(FILES, name), "wb") as f:
        f.write(content)
    return name


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


def fake_server(content, sha256=None, size=None, cut_after=None):
    """Answers one download with the given content, optionally lying or stopping early."""
    listener = socket.socket()
    listener.bind((HOST, 0))
    listener.listen()
    requests = []

    def serve():
        conn, _ = listener.accept()
        with conn:
            conn.settimeout(5)
            requests.append(read_message(conn))
            send(conn, {
                "type": "file_info",
                "size": len(content) if size is None else size,
                "sha256": sha256 or hashlib.sha256(content).hexdigest(),
            })
            data = content if cut_after is None else content[:cut_after]
            for i in range(0, len(data), CHUNK_SIZE):
                send_raw(conn, data[i:i + CHUNK_SIZE])
            try:
                conn.recv(1)
            except OSError:
                pass

    threading.Thread(target=serve, daemon=True).start()
    return listener, requests


def client_download(listener, name, dest):
    import client
    importlib.reload(client)
    with socket.create_connection(listener.getsockname()) as s:
        s.settimeout(5)
        return client.download(s, name, str(dest))


def test_server_announces_the_file(server):
    """download answers first with file_info holding the size and the SHA-256"""
    content = secrets.token_bytes(1000)
    with connect() as s:
        info, _ = fetch(s, put_file(content))
    assert info == {
        "type": "file_info",
        "size": 1000,
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def test_server_sends_the_content(server):
    """After file_info, the chunks put together are exactly the file"""
    content = bytes(range(256)) * 40
    with connect() as s:
        _, chunks = fetch(s, put_file(content))
    assert b"".join(chunks) == content


def test_server_sends_in_chunks(server):
    """A 3 MB file arrives in several messages, none above 64 KB"""
    content = secrets.token_bytes(3 * 1024 * 1024)
    with connect() as s:
        _, chunks = fetch(s, put_file(content))
    assert b"".join(chunks) == content
    assert len(chunks) > 1, "the file was sent as one message"
    assert max(map(len, chunks)) <= CHUNK_SIZE


def test_server_handles_an_empty_file(server):
    """An empty file gets file_info with size 0, and no chunks"""
    with connect() as s:
        info, chunks = fetch(s, put_file(b""))
        assert info["size"] == 0
        assert chunks == []
        send(s, {"type": "exec", "cmd": "echo alive"})
        assert read(s)["stdout"].strip() == "alive"


def test_server_reports_a_missing_file(server):
    """A file that does not exist gets a not found error"""
    with connect() as s:
        send(s, {"type": "download", "path": secrets.token_hex(6) + ".txt"})
        assert read(s) == {"type": "error", "message": "not found"}


def test_server_keeps_the_connection_after_a_download(server):
    """Commands and more downloads keep working on the same connection"""
    content = secrets.token_bytes(200000)
    with connect() as s:
        _, chunks = fetch(s, put_file(content))
        assert b"".join(chunks) == content
        send(s, {"type": "exec", "cmd": "echo alive"})
        assert read(s)["stdout"].strip() == "alive"
        _, chunks = fetch(s, put_file(b"again"))
        assert b"".join(chunks) == b"again"


def test_server_survives_a_client_leaving_mid_download(server):
    """The server survives a client that disconnects in the middle of a download"""
    name = put_file(secrets.token_bytes(20 * 1024 * 1024))
    with connect() as s:
        send(s, {"type": "download", "path": name})
        assert read(s)["type"] == "file_info", "the download did not start"
        read_raw(s)
    time.sleep(0.2)
    with connect() as s:
        send(s, {"type": "exec", "cmd": "echo alive"})
        assert read(s)["stdout"].strip() == "alive"


def test_client_saves_the_file(tmp_path):
    """download asks for the file, saves it to dest and returns the size"""
    content = secrets.token_bytes(300000)
    listener, requests = fake_server(content)
    dest = tmp_path / "out.bin"
    try:
        assert client_download(listener, "cats.bin", dest) == 300000
    finally:
        listener.close()
    assert requests == [{"type": "download", "path": "cats.bin"}]
    assert dest.read_bytes() == content


def test_client_handles_an_empty_file(tmp_path):
    """download saves an empty file and returns 0"""
    listener, _ = fake_server(b"")
    dest = tmp_path / "out.bin"
    try:
        assert client_download(listener, "empty", dest) == 0
    finally:
        listener.close()
    assert dest.read_bytes() == b""


def test_client_rejects_a_wrong_hash(tmp_path):
    """On a wrong SHA-256, download returns None and leaves no file at dest"""
    listener, _ = fake_server(b"corrupted content", sha256="0" * 64)
    dest = tmp_path / "out.bin"
    try:
        assert client_download(listener, "a", dest) is None
    finally:
        listener.close()
    assert not dest.exists()


def test_client_handles_a_lost_connection(tmp_path):
    """If the connection closes mid-file, download returns None and leaves no file at dest"""
    content = secrets.token_bytes(100000)
    listener = socket.socket()
    listener.bind((HOST, 0))
    listener.listen()

    def serve():
        conn, _ = listener.accept()
        with conn:
            read_message(conn)
            send(conn, {
                "type": "file_info",
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            })
            send_raw(conn, content[:1000])

    threading.Thread(target=serve, daemon=True).start()
    dest = tmp_path / "out.bin"
    try:
        assert client_download(listener, "a", dest) is None
    finally:
        listener.close()
    assert not dest.exists()


def test_client_handles_the_error_answer(tmp_path):
    """On an error answer, download returns None and creates no file"""
    listener = socket.socket()
    listener.bind((HOST, 0))
    listener.listen()

    def serve():
        conn, _ = listener.accept()
        with conn:
            read_message(conn)
            send(conn, {"type": "error", "message": "not found"})
            try:
                conn.recv(1)
            except OSError:
                pass

    threading.Thread(target=serve, daemon=True).start()
    dest = tmp_path / "out.bin"
    try:
        assert client_download(listener, "a", dest) is None
    finally:
        listener.close()
    assert not dest.exists()


def test_client_prompt_downloads(server, tmp_path):
    """download <name> in client.py saves the file to the current folder, and commands still run"""
    content = secrets.token_bytes(100000)
    name = put_file(content)
    token = secrets.token_hex(8)
    result = subprocess.run(
        [sys.executable, os.path.join(HERE, "client.py")],
        input=f"download {name}\necho {token}\nexit\n",
        capture_output=True, text=True, timeout=20, cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / name).read_bytes() == content
    assert token in result.stdout
