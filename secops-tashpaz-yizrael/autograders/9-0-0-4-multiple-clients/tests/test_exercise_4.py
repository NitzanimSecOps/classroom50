"""Tests for 9.0.0.4 Multiple Clients - one thread per client, and a who command.

The student's server.py runs as its own process on 127.0.0.1 and is restarted only if it crashed.
A blocked client is simulated by leaving a connection idle, or by not reading a large download.
"""
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
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.join(HERE, "files")
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


class Server:
    def __init__(self):
        self.proc = None

    def ensure_running(self):
        if self.proc is None or self.proc.poll() is not None:
            self.proc = subprocess.Popen(
                [sys.executable, os.path.join(HERE, "server.py")],
                cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)

    def stop(self):
        if self.proc is not None:
            self.proc.kill()
            self.proc.wait()


@pytest.fixture(scope="module")
def running():
    os.makedirs(FILES, exist_ok=True)
    server = Server()
    yield server
    server.stop()
    shutil.rmtree(FILES, ignore_errors=True)


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
        s.settimeout(3)
        try:
            s.connect((HOST, PORT))
            return s
        except OSError:
            s.close()
            time.sleep(0.1)
    pytest.fail("nothing is listening on port 2405")


def read_exact(s, n):
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        assert chunk, "the connection closed in the middle of a message"
        data += chunk
    return data


def send(s, obj):
    data = json.dumps(obj).encode()
    s.sendall(struct.pack("!I", len(data)) + data)


def read_raw(s):
    return read_exact(s, struct.unpack("!I", read_exact(s, 4))[0])


def read(s):
    return json.loads(read_raw(s))


def execute(s, cmd):
    send(s, {"type": "exec", "cmd": cmd})
    return read(s)


def who_ports(s):
    send(s, {"type": "who"})
    reply = read(s)
    assert reply["type"] == "clients", reply
    return [c["port"] for c in reply["clients"]]


def wait_until_gone(port):
    """True once port has left the who list, within 3 seconds."""
    end = time.time() + 3
    with connect() as s:
        while time.time() < end:
            if port not in who_ports(s):
                return True
            time.sleep(0.1)
    return False


def reset(s):
    """Close like a crashed client: the server gets a reset, not a clean close."""
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    s.close()


def test_two_clients_connect_together(server):
    """Two connected clients both get answers, taking turns"""
    with connect() as a, connect() as b:
        assert execute(a, "echo a")["stdout"].strip() == "a"
        assert execute(b, "echo b")["stdout"].strip() == "b"
        assert execute(a, "echo a2")["stdout"].strip() == "a2"


def test_idle_client_does_not_block(server):
    """A client that connected and sent nothing does not block the others"""
    with connect() as idle, connect() as other:
        assert execute(other, "echo fine")["stdout"].strip() == "fine"
        assert idle.fileno() != -1


def test_slow_command_does_not_block(server):
    """A client running a slow command does not block another client"""
    with connect() as slow, connect() as other:
        send(slow, {"type": "exec", "cmd": "sleep 2; echo slow"})
        time.sleep(0.1)
        start = time.time()
        assert execute(other, "echo fast")["stdout"].strip() == "fast"
        assert time.time() - start < 1, "the fast client waited for the slow one"
        assert read(slow)["stdout"].strip() == "slow"


def test_unread_download_does_not_block(server):
    """A client that stops reading a large download does not block another client"""
    name = put_file(secrets.token_bytes(1024 * 1024) * 30)
    with connect() as stuck, connect() as other:
        send(stuck, {"type": "download", "path": name})
        read(stuck)
        assert execute(other, "echo fine")["stdout"].strip() == "fine"


