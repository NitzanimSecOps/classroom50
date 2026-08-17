"""Tests for invert_image."""

import struct
from pathlib import Path

import pytest

from solutions.suite_2_2_1.exercise_4.solution import invert_image
FILE_HEADER_SIZE = 14
INFO_HEADER_SIZE = 40
PIXEL_OFFSET = FILE_HEADER_SIZE + INFO_HEADER_SIZE


def build_bmp(rows: list[list[tuple[int, int, int]]], bit_count: int = 24) -> bytes:
    """Build a minimal uncompressed BMP. `rows` is bottom-up, pixels are (B, G, R)."""
    height = len(rows)
    width = len(rows[0])
    bytes_per_pixel = bit_count // 8

    row_bytes = width * bytes_per_pixel
    padding = (-row_bytes) % 4  # rows are padded to a 4-byte boundary

    pixel_data = b""
    for row in rows:
        for pixel in row:
            pixel_data += bytes(pixel[:bytes_per_pixel])
        pixel_data += b"\x00" * padding

    info_header = struct.pack(
        "<IiiHHIIiiII",
        INFO_HEADER_SIZE,  # biSize
        width,             # biWidth
        height,            # biHeight
        1,                 # biPlanes
        bit_count,         # biBitCount
        0,                 # biCompression (BI_RGB)
        len(pixel_data),   # biSizeImage
        2835,              # biXPelsPerMeter
        2835,              # biYPelsPerMeter
        0,                 # biClrUsed
        0,                 # biClrImportant
    )
    file_size = PIXEL_OFFSET + len(pixel_data)
    file_header = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, PIXEL_OFFSET)

    return file_header + info_header + pixel_data


@pytest.fixture
def sample_bmp(tmp_path: Path) -> Path:
    # 3x2 image, deliberately not a multiple of 4 bytes per row -> exercises padding
    rows = [
        [(0, 0, 0), (255, 255, 255), (10, 20, 30)],
        [(255, 0, 0), (0, 255, 0), (0, 0, 255)],
    ]
    path = tmp_path / "sample.bmp"
    path.write_bytes(build_bmp(rows))
    return path


def read_parts(path: Path) -> tuple[bytes, bytes]:
    data = path.read_bytes()
    offset = int.from_bytes(data[10:14], "little")
    return data[:offset], data[offset:]


def test_header_is_preserved(sample_bmp: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.bmp"
    invert_image(sample_bmp, out)

    original_header, _ = read_parts(sample_bmp)
    new_header, _ = read_parts(out)

    assert new_header == original_header


def test_every_pixel_byte_is_bit_flipped(sample_bmp: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.bmp"
    invert_image(sample_bmp, out)

    _, original_pixels = read_parts(sample_bmp)
    _, new_pixels = read_parts(out)

    assert len(new_pixels) == len(original_pixels)
    assert new_pixels == bytes(b ^ 0xFF for b in original_pixels)


def test_black_becomes_white(sample_bmp: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.bmp"
    invert_image(sample_bmp, out)

    _, new_pixels = read_parts(out)

    # first pixel of the first stored row was (0, 0, 0)
    assert new_pixels[:3] == b"\xff\xff\xff"


def test_inverting_twice_restores_the_original(sample_bmp: Path, tmp_path: Path) -> None:
    once = tmp_path / "once.bmp"
    twice = tmp_path / "twice.bmp"

    invert_image(sample_bmp, once)
    invert_image(once, twice)

    assert twice.read_bytes() == sample_bmp.read_bytes()


def test_file_size_is_unchanged(sample_bmp: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.bmp"
    invert_image(sample_bmp, out)

    assert out.stat().st_size == sample_bmp.stat().st_size


def test_32_bit_image_is_supported(tmp_path: Path) -> None:
    rows = [[(1, 2, 3, 4), (5, 6, 7, 8)]]
    src = tmp_path / "src32.bmp"
    src.write_bytes(build_bmp(rows, bit_count=32))

    out = tmp_path / "out32.bmp"
    invert_image(src, out)

    _, new_pixels = read_parts(out)
    assert new_pixels == bytes([254, 253, 252, 251, 250, 249, 248, 247])


def test_non_bmp_file_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "not_a.bmp"
    bad.write_bytes(b"PK\x03\x04" + b"\x00" * 100)

    with pytest.raises(ValueError, match="signature"):
        invert_image(bad, tmp_path / "out.bmp")
