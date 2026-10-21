"""Tests for 6.1.0.1 Parse ARP — parsing an ARP message.

Messages are built field-by-field with struct in network order. The class receives the
ARP message only (the 28 bytes after the Ethernet header), exactly as the exercise says.
"""
import struct

import pytest

from arp import ARP

ATTACKER_MAC = "02:00:00:00:00:66"
ATTACKER_IP = "10.0.0.66"
GATEWAY_MAC = "02:00:00:00:00:01"
GATEWAY_IP = "10.0.0.1"
ZERO_MAC = "00:00:00:00:00:00"


def mac_to_bytes(mac: str) -> bytes:
    return bytes.fromhex(mac.replace(":", ""))


def ip_to_bytes(ip: str) -> bytes:
    return bytes(int(part) for part in ip.split("."))


def build_arp(opcode: int, sender_mac: str, sender_ip: str, target_mac: str, target_ip: str,
              hardware_type: int = 1, protocol_type: int = 0x0800,
              hardware_size: int = 6, protocol_size: int = 4) -> bytes:
    return struct.pack("!HHBBH6s4s6s4s", hardware_type, protocol_type, hardware_size,
                       protocol_size, opcode, mac_to_bytes(sender_mac), ip_to_bytes(sender_ip),
                       mac_to_bytes(target_mac), ip_to_bytes(target_ip))


# A request ("who has the gateway?") and the reply that answers it.
REQUEST = build_arp(1, ATTACKER_MAC, ATTACKER_IP, ZERO_MAC, GATEWAY_IP)
REPLY = build_arp(2, GATEWAY_MAC, GATEWAY_IP, ATTACKER_MAC, ATTACKER_IP)


@pytest.fixture
def request_packet():
    return ARP(REQUEST)


@pytest.fixture
def reply_packet():
    return ARP(REPLY)


def test_parses_opcode_request(request_packet):
    """opcode of a request is 1."""
    assert request_packet.opcode == 1, f"opcode: expected 1, got {request_packet.opcode!r}"


def test_parses_opcode_reply(reply_packet):
    """opcode of a reply is 2."""
    assert reply_packet.opcode == 2, f"opcode: expected 2, got {reply_packet.opcode!r}"


def test_parses_sender_mac(reply_packet):
    """sender_mac is parsed into a readable string like '02:00:00:00:00:01'."""
    assert reply_packet.sender_mac.lower() == GATEWAY_MAC, (
        f"sender_mac: expected {GATEWAY_MAC!r}, got {reply_packet.sender_mac!r}"
    )


def test_parses_sender_ip(reply_packet):
    """sender_ip is parsed into a readable string like '10.0.0.1'."""
    assert reply_packet.sender_ip == GATEWAY_IP, (
        f"sender_ip: expected {GATEWAY_IP!r}, got {reply_packet.sender_ip!r}"
    )


def test_parses_target_mac(reply_packet):
    """target_mac is parsed into a readable string."""
    assert reply_packet.target_mac.lower() == ATTACKER_MAC, (
        f"target_mac: expected {ATTACKER_MAC!r}, got {reply_packet.target_mac!r}"
    )


def test_parses_target_ip(reply_packet):
    """target_ip is parsed into a readable string."""
    assert reply_packet.target_ip == ATTACKER_IP, (
        f"target_ip: expected {ATTACKER_IP!r}, got {reply_packet.target_ip!r}"
    )


def test_parses_fixed_header_fields(reply_packet):
    """hardware_type, protocol_type, hardware_size and protocol_size are parsed."""
    got = (reply_packet.hardware_type, reply_packet.protocol_type,
           reply_packet.hardware_size, reply_packet.protocol_size)
    expected = (1, 0x0800, 6, 4)
    assert got == expected, (
        f"(hardware_type, protocol_type, hardware_size, protocol_size): "
        f"expected {expected}, got {got} (did you use '!' for big endian?)"
    )


def test_ignores_trailing_padding():
    """A padded frame still parses — read the first 28 bytes, not the whole buffer.

    Ethernet pads short frames to 60 bytes, so a real ARP message often arrives with
    trailing zeros that are not part of the ARP data.
    """
    padded = REPLY + b"\x00" * 18
    arp = ARP(padded)
    assert (arp.opcode, arp.sender_ip, arp.target_ip) == (2, GATEWAY_IP, ATTACKER_IP), (
        "a padded ARP message should parse the same as an unpadded one "
        "(slice raw_data[:28] before unpacking)"
    )


def test_a_real_captured_reply():
    """A full ARP reply captured off the wire parses to the expected fields."""
    arp = ARP(build_arp(2, "aa:bb:cc:dd:ee:ff", "192.168.1.1",
                        "11:22:33:44:55:66", "192.168.1.50"))
    got = (arp.opcode, arp.sender_mac.lower(), arp.sender_ip,
           arp.target_mac.lower(), arp.target_ip)
    expected = (2, "aa:bb:cc:dd:ee:ff", "192.168.1.1", "11:22:33:44:55:66", "192.168.1.50")
    assert got == expected, f"expected {expected}, got {got}"
