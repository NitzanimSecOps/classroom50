"""Tests for 8.1.0.2 UDP to TCP - change the given UDP server so it works over TCP.

Real sockets on 127.0.0.1. The student's server.py is started as its own process, which keeps
running between tests so a server that skips SO_REUSEADDR is not punished; it is restarted only
if a previous test crashed it.
"""
import os
import secrets
import socket
import subprocess
import sys
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
            time.sleep(0.3)

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
    """A socket connected to the student's server, or None if nothing is listening."""
    end = time.time() + timeout
    while time.time() < end:
        s = socket.socket()
        s.settimeout(2)
        try:
            s.connect((HOST, PORT))
            return s
        except OSError:
            s.close()
            time.sleep(0.1)
    return None


def ask(message):
    """One full exchange over TCP; returns the reply bytes (b"" if none)."""
    s = connect()
    assert s is not None, "nothing is listening for TCP on port 2405"
    with s:
        s.settimeout(3)
        s.sendall(message)
        try:
            return s.recv(1024)
        except OSError:
            return b""


def test_server_accepts_a_tcp_connection(server):
    """The server accepts a TCP connection on port 2405"""
    ask(b"hello")


def test_server_answers_with_the_message_reversed(server):
    """The server answers over TCP with the message reversed"""
    assert ask(b"meow") == b"woem"


def test_server_answer_depends_on_the_message(server):
    """The server reverses whatever message it gets, not a fixed text"""
    message = secrets.token_hex(8).encode()
    assert ask(message) == message[::-1]


def test_server_keeps_serving_more_clients(server):
    """The server keeps running and answers a second client after the first"""
    assert ask(b"first") == b"tsrif", "the first client got no correct answer"
    assert ask(b"second") == b"dnoces", "the server did not answer a second client"


def test_server_no_longer_uses_udp(server):
    """The server was changed to TCP: it no longer answers over UDP"""
    ask(b"hello")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as u:
        u.settimeout(1)
        u.sendto(b"meow", (HOST, PORT))
        with pytest.raises(OSError):
            u.recvfrom(1024)
