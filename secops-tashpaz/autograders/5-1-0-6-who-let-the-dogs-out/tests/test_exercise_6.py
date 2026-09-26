"""Tests for 5.1.0.6 Who Let the Dogs Out — forward_packet sends an Ethernet frame.

A raw packet socket needs root and a live interface, which the grader doesn't have, so
socket.socket is replaced with a mock (as in 4.1.0.2) and the tests read the frame the code
sent. Sending with send(), sendall() or sendto() are all accepted.
"""
import importlib
import socket
import struct
from unittest.mock import MagicMock

import pytest

from routing import RouteEntry

ETH_P_ALL = 0x0003
HOST_IP = "192.168.1.10"
HOST_MAC = "D4:92:5E:B8:99:37"
ROUTER_IP, ROUTER_MAC = "192.168.1.254", "AA:BB:CC:00:11:FE"
NEXT_MAC = "00:11:22:33:44:55"
PAYLOAD = b"who? who? who?"


def ip_to_bytes(ip: str) -> bytes:
    return bytes(int(part) for part in ip.split("."))


def mac_to_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def build_packet(dst: str, payload: bytes = PAYLOAD, src: str = "172.20.0.5") -> bytes:
    header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 0x1c46, 0x4000,
                         64, 17, 0, ip_to_bytes(src), ip_to_bytes(dst))
    return header + payload


RAW = build_packet("10.9.9.9")


@pytest.fixture
def net(monkeypatch):
    # AF_PACKET and ETH_P_ALL exist only on Linux; stand-ins let the tests run elsewhere too.
    monkeypatch.setattr(socket, "AF_PACKET", getattr(socket, "AF_PACKET", 17), raising=False)
    monkeypatch.setattr(socket, "ETH_P_ALL", getattr(socket, "ETH_P_ALL", ETH_P_ALL), raising=False)
    fake = MagicMock(name="socket.socket")
    sock = fake.return_value
    sock.__enter__.return_value = sock          # `with socket.socket(...) as s:` works too
    monkeypatch.setattr(socket, "socket", fake)
    import host as host_module
    host_module = importlib.reload(host_module)  # so `from socket import socket` gets the mock too
    fake.reset_mock()

    host = host_module.Host(HOST_IP, HOST_MAC)
    host.routing_table.add_route(RouteEntry("10.0.0.0", "255.0.0.0", ROUTER_IP, "eth1"))
    host.arp_table.learn(ROUTER_IP, ROUTER_MAC)
    return host, fake


def sent_frames(fake) -> list[bytes]:
    sock = fake.return_value
    calls = sock.send.call_args_list + sock.sendall.call_args_list + sock.sendto.call_args_list
    return [bytes(call.args[0]) for call in calls if call.args]


def used_interfaces(fake) -> set:
    sock = fake.return_value
    addresses = [call.args[0] for call in sock.bind.call_args_list if call.args]
    addresses += [call.args[1] for call in sock.sendto.call_args_list if len(call.args) > 1]
    return {address[0] for address in addresses if address}


def only_frame(fake) -> bytes:
    frames = sent_frames(fake)
    assert len(frames) == 1, f"expected exactly one frame to be sent, got {len(frames)}"
    return frames[0]


def test_sends_one_frame(net):
    """forward_packet() sends exactly one frame through the socket."""
    host, fake = net
    host.forward_packet(RAW, NEXT_MAC, "eth1")
    only_frame(fake)


def test_frame_destination_is_next_hop(net):
    """The frame's destination MAC (first 6 bytes) is next_mac."""
    host, fake = net
    host.forward_packet(RAW, NEXT_MAC, "eth1")
    frame = only_frame(fake)
    assert frame[:6] == mac_to_bytes(NEXT_MAC), (
        f"destination MAC: expected {mac_to_bytes(NEXT_MAC)!r}, got {frame[:6]!r}"
    )


def test_frame_source_is_host_mac(net):
    """The frame's source MAC (bytes 6-12) is the host's own MAC."""
    host, fake = net
    host.forward_packet(RAW, NEXT_MAC, "eth1")
    frame = only_frame(fake)
    assert frame[6:12] == mac_to_bytes(HOST_MAC), (
        f"source MAC: expected {mac_to_bytes(HOST_MAC)!r}, got {frame[6:12]!r}"
    )


def test_frame_ethertype_is_ipv4(net):
    """The frame's ethertype is 0x0800 (IPv4), in big endian."""
    host, fake = net
    host.forward_packet(RAW, NEXT_MAC, "eth1")
    frame = only_frame(fake)
    assert frame[12:14] == b"\x08\x00", f"ethertype: expected b'\\x08\\x00', got {frame[12:14]!r}"


def test_frame_carries_ip_packet(net):
    """After the 14-byte Ethernet header, the frame carries the IP packet unchanged."""
    host, fake = net
    host.forward_packet(RAW, NEXT_MAC, "eth1")
    frame = only_frame(fake)
    assert frame[14:] == RAW, "the IP packet after the Ethernet header is not the one given"


def test_uses_raw_packet_socket(net):
    """The frame is sent through a raw AF_PACKET socket."""
    host, fake = net
    host.forward_packet(RAW, NEXT_MAC, "eth1")
    assert fake.call_count >= 1, "no socket was created"
    args = list(fake.call_args.args) + [fake.call_args.kwargs.get(k) for k in ("family", "type")]
    assert args[0] == socket.AF_PACKET and args[1] == socket.SOCK_RAW, (
        "expected socket.socket(socket.AF_PACKET, socket.SOCK_RAW, ...)"
    )


def test_sends_out_of_given_interface(net):
    """The frame goes out of the interface forward_packet() was given."""
    host, fake = net
    host.forward_packet(RAW, NEXT_MAC, "veth-muj")
    only_frame(fake)
    interfaces = used_interfaces(fake)
    assert interfaces == {"veth-muj"}, (
        f"expected the socket bound to 'veth-muj', got {sorted(interfaces) or 'no interface'}"
    )


def test_process_packet_puts_frame_on_the_wire(net):
    """process_packet() of a routed packet sends a frame to the gateway's MAC via the route's interface."""
    host, fake = net
    host.process_packet(RAW)
    frame = only_frame(fake)
    assert frame[:6] == mac_to_bytes(ROUTER_MAC), "the frame should be addressed to the gateway"
    assert frame[14:] == RAW
    assert used_interfaces(fake) == {"eth1"}


def test_nothing_sent_for_delivered_or_dropped(net):
    """Only forwarded packets go on the wire — delivered and dropped ones send nothing."""
    host, fake = net
    host.process_packet(build_packet(HOST_IP))   # ours -> DELIVER
    host.process_packet(RAW)                      # routed -> FORWARD
    host.process_packet(build_packet("8.8.8.8"))  # no route -> DROP
    frames = sent_frames(fake)
    assert [frame[14:] for frame in frames] == [RAW], (
        f"expected only the forwarded packet on the wire, got {len(frames)} frame(s)"
    )
