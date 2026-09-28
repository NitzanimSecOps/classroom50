import socket
import struct

import pytest

import chat
from chat import ChatClient

OTHER_MAC = "02:00:00:00:00:99"   # a peer that is not us


class FakeSocket:
    """Stand-in for the raw AF_PACKET socket: records bind() and send() so the
    tests need neither root nor a real interface."""

    def __init__(self, *args):
        self.bound = None
        self.sent: list[bytes] = []

    def bind(self, addr):
        self.bound = addr

    def send(self, data: bytes) -> int:
        self.sent.append(data)
        return len(data)


def build_frame(src_mac: str, nickname: str, message: str) -> bytes:
    """Build a valid L2CHAT frame from `src_mac`, per the protocol."""
    nick = nickname.encode()
    msg = message.encode()
    payload = struct.pack("!B", len(nick)) + nick + struct.pack("!H", len(msg)) + msg
    return (chat.mac_to_bytes(chat.BROADCAST) + chat.mac_to_bytes(src_mac)
            + struct.pack("!H", chat.ETH_P_CHAT) + payload)


@pytest.fixture
def client(monkeypatch):
    # swap the real raw socket for a fake so __init__ needs no root/interface
    monkeypatch.setattr(socket, "socket", FakeSocket)
    return ChatClient()


# --- __init__ ---------------------------------------------------------------

def test_init_binds_socket_to_the_interface(client):
    assert client.sock.bound == (chat.INTERFACE, 0), (
        f"socket should be bound to the configured interface, got {client.sock.bound!r}"
    )


# --- send -------------------------------------------------------------------

def test_send_builds_a_wellformed_chat_frame(client):
    client.send("hi")

    assert len(client.sock.sent) == 1, "send should transmit exactly one frame"
    frame = client.sock.sent[0]

    # Ethernet header
    assert frame[0:6] == chat.mac_to_bytes(chat.BROADCAST), "destination MAC should be broadcast"
    assert frame[6:12] == chat.mac_to_bytes(chat.MY_MAC), "source MAC should be our own MAC"
    assert frame[12:14] == struct.pack("!H", chat.ETH_P_CHAT), "EtherType should be 0x88B5"

    # Payload: nick_len | nickname | msg_len | message
    payload = frame[14:]
    nick = chat.NICKNAME.encode()
    assert payload[0] == len(nick), "first payload byte should be the nickname length"
    assert payload[1:1 + len(nick)] == nick, "nickname should follow its length"
    offset = 1 + len(nick)
    assert struct.unpack("!H", payload[offset:offset + 2])[0] == len(b"hi"), "message length field wrong"
    assert payload[offset + 2:offset + 4] == b"hi", "message bytes wrong"


# --- parse ------------------------------------------------------------------

def test_parse_extracts_nickname_and_message(client):
    frame = build_frame(OTHER_MAC, "bob", "hello")
    assert client.parse(frame) == ("bob", "hello")


def test_parse_ignores_our_own_echo(client):
    # a frame whose source is us -> the loopback of our own broadcast
    frame = build_frame(chat.MY_MAC, chat.NICKNAME, "echo")
    assert client.parse(frame) is None, "frames we sent ourselves must be dropped"


def test_parse_ignores_trailing_padding(client):
    frame = build_frame(OTHER_MAC, "bob", "hi")
    frame += b"\x00" * (60 - len(frame))          # pad to the 60-octet minimum
    assert client.parse(frame) == ("bob", "hi"), "msg_len should delimit the message, not the frame size"


def test_parse_returns_none_on_truncated_message(client):
    # claims a 10-byte message but only carries 2
    payload = struct.pack("!B", 3) + b"bob" + struct.pack("!H", 10) + b"hi"
    frame = (chat.mac_to_bytes(chat.BROADCAST) + chat.mac_to_bytes(OTHER_MAC)
             + struct.pack("!H", chat.ETH_P_CHAT) + payload)
    assert client.parse(frame) is None


# --- send + parse are inverses across two stations --------------------------

def test_sent_frame_is_parseable_by_a_peer(client, monkeypatch):
    client.send("hello world")
    frame = client.sock.sent[0]

    # a peer with a different MAC parses what we sent
    monkeypatch.setattr(chat, "MY_MAC", OTHER_MAC)
    assert client.parse(frame) == (chat.NICKNAME, "hello world")