def test_many_clients_at_once(server):
    """20 clients running commands at the same time each get their own answer"""
    errors = []
    finished = []
    everyone_connected = threading.Barrier(20, timeout=10)

    def one_client(i):
        try:
            with connect() as s:
                for round_number in range(3):
                    token = f"{i}-{secrets.token_hex(4)}"
                    if execute(s, f"echo {token}")["stdout"].strip() != token:
                        errors.append(f"client {i} got someone else's answer")
                    if round_number == 0:
                        # nobody goes on until all 20 are connected and were answered once
                        everyone_connected.wait()
            finished.append(i)
        except BaseException as e:  # pytest.fail raises a BaseException
            errors.append(f"client {i}: {type(e).__name__} {e}")

    threads = [threading.Thread(target=one_client, args=(i,), daemon=True) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert not errors, errors
    assert len(finished) == 20, f"only {len(finished)} of 20 clients finished"


def test_downloads_do_not_mix(server):
    """Two clients downloading at the same time each get their own file"""
    contents = [secrets.token_bytes(8 * 1024 * 1024) for _ in range(2)]
    names = [put_file(c) for c in contents]
    results = {}
    errors = []
    first_chunk_read = threading.Event()
    other_finished = threading.Event()

    def fetch(i):
        # Client 0 stops reading after its first chunk, in the middle of its download,
        # and goes on only after client 1 has its whole file.
        try:
            with connect() as s:
                s.settimeout(15)
                send(s, {"type": "download", "path": names[i]})
                info = read(s)
                data = read_raw(s)
                if i == 0:
                    first_chunk_read.set()
                    if not other_finished.wait(timeout=10):
                        errors.append("client 1 could not download while client 0 was in the middle")
                while len(data) < info["size"]:
                    data += read_raw(s)
                results[i] = data
                if i == 1:
                    other_finished.set()
        except BaseException as e:
            errors.append(f"client {i}: {type(e).__name__} {e}")

    t0 = threading.Thread(target=fetch, args=(0,), daemon=True)
    t0.start()
    assert first_chunk_read.wait(timeout=10), errors
    t1 = threading.Thread(target=fetch, args=(1,), daemon=True)
    t1.start()
    t1.join(timeout=30)
    t0.join(timeout=30)
    assert not errors, errors
    assert results.get(0) == contents[0]
    assert results.get(1) == contents[1]


def test_who_lists_the_caller(server):
    """who answers with a clients list that holds the ip and port of the one asking"""
    with connect() as s:
        send(s, {"type": "who"})
        reply = read(s)
        assert reply["type"] == "clients"
        ip, port = s.getsockname()
        assert {"ip": ip, "port": port} in reply["clients"]


def test_who_lists_other_clients(server):
    """who lists the other connected clients too"""
    with connect() as a, connect() as b:
        execute(b, "true")
        ports = who_ports(a)
        assert a.getsockname()[1] in ports
        assert b.getsockname()[1] in ports


def test_who_removes_a_client_that_left(server):
    """A client that disconnected leaves the who list"""
    a = connect()
    port = a.getsockname()[1]
    execute(a, "true")
    a.close()
    assert wait_until_gone(port)


def test_who_removes_a_crashed_client(server):
    """A client that crashed leaves the who list"""
    a = connect()
    port = a.getsockname()[1]
    execute(a, "true")
    reset(a)
    assert wait_until_gone(port)


def test_who_removes_a_client_killed_mid_download(server):
    """A client killed in the middle of a download leaves the who list"""
    name = put_file(secrets.token_bytes(1024 * 1024) * 30)
    a = connect()
    port = a.getsockname()[1]
    send(a, {"type": "download", "path": name})
    read(a)
    reset(a)
    assert wait_until_gone(port)


def test_server_survives_a_crashed_client(server):
    """The server keeps serving after a client crashes"""
    a = connect()
    reset(a)
    with connect() as s:
        assert execute(s, "echo alive")["stdout"].strip() == "alive"


def test_client_who_returns_the_list(server):
    """who(sock) in client.py returns the list of clients"""
    import client
    importlib.reload(client)
    with connect() as s:
        ip, port = s.getsockname()
        assert {"ip": ip, "port": port} in client.who(s)


def test_client_prompt_who(server):
    """who in client.py prints the connected clients"""
    result = subprocess.run(
        [sys.executable, os.path.join(HERE, "client.py")],
        input="who\nexit\n", capture_output=True, text=True, timeout=20, cwd=HERE,
    )
    assert result.returncode == 0, result.stderr
    assert "127.0.0.1" in result.stdout
