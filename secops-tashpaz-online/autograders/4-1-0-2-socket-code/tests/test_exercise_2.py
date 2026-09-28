"""Tests for 4.1.0.2 Socket Code.

A raw packet socket needs root and a live interface, which the grader doesn't have, so
socket.socket is replaced with a mock and the tests check what the code asks the OS for.
"""
import importlib
import socket
from unittest.mock import MagicMock

import pytest

ETH_P_ALL = 0x0003
MAX_FRAME = 1514   # largest Ethernet frame without the FCS


@pytest.fixture
def sol(monkeypatch):
    # AF_PACKET and ETH_P_ALL exist only on Linux; stand-ins let the tests run elsewhere too.
    monkeypatch.setattr(socket, "AF_PACKET", getattr(socket, "AF_PACKET", 17), raising=False)
    monkeypatch.setattr(socket, "ETH_P_ALL", getattr(socket, "ETH_P_ALL", ETH_P_ALL), raising=False)
    fake = MagicMock(name="socket.socket")
    monkeypatch.setattr(socket, "socket", fake)
    import solution
    solution = importlib.reload(solution)   # so `from socket import socket` gets the mock too
    fake.reset_mock()                       # ignore anything the module did at import time
    return solution, fake


def _family_type_proto(call):
    args, kwargs = call
    names = ("family", "type", "proto")
    values = list(args) + [kwargs.get(name) for name in names[len(args):]]
    return tuple(values[:3])


def test_make_socket_creates_a_raw_packet_socket(sol):
    """make_socket() creates socket(AF_PACKET, SOCK_RAW, htons(ETH_P_ALL))."""
    solution, fake = sol
    solution.make_socket("veth-test")
    assert fake.call_count == 1
    assert _family_type_proto(fake.call_args) == (
        socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))


def test_make_socket_binds_to_the_given_interface(sol):
    """make_socket() binds the socket to the interface it was given."""
    solution, fake = sol
    solution.make_socket("veth-test")
    sock = fake.return_value
    assert sock.bind.call_count == 1
    address = sock.bind.call_args.args[0] if sock.bind.call_args.args else None
    assert address is not None and address[0] == "veth-test"


def test_make_socket_returns_the_socket(sol):
    """make_socket() returns the socket it created."""
    solution, fake = sol
    assert solution.make_socket("veth-test") is fake.return_value


def test_receive_frame_returns_what_recv_read(sol):
    """receive_frame() returns the bytes recv() read from the socket."""
    solution, _ = sol
    sock = MagicMock()
    sock.recv.return_value = bytes.fromhex("ffffffffffff" "a12f3fbe6c12" "0806") + b"payload"
    assert solution.receive_frame(sock) == sock.recv.return_value


def test_receive_frame_buffer_fits_a_full_frame(sol):
    """receive_frame() asks recv() for at least 1514 bytes, enough for any Ethernet frame."""
    solution, _ = sol
    sock = MagicMock()
    sock.recv.return_value = b""
    solution.receive_frame(sock)
    call = sock.recv.call_args
    bufsize = call.args[0] if call.args else call.kwargs.get("bufsize")
    assert bufsize is not None and bufsize >= MAX_FRAME


def test_close_socket_closes_it(sol):
    """close_socket() closes the socket it was given."""
    solution, _ = sol
    sock = MagicMock()
    solution.close_socket(sock)
    sock.close.assert_called_once()
