import struct

import pytest

from ethernet import Ethernet

# Frame layout per the Ethernet class: first 6 bytes -> src_mac,
# next 6 bytes -> dst_mac, 2 bytes ethertype, then the payload.
SRC_MAC = b'\x11\x22\x33\x44\x55\x66'
DST_MAC = b'\xAA\xBB\xCC\xDD\xEE\xFF'
ETHERTYPE = 0x0800
PAYLOAD = b'hello world'
RAW_DATA = DST_MAC + SRC_MAC + struct.pack('!H', ETHERTYPE) + PAYLOAD


@pytest.fixture
def frame():
    return Ethernet(RAW_DATA)


def test_stores_raw_data(frame):
    assert frame.raw_data == RAW_DATA, (
        f"raw_data was not stored intact: expected {RAW_DATA!r}, got {frame.raw_data!r}"
    )


def test_parses_src_mac(frame):
    assert frame.src_mac == SRC_MAC, (
        f"src_mac parsed incorrectly: expected {SRC_MAC!r}, got {frame.src_mac!r}"
    )


def test_parses_dst_mac(frame):
    assert frame.dst_mac == DST_MAC, (
        f"dst_mac parsed incorrectly: expected {DST_MAC!r}, got {frame.dst_mac!r}"
    )


def test_parses_ethertype(frame):
    assert frame.ethertype == ETHERTYPE, (
        f"ethertype parsed incorrectly: expected {ETHERTYPE:#06x}, got {frame.ethertype:#06x}"
    )


def test_parses_payload(frame):
    assert frame.payload == PAYLOAD, (
        f"payload parsed incorrectly: expected {PAYLOAD!r}, got {frame.payload!r}"
    )
