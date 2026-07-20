import hashlib

import pytest

from vendor.pyrpfiv.crypto import normalize_aes_key, scan_for_known_key
from vendor.pyrpfiv.exceptions import AESKeyExtractionError


AES_KEY = bytes(range(32))


def test_normalize_aes_key_accepts_bytes():
    assert normalize_aes_key(AES_KEY) == AES_KEY


def test_normalize_aes_key_accepts_prefixed_hex():
    assert normalize_aes_key(f"0x{AES_KEY.hex()}") == AES_KEY


def test_normalize_aes_key_rejects_invalid_length():
    with pytest.raises(AESKeyExtractionError, match="must be 32 bytes"):
        normalize_aes_key(b"too short")


def test_normalize_aes_key_rejects_non_hex_string():
    with pytest.raises(AESKeyExtractionError, match="must be a hex string"):
        normalize_aes_key("not-a-key")


def test_scan_for_known_key_finds_aligned_unknown_offset(tmp_path):
    executable = tmp_path / "GTAIV.exe"
    executable.write_bytes((b"\xCC" * 12) + AES_KEY + (b"\x90" * 16))
    expected_sha1 = hashlib.sha1(AES_KEY).hexdigest()

    key, offset = scan_for_known_key(executable, [expected_sha1], step=4)

    assert key == AES_KEY
    assert offset == 12


def test_scan_for_known_key_returns_none_when_key_is_absent(tmp_path):
    executable = tmp_path / "GTAIV.exe"
    executable.write_bytes(b"\x00" * 128)

    key, offset = scan_for_known_key(executable, [hashlib.sha1(AES_KEY).hexdigest()], step=4)

    assert key is None
    assert offset is None
