"""Tests for 8.1.1.1 File Server - a TCP file server and a download_file client.

Real sockets on 127.0.0.1. The student's server.py is started as its own process, which keeps
running between tests (the server closes first, so its port sits in TIME_WAIT and a restart could
fail to bind); it is restarted only if a previous test crashed it. File names and contents are
random, so nothing can be hard-coded. The client is tested against a small fake server.
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
            time.sleep(0.3)

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
    """Send a file name and read everything until the server closes the connection."""
    s = connect()
    assert s is not None, "nothing is listening on port 2405"
    with s:
        s.sendall(name.encode())
        data = b""
        try:
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    return data
                data += chunk
        except socket.timeout:
            pytest.fail("the server did not close the connection after sending")
        except OSError:
            return data


class FakeServer:
    """Answers every connection with the given chunks, slowly, then closes."""

    def __init__(self, chunks):
        self.chunks = chunks
        self.received = []
        self.sock = socket.socket()
        self.sock.bind((HOST, 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                conn.settimeout(3)
                try:
                    self.received.append(conn.recv(1024))
                    for chunk in self.chunks:
                        conn.sendall(chunk)
                        time.sleep(0.05)
                except OSError:
                    pass

    def close(self):
        self.sock.close()


@pytest.fixture
def fake():
    servers = []

    def make(*chunks):
        f = FakeServer(chunks)
        servers.append(f)
        return f

    yield make
    for f in servers:
        f.close()


def download(*args):
    import client
    importlib.reload(client)
    return client.download_file(*args)


def test_server_listens_on_port_2405(server):
    """The server accepts a TCP connection on port 2405"""
    s = connect()
    assert s is not None, "nothing is listening on port 2405"
    with s:
        s.sendall(b"hello.txt")
        try:
            s.recv(1)
        except OSError:
            pass


def test_server_returns_the_file(server):
    """The server sends the content of the requested file from files/, then closes"""
    content = secrets.token_hex(20).encode()
    assert fetch(put_file(content)) == content


def test_server_handles_binary_files(server):
    """The server sends binary files unchanged (read them as bytes, not text)"""
    content = bytes(range(256)) * 4
    assert fetch(put_file(content)) == content


def test_server_sends_large_files_whole(server):
    """The server sends a 1 MB file whole, not only the first part"""
    content = secrets.token_bytes(1024 * 1024)
    assert fetch(put_file(content)) == content


def test_server_reports_a_missing_file(server):
    """A file that does not exist gets the answer ERROR: not found"""
    assert fetch(secrets.token_hex(6) + ".txt") == ERROR


def test_server_refuses_to_leave_the_folder(server):
    """A name that climbs out with ../ gets ERROR: not found, not the outside file"""
    name = "secret_" + secrets.token_hex(6) + ".txt"
    path = os.path.join(HERE, name)
    with open(path, "wb") as f:
        f.write(b"top secret")
    try:
        assert fetch("../" + name) == ERROR
    finally:
        os.remove(path)


def test_server_refuses_an_absolute_path(server):
    """A full path like /etc/passwd gets ERROR: not found, not the outside file"""
    name = "secret_" + secrets.token_hex(6) + ".txt"
    path = os.path.join(HERE, name)
    with open(path, "wb") as f:
        f.write(b"top secret")
    try:
        assert fetch(path) == ERROR
    finally:
        os.remove(path)


def test_server_keeps_serving_more_clients(server):
    """The server keeps running after a request and after an error"""
    assert fetch(secrets.token_hex(6) + ".txt") == ERROR
    content = secrets.token_hex(20).encode()
    assert fetch(put_file(content)) == content, "the server did not answer the next client"


def test_client_sends_the_file_name(fake, tmp_path):
    """download_file sends exactly the file name it was given"""
    f = fake(b"data")
    download(HOST, f.port, "cats.txt", str(tmp_path / "out"))
    assert f.received == [b"cats.txt"]


def test_client_saves_and_returns_the_size(fake, tmp_path):
    """download_file saves the answer to dest and returns the number of bytes"""
    f = fake(b"meow meow")
    dest = tmp_path / "out"
    assert download(HOST, f.port, "a", str(dest)) == 9
    assert dest.read_bytes() == b"meow meow"


def test_client_reads_until_the_server_closes(fake, tmp_path):
    """download_file keeps reading until the server closes, not just one recv"""
    chunks = [secrets.token_bytes(30000) for _ in range(4)]
    f = fake(*chunks)
    dest = tmp_path / "out"
    assert download(HOST, f.port, "a", str(dest)) == 120000
    assert dest.read_bytes() == b"".join(chunks)


def test_client_saves_binary_data(fake, tmp_path):
    """download_file saves binary data unchanged (write it as bytes, not text)"""
    content = bytes(range(256))
    f = fake(content)
    dest = tmp_path / "out"
    download(HOST, f.port, "a", str(dest))
    assert dest.read_bytes() == content


def test_client_handles_the_error_answer(fake, tmp_path):
    """On ERROR: not found, download_file returns None and creates no file"""
    f = fake(ERROR)
    dest = tmp_path / "out"
    assert download(HOST, f.port, "a", str(dest)) is None
    assert f.received, "download_file did not ask the server"
    assert not dest.exists()


def test_client_uses_the_given_port(fake, tmp_path):
    """download_file connects to the port it was given"""
    a, b = fake(b"one"), fake(b"two")
    dest = tmp_path / "out"
    download(HOST, b.port, "x", str(dest))
    assert dest.read_bytes() == b"two"
    download(HOST, a.port, "x", str(dest))
    assert dest.read_bytes() == b"one"
