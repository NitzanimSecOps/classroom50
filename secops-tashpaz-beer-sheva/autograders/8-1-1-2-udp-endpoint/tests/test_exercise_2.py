"""Tests for 8.1.1.2 UDP Endpoint - the file server also answers file sizes over UDP.

Real sockets on 127.0.0.1. The student's server.py is started as its own process, which keeps
running between tests; it is restarted only if a previous test crashed it. File names and contents
are random, so nothing can be hard-coded.
"""
import importlib
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = os.path.join(HERE, "files")
HOST = "127.0.0.1"
PORT = 2405
ERROR = b"ERROR: not found"


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
            time.sleep(0.1)
    return None


def fetch(name):
    """Over TCP: send a file name and read everything until the server closes."""
    s = connect()
    assert s is not None, "nothing is listening for TCP on port 2405"
    with s:
        s.sendall(name.encode())
        data = b""
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    return data
                data += chunk
        except OSError:
            return data


def ask_udp(name):
    """Over UDP: send a file name and return the answer, or None if nothing came back."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
        u.settimeout(2)
        u.sendto(name.encode(), (HOST, PORT))
        try:
            return u.recvfrom(1024)[0]
        except OSError:
            return None


class FakeUdp:
    """Answers each datagram with the given bytes, or stays silent if reply is None."""

    def __init__(self, reply):
        self.reply = reply
        self.received = []
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((HOST, 0))
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                data, addr = self.sock.recvfrom(1024)
            except OSError:
                return
            self.received.append(data)
            if self.reply is not None:
                self.sock.sendto(self.reply, addr)

    def close(self):
        self.sock.close()


@pytest.fixture
def fake():
    servers = []

    def make(reply):
        f = FakeUdp(reply)
        servers.append(f)
        return f

    yield make
    for f in servers:
        f.close()


def size_of(*args):
    import client
    importlib.reload(client)
    return client.file_size(*args)


def test_tcp_still_returns_files(server):
    """The server still sends files over TCP, as in the previous exercise"""
    content = secrets.token_hex(20).encode()
    assert fetch(put_file(content)) == content


def test_udp_returns_the_file_size(server):
    """Over UDP, the server answers with the size of the file in bytes"""
    assert ask_udp(put_file(b"x" * 1234)) == b"1234"


def test_udp_size_depends_on_the_file(server):
    """Over UDP, the size is read from the real file, not a fixed answer"""
    size = 1000 + secrets.randbelow(5000)
    assert ask_udp(put_file(b"x" * size)) == str(size).encode()


def test_udp_reports_a_missing_file(server):
    """Over UDP, a missing file gets the answer ERROR: not found"""
    assert ask_udp(secrets.token_hex(6) + ".txt") == ERROR


def test_udp_refuses_to_leave_the_folder(server):
    """Over UDP, a name that climbs out with ../ gets ERROR: not found"""
    name = "secret_" + secrets.token_hex(6) + ".txt"
    path = os.path.join(HERE, name)
    with open(path, "wb") as f:
        f.write(b"top secret")
    try:
        assert ask_udp("../" + name) == ERROR
    finally:
        os.remove(path)


def test_udp_answers_while_a_tcp_client_is_connected(server):
    """UDP is answered even while a TCP client is connected and silent"""
    idle = connect()
    assert idle is not None, "nothing is listening for TCP on port 2405"
    try:
        time.sleep(0.2)
        assert ask_udp(put_file(b"x" * 77)) == b"77", "UDP got no answer while TCP was busy"
    finally:
        # finish the request, so a server that can't handle an empty name isn't crashed by this test
        try:
            idle.sendall(b"hello.txt")
            idle.recv(1024)
        except OSError:
            pass
        idle.close()


def test_tcp_answers_after_udp_requests(server):
    """TCP still works right after UDP requests, and UDP right after TCP"""
    assert ask_udp(put_file(b"x" * 10)) == b"10"
    content = secrets.token_hex(20).encode()
    assert fetch(put_file(content)) == content
    assert ask_udp(put_file(b"x" * 11)) == b"11"


def test_client_returns_the_size_as_a_number(fake):
    """file_size returns the size as a number (int), not text"""
    f = fake(b"4321")
    assert size_of(HOST, f.port, "a") == 4321


def test_client_sends_the_file_name(fake):
    """file_size sends exactly the file name it was given"""
    f = fake(b"1")
    size_of(HOST, f.port, "cats.txt")
    assert f.received == [b"cats.txt"]


def test_client_handles_the_error_answer(fake):
    """On ERROR: not found, file_size returns None"""
    f = fake(ERROR)
    assert size_of(HOST, f.port, "a") is None


def test_client_gives_up_when_there_is_no_answer(fake):
    """If no answer arrives (UDP gives no promise), file_size returns None within 5 seconds"""
    f = fake(None)
    result = {}

    def call():
        result["value"] = size_of(HOST, f.port, "a")

    thread = threading.Thread(target=call, daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive(), "file_size is still waiting: use settimeout"
    assert result["value"] is None
