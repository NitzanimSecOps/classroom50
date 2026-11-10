"""Tests for 9.0.0.5 Parallel Streams - a control connection, and data connections with tokens.

The student's server.py runs as its own process on 127.0.0.1 and is restarted only if it crashed.
start_download is tested against a small fake server that sends slowly, so a download that blocks
the caller can be told apart from one that runs in the background.
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
LIMITS = {
    "test_client_prompt_downloads_and_runs_commands": 15,
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
        s.settimeout(5)
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


def execute(s, cmd):
    send(s, {"type": "exec", "cmd": cmd})
    return read(s)


def ask_transfer(control, name):
    send(control, {"type": "download", "path": name})
    reply = read(control)
    assert reply["type"] == "transfer", reply
    return reply


def open_data(token):
    s = connect()
    send(s, {"type": "data", "token": token})
    return s


def read_chunks(s, size):
    chunks = []
    received = 0
    while received < size:
        chunks.append(read_raw(s))
        received += len(chunks[-1])
    return chunks


class FakeServer:
    """A control and data server. Sends the file in pieces with a pause between them."""

    def __init__(self, content, sha256=None, pause=0.1, pieces=20, port=0):
        self.content = content
        self.sha256 = sha256 or hashlib.sha256(content).hexdigest()
        self.pause = pause
        self.pieces = pieces
        self.token = secrets.token_hex(8)
        self.data_tokens = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((HOST, port))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.accept, daemon=True).start()

    def accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self.serve, args=(conn,), daemon=True).start()

    def serve(self, conn):
        with conn:
            conn.settimeout(10)
            try:
                while True:
                    request = read_message(conn)
                    if request["type"] == "download":
                        send(conn, {
                            "type": "transfer",
                            "token": self.token,
                            "size": len(self.content),
                            "sha256": self.sha256,
                        })
                    elif request["type"] == "exec":
                        cmd = request["cmd"]
                        out = cmd[5:] + "\n" if cmd.startswith("echo ") else "ok"
                        send(conn, {"type": "result", "stdout": out, "stderr": "", "exit_code": 0})
                    elif request["type"] == "data":
                        self.data_tokens.append(request["token"])
                        send(conn, {"type": "ok"})
                        step = max(1, len(self.content) // self.pieces)
                        for i in range(0, len(self.content), step):
                            send_raw(conn, self.content[i:i + step])
                            time.sleep(self.pause)
                        return
            except (OSError, AssertionError):
                pass

    def close(self):
        self.sock.close()


@pytest.fixture
def fake():
    servers = []

    def make(*args, **kwargs):
        f = FakeServer(*args, **kwargs)
        servers.append(f)
        return f

    yield make
    for f in servers:
        f.close()


def load_client():
    import client
    return importlib.reload(client)


def wait_done(transfer, seconds=10):
    end = time.time() + seconds
    while time.time() < end and not transfer["done"]:
        time.sleep(0.05)
    assert transfer["done"], "the transfer did not finish"


def test_server_answers_with_a_transfer(server):
    """download on the control connection answers with a token, the size and the SHA-256"""
    content = secrets.token_bytes(1000)
    with connect() as control:
        reply = ask_transfer(control, put_file(content))
    assert reply["size"] == 1000
    assert reply["sha256"] == hashlib.sha256(content).hexdigest()
    assert isinstance(reply["token"], str) and reply["token"]


def test_server_sends_no_file_on_the_control_connection(server):
    """No file data arrives on the control connection, so the next answer is the next command's"""
    with connect() as control:
        ask_transfer(control, put_file(secrets.token_bytes(100000)))
        assert execute(control, "echo clean")["stdout"].strip() == "clean"


def test_data_connection_delivers_the_file(server):
    """A second connection with the token gets ok, then the file in chunks of up to 64 KB"""
    content = secrets.token_bytes(3 * 1024 * 1024)
    with connect() as control:
        reply = ask_transfer(control, put_file(content))
        with open_data(reply["token"]) as data:
            assert read(data) == {"type": "ok"}
            chunks = read_chunks(data, reply["size"])
    assert b"".join(chunks) == content
    assert max(map(len, chunks)) <= CHUNK_SIZE


