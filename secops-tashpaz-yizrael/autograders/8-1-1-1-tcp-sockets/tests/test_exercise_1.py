"""Tests for 8.1.1.1 TCP Sockets - a TCP server on port 2405 and a contact_server client.

Real sockets on 127.0.0.1. The student's server.py is started as its own process for each server
test; the client is tested against a small fake server that records what it was sent.
The student's client.py is imported fresh for every test.
"""
import importlib
import os
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
REPLY = "Kululu"


@pytest.fixture
def server():
    """The student's server.py running as a separate process."""
    proc = subprocess.Popen(
        [sys.executable, os.path.join(HERE, "server.py")],
        cwd=HERE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    yield proc
    proc.kill()
    proc.wait()


def wait_for_port(proc, timeout=5.0):
    """Connect to the server; returns a connected socket or None."""
    end = time.time() + timeout
    while time.time() < end and proc.poll() is None:
        s = socket.socket()
        s.settimeout(2)
        try:
            s.connect((HOST, PORT))
            return s
        except OSError:
            s.close()
            time.sleep(0.1)
    return None


def hang_up(s):
    """Close with RST so no TIME_WAIT is left to block the next server from binding 2405."""
    s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    s.close()


def ask(proc, message):
    """One full exchange with the student's server; returns the reply bytes (b"" if none)."""
    s = wait_for_port(proc)
    assert s is not None, "nothing is listening on port 2405"
    try:
        s.settimeout(3)
        s.sendall(message)
        try:
            return s.recv(1024)
        except OSError:
            return b""
    finally:
        hang_up(s)


class FakeServer:
    """A server on a free port that answers every connection with a chosen text."""

    def __init__(self, reply):
        self.reply = reply
        self.received = []
        self.sock = socket.socket()
        self.sock.bind((HOST, 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

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
                    conn.sendall(self.reply.encode())
                except OSError:
                    pass

    def close(self):
        self.sock.close()


@pytest.fixture
def fake():
    servers = []

    def make(reply=REPLY):
        f = FakeServer(reply)
        servers.append(f)
        return f

    yield make
    for f in servers:
        f.close()


def contact(*args):
    import client
    importlib.reload(client)
    return client.contact_server(*args)


def test_server_listens_on_port_2405(server):
    """The server accepts a TCP connection on port 2405"""
    s = wait_for_port(server)
    assert s is not None, "nothing is listening on port 2405"
    hang_up(s)


def test_server_replies_kululu(server):
    """The server answers a message with Kululu"""
    assert ask(server, b"Hello") == b"Kululu"


def test_server_reply_does_not_depend_on_the_message(server):
    """The server answers Kululu whatever the message says"""
    assert ask(server, b"meow meow meow") == b"Kululu"


def test_client_returns_the_servers_answer(fake):
    """contact_server returns the server's answer"""
    f = fake("Kululu")
    assert contact(HOST, f.port) == "Kululu"


def test_client_returns_text_not_bytes(fake):
    """contact_server returns text (str), not bytes"""
    f = fake("Kululu")
    assert isinstance(contact(HOST, f.port), str)


def test_client_does_not_hardcode_the_answer(fake):
    """contact_server returns whatever the server answered"""
    f = fake("Purrr")
    assert contact(HOST, f.port) == "Purrr"


def test_client_sends_a_message(fake):
    """contact_server sends a message to the server"""
    f = fake()
    contact(HOST, f.port)
    assert f.received and f.received[0], "the server did not receive anything"


def test_client_uses_the_given_port(fake):
    """contact_server connects to the port it was given"""
    a, b = fake("one"), fake("two")
    assert contact(HOST, b.port) == "two"
    assert contact(HOST, a.port) == "one"
