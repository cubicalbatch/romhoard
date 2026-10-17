"""CHD header SHA1 extraction (matches `chdman info`, verified on 671 real CHDs)."""

import struct

from library.chd import extract_chd_sha1

SHA1 = bytes(range(1, 21))
RAW_SHA1 = bytes(range(101, 121))


def _chd(tmp_path, version: int, fields: dict[int, bytes], length: int) -> str:
    header = bytearray(length)
    header[:16] = b"MComprHD" + struct.pack(">II", length, version)
    for offset, value in fields.items():
        header[offset : offset + len(value)] = value
    path = tmp_path / f"v{version}.chd"
    path.write_bytes(bytes(header) + b"\x00" * 64)
    return str(path)


def test_v5_returns_combined_sha1_not_raw_sha1(tmp_path):
    path = _chd(tmp_path, 5, {64: RAW_SHA1, 84: SHA1}, 124)

    assert extract_chd_sha1(path) == SHA1.hex()


def test_v4_sha1(tmp_path):
    path = _chd(tmp_path, 4, {48: SHA1, 88: RAW_SHA1}, 108)

    assert extract_chd_sha1(path) == SHA1.hex()


def test_v3_sha1(tmp_path):
    path = _chd(tmp_path, 3, {80: SHA1}, 120)

    assert extract_chd_sha1(path) == SHA1.hex()


def test_md5_only_versions_have_no_sha1(tmp_path):
    assert extract_chd_sha1(_chd(tmp_path, 2, {}, 80)) is None


def test_zero_sha1_is_none(tmp_path):
    assert extract_chd_sha1(_chd(tmp_path, 5, {}, 124)) is None


def test_not_a_chd(tmp_path):
    path = tmp_path / "fake.chd"
    path.write_bytes(b"\x00" * 200)

    assert extract_chd_sha1(str(path)) is None


def test_truncated_header(tmp_path):
    path = tmp_path / "short.chd"
    path.write_bytes(b"MComprHD" + struct.pack(">II", 124, 5) + b"\x01" * 20)

    assert extract_chd_sha1(str(path)) is None


def test_unreadable_file(tmp_path):
    assert extract_chd_sha1(str(tmp_path / "missing.chd")) is None