def test_data_connection_closes_at_the_end(server):
    """The server closes the data connection after the last chunk"""
    with connect() as control:
        reply = ask_transfer(control, put_file(b"short file"))
        with open_data(reply["token"]) as data:
            read(data)
            read_chunks(data, reply["size"])
            assert data.recv(1) == b""


def test_empty_file_is_delivered(server):
    """An empty file gets ok on the data connection, and no chunks"""
    with connect() as control:
        reply = ask_transfer(control, put_file(b""))
        assert reply["size"] == 0
        with open_data(reply["token"]) as data:
            assert read(data) == {"type": "ok"}
            assert data.recv(1) == b""


def test_invalid_token_is_refused(server):
    """An unknown token gets an invalid token error"""
    with open_data(secrets.token_hex(16)) as data:
        assert read(data) == {"type": "error", "message": "invalid token"}


def test_token_works_only_once(server):
    """A token that was already used gets an invalid token error"""
    with connect() as control:
        reply = ask_transfer(control, put_file(b"once"))
        with open_data(reply["token"]) as data:
            assert read(data) == {"type": "ok"}
            read_chunks(data, reply["size"])
        with open_data(reply["token"]) as data:
            assert read(data) == {"type": "error", "message": "invalid token"}


def test_tokens_are_different(server):
    """Each download gets its own unpredictable token"""
    name = put_file(b"same file")
    with connect() as control:
        tokens = {ask_transfer(control, name)["token"] for _ in range(5)}
    assert len(tokens) == 5
    assert all(len(t) >= 16 for t in tokens), "a short token can be guessed"


def test_missing_file_is_refused(server):
    """A file that does not exist gets a not found error, and no token"""
    with connect() as control:
        send(control, {"type": "download", "path": secrets.token_hex(6) + ".txt"})
        assert read(control) == {"type": "error", "message": "not found"}


def test_leaving_the_folder_is_refused(server):
    """A name that climbs out with ../ gets a not found error, and no token"""
    name = "secret_" + secrets.token_hex(6) + ".txt"
    path = os.path.join(HERE, name)
    with open(path, "wb") as f:
        f.write(b"top secret")
    try:
        with connect() as control:
            send(control, {"type": "download", "path": "../" + name})
            assert read(control) == {"type": "error", "message": "not found"}
    finally:
        os.remove(path)


def test_commands_work_during_a_transfer(server):
    """Commands on the control connection get answers while a large transfer is in progress"""
    name = put_file(secrets.token_bytes(1024 * 1024) * 30)
    with connect() as control:
        reply = ask_transfer(control, name)
        with open_data(reply["token"]) as data:
            assert read(data) == {"type": "ok"}
            start = time.time()
            assert execute(control, "echo alive")["stdout"].strip() == "alive"
            assert time.time() - start < 1


def test_two_transfers_run_together(server):
    """Two downloads from one client run at the same time and both arrive whole"""
    contents = [secrets.token_bytes(2 * 1024 * 1024) for _ in range(2)]
    with connect() as control:
        replies = [ask_transfer(control, put_file(c)) for c in contents]
        streams = [open_data(r["token"]) for r in replies]
        try:
            for data in streams:
                assert read(data) == {"type": "ok"}
            results = [b"", b""]
            while any(len(r) < 2 * 1024 * 1024 for r in results):
                for i, data in enumerate(streams):
                    if len(results[i]) < 2 * 1024 * 1024:
                        results[i] += read_raw(data)
        finally:
            for data in streams:
                data.close()
    assert results == contents


def test_server_survives_a_killed_transfer(server):
    """The server keeps serving after a data connection dies in the middle of a file"""
    name = put_file(secrets.token_bytes(1024 * 1024) * 30)
    with connect() as control:
        reply = ask_transfer(control, name)
        data = open_data(reply["token"])
        read(data)
        data.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
        data.close()
        assert execute(control, "echo alive")["stdout"].strip() == "alive"


def test_client_returns_right_away(fake, tmp_path):
    """start_download returns immediately, while the file is still arriving"""
    client = load_client()
    f = fake(secrets.token_bytes(100000))
    with socket.create_connection((HOST, f.port)) as s:
        s.settimeout(5)
        start = time.time()
        transfer = client.start_download(s, HOST, f.port, "a", str(tmp_path / "out"))
        assert time.time() - start < 1, "start_download waited for the whole file"
        assert transfer["done"] is False
        wait_done(transfer)


