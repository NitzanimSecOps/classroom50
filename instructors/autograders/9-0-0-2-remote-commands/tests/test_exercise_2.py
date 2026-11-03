"""Tests for 9.0.0.2 Remote Commands - run commands on the server over one connection.

The student's server.py runs as its own process on 127.0.0.1 and is restarted only if it crashed.
These tests speak the wire format themselves. run_command is tested against a small fake server.
"""
import importlib
import json
import os
import secrets
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = "127.0.0.1"
PORT = 2405


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
    server = Server()
    yield server
    server.stop()


@pytest.fixture
def server(running):
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
            time.sleep(0.1)
    pytest.fail("nothing is listening on port 2405")


def read_exact(s, n):
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        assert chunk, "the connection closed in the middle of a message"
        data += chunk
    return data


def send_raw(s, data):
    s.sendall(struct.pack("!I", len(data)) + data)


def send(s, obj):
    send_raw(s, json.dumps(obj).encode())


def read(s):
    return json.loads(read_exact(s, struct.unpack("!I", read_exact(s, 4))[0]))


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
                    request = read(conn)
                    requests.append(request)
                    send(conn, answer(request))
            except (OSError, AssertionError):
                pass

    threading.Thread(target=serve, daemon=True).start()
    return listener.getsockname()[1], requests, listener


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
            assert client.run_command(s, "echo hi") == answer
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
            assert client.run_command(s, "big") == answer
    finally:
        listener.close()


def test_client_prompt_runs_commands(server):
    """python3 client.py reads commands, prints stdout, and ends on exit"""
    token = secrets.token_hex(8)
    result = run_prompt(f"echo {token}\nexit\n")
    assert result.returncode == 0, result.stderr
    assert token in result.stdout
