"""Tests for 5.1.0.1 Yip Yip — parsing an IPv4 header.

Packets are built field-by-field with struct in network order, so a test failing on
total_length usually means the header was unpacked little-endian (no '!').
"""
import struct

import pytest

from ipv4 import IPv4Packet

SRC_IP = "192.168.1.10"
DST_IP = "8.8.4.4"
TTL = 64
PROTOCOL_UDP = 17
PROTOCOL_TCP = 6
PAYLOAD = b"Yip yip! Muj wants shawarma. " * 9     # 261 bytes -> total length 0x0119
# Two Router Alert options (RFC 2113), 4 bytes each -> IHL 7 (28-byte header).
OPTIONS = b"\x94\x04\x00\x00" * 2


def ip_to_bytes(ip: str) -> bytes:
    return bytes(int(part) for part in ip.split("."))


def build_packet(payload: bytes, options: bytes = b"", ttl: int = TTL,
                 protocol: int = PROTOCOL_UDP, src: str = SRC_IP, dst: str = DST_IP) -> bytes:
    ihl = (20 + len(options)) // 4
    total_length = ihl * 4 + len(payload)
    header = struct.pack("!BBHHHBBH4s4s", (4 << 4) | ihl, 0, total_length, 0x1c46, 0x4000,
                         ttl, protocol, 0, ip_to_bytes(src), ip_to_bytes(dst))
    return header + options + payload


RAW_DATA = build_packet(PAYLOAD)
RAW_DATA_WITH_OPTIONS = build_packet(b"options are not payload", OPTIONS,
                                     ttl=128, protocol=PROTOCOL_TCP,
                                     src="10.0.0.1", dst="172.16.254.3")


@pytest.fixture
def packet():
    return IPv4Packet(RAW_DATA)


@pytest.fixture
def packet_with_options():
    return IPv4Packet(RAW_DATA_WITH_OPTIONS)


def test_parses_version(packet):
    """version is parsed from the high 4 bits of the first byte (4)."""
    assert packet.version == 4, f"version: expected 4, got {packet.version!r}"


def test_parses_ihl(packet):
    """ihl is parsed from the low 4 bits of the first byte (5 = a 20-byte header)."""
    assert packet.ihl == 5, f"ihl: expected 5, got {packet.ihl!r}"


def test_parses_total_length(packet):
    """total_length is parsed as a big-endian number."""
    expected = len(RAW_DATA)
    assert packet.total_length == expected, (
        f"total_length: expected {expected}, got {packet.total_length!r} "
        f"(did you use '!' for big endian?)"
    )


def test_parses_ttl(packet):
    """ttl is parsed correctly."""
    assert packet.ttl == TTL, f"ttl: expected {TTL}, got {packet.ttl!r}"


def test_parses_protocol(packet):
    """protocol is parsed correctly (17 = UDP)."""
    assert packet.protocol == PROTOCOL_UDP, (
        f"protocol: expected {PROTOCOL_UDP}, got {packet.protocol!r}"
    )


def test_parses_src_ip(packet):
    """src_ip is parsed into a readable string like '192.168.1.10'."""
    assert packet.src_ip == SRC_IP, f"src_ip: expected {SRC_IP!r}, got {packet.src_ip!r}"


def test_parses_dst_ip(packet):
    """dst_ip is parsed into a readable string like '8.8.4.4'."""
    assert packet.dst_ip == DST_IP, f"dst_ip: expected {DST_IP!r}, got {packet.dst_ip!r}"


def test_parses_payload(packet):
    """payload holds exactly the data after a 20-byte header."""
    assert packet.payload == PAYLOAD, (
        f"payload: expected {PAYLOAD[:20]!r}... ({len(PAYLOAD)} bytes), "
        f"got {packet.payload[:20]!r}... ({len(packet.payload)} bytes)"
    )


def test_header_with_options_ihl(packet_with_options):
    """A header carrying options is parsed with its real ihl (7 = a 28-byte header)."""
    assert packet_with_options.ihl == 7, f"ihl: expected 7, got {packet_with_options.ihl!r}"


def test_header_with_options_payload(packet_with_options):
    """With options, payload starts after ihl * 4 bytes and holds no header data."""
    expected = b"options are not payload"
    assert packet_with_options.payload == expected, (
        f"payload: expected {expected!r}, got {packet_with_options.payload!r} "
        f"(the header is ihl * 4 bytes long, not always 20)"
    )


def test_header_with_options_other_fields(packet_with_options):
    """The fields of a packet with options are parsed correctly as well."""
    p = packet_with_options
    got = (p.version, p.ttl, p.protocol, p.src_ip, p.dst_ip, p.total_length)
    expected = (4, 128, PROTOCOL_TCP, "10.0.0.1", "172.16.254.3", len(RAW_DATA_WITH_OPTIONS))
    assert got == expected, (
        f"(version, ttl, protocol, src_ip, dst_ip, total_length): expected {expected}, got {got}"
    )


def test_convert_bytes_to_ip(packet):
    """_convert_bytes_to_ip(b'\\xC0\\xA8\\x01\\x01') returns '192.168.1.1'."""
    result = packet._convert_bytes_to_ip(b"\xC0\xA8\x01\x01")
    assert result == "192.168.1.1", f"expected '192.168.1.1', got {result!r}"


def test_convert_bytes_to_ip_edge_values(packet):
    """_convert_bytes_to_ip handles 0, 255 and single-digit bytes."""
    cases = {
        b"\x00\x00\x00\x00": "0.0.0.0",
        b"\xFF\xFF\xFF\xFF": "255.255.255.255",
        b"\x0A\x00\x00\x7F": "10.0.0.127",
    }
    for ip_bytes, expected in cases.items():
        result = packet._convert_bytes_to_ip(ip_bytes)
        assert result == expected, f"{ip_bytes!r}: expected {expected!r}, got {result!r}"