def test_client_saves_the_file(fake, tmp_path):
    """The background download saves the file, and sets done and ok to True"""
    client = load_client()
    content = secrets.token_bytes(100000)
    f = fake(content, pause=0.01)
    dest = tmp_path / "out"
    with socket.create_connection((HOST, f.port)) as s:
        s.settimeout(5)
        transfer = client.start_download(s, HOST, f.port, "a", str(dest))
        wait_done(transfer)
    assert transfer["ok"] is True
    assert dest.read_bytes() == content
    assert f.data_tokens == [f.token]


def test_client_control_connection_stays_free(fake, tmp_path):
    """run_command works on the control connection while a download runs"""
    client = load_client()
    f = fake(secrets.token_bytes(100000))
    with socket.create_connection((HOST, f.port)) as s:
        s.settimeout(5)
        transfer = client.start_download(s, HOST, f.port, "a", str(tmp_path / "out"))
        assert client.run_command(s, "anything")["stdout"] == "ok"
        assert transfer["done"] is False
        wait_done(transfer)


def test_client_tracks_progress(fake, tmp_path):
    """received grows during the download, and TRANSFERS holds the transfer"""
    client = load_client()
    f = fake(secrets.token_bytes(100000))
    with socket.create_connection((HOST, f.port)) as s:
        s.settimeout(5)
        transfer = client.start_download(s, HOST, f.port, "a", str(tmp_path / "out"))
        assert transfer in client.TRANSFERS
        assert transfer["name"] == "a"
        assert transfer["size"] == 100000
        time.sleep(0.5)
        assert 0 < transfer["received"] < 100000
        wait_done(transfer)
        assert transfer["received"] == 100000


def test_client_rejects_a_wrong_hash(fake, tmp_path):
    """On a wrong SHA-256, ok is False, done is True, and no file is left at dest"""
    client = load_client()
    f = fake(b"corrupted content", sha256="0" * 64, pause=0.01)
    dest = tmp_path / "out"
    with socket.create_connection((HOST, f.port)) as s:
        s.settimeout(5)
        transfer = client.start_download(s, HOST, f.port, "a", str(dest))
        wait_done(transfer)
    assert transfer["ok"] is False
    assert not dest.exists()


def test_client_returns_none_when_refused(server, tmp_path):
    """start_download returns None when the server refuses the file"""
    client = load_client()
    with connect() as s:
        name = secrets.token_hex(6) + ".txt"
        assert client.start_download(s, HOST, PORT, name, str(tmp_path / "out")) is None


def test_client_prompt_downloads_and_runs_commands(running, fake, tmp_path):
    """In client.py, download runs in the background, and transfers shows it"""
    # A fake server takes port 2405 for this test and sends the file slowly (about 3 seconds).
    running.stop()
    content = secrets.token_bytes(100000)
    fake(content, pause=0.15, port=PORT)
    name = "slow_" + secrets.token_hex(4) + ".bin"
    token = secrets.token_hex(8)
    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "client.py")],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, cwd=tmp_path, env=env,
    )
    lines = []

    def pump():
        for line in proc.stdout:
            lines.append(line)

    threading.Thread(target=pump, daemon=True).start()

    def wait_for(text, seconds, after=0):
        end = time.time() + seconds
        while time.time() < end:
            if text in "".join(lines)[after:]:
                return True
            time.sleep(0.05)
        return False

    dest = tmp_path / name
    try:
        proc.stdin.write(f"download {name}\n")
        proc.stdin.write(f"echo {token}\n")
        proc.stdin.flush()
        assert wait_for(token, 2), "the command after download waited for the download to finish"
        seen = len("".join(lines))
        proc.stdin.write("transfers\n")
        proc.stdin.flush()
        assert wait_for(name, 3, after=seen), "transfers did not show the download"
        end = time.time() + 15
        while time.time() < end and not dest.exists():
            time.sleep(0.05)
        proc.stdin.write("exit\n")
        proc.stdin.flush()
        proc.wait(timeout=20)
    finally:
        proc.kill()
    assert proc.returncode == 0, "".join(lines)
    assert dest.read_bytes() == content
