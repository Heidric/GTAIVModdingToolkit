import hashlib
import os
from typing import Iterable, Tuple

from Crypto.Cipher import AES

from .constants import KEY_OFFSETS, KEY_SHA1S
from .exceptions import AESKeyExtractionError, TOCDecryptionError

AES_KEY_SIZE = 32


def file_sha1(path):
    sha1 = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            sha1.update(chunk)
    return sha1.hexdigest()


def normalize_aes_key(aes_key):
    """Return a 32-byte AES key from bytes or a hex string."""
    if isinstance(aes_key, bytes):
        key = aes_key
    elif isinstance(aes_key, str):
        value = aes_key.strip()
        if value.lower().startswith("0x"):
            value = value[2:]
        try:
            key = bytes.fromhex(value)
        except ValueError as exc:
            raise AESKeyExtractionError("AES key override must be a hex string.") from exc
    else:
        raise AESKeyExtractionError("AES key override must be bytes or a hex string.")

    if len(key) != AES_KEY_SIZE:
        raise AESKeyExtractionError(
            f"AES key override must be {AES_KEY_SIZE} bytes, got {len(key)} bytes."
        )
    return key


def get_exe_fingerprint(exe_path):
    if not os.path.exists(exe_path):
        raise AESKeyExtractionError(f"GTAIV.exe not found at path: {exe_path}")

    return {
        "path": os.path.abspath(exe_path),
        "size": os.path.getsize(exe_path),
        "sha1": file_sha1(exe_path),
    }


def try_extract_key(exe_path, offset, expected_sha1):
    """Try extracting and verifying a key at a specific executable offset."""
    with open(exe_path, "rb") as f:
        f.seek(offset)
        possible_key = f.read(AES_KEY_SIZE)

    if len(possible_key) != AES_KEY_SIZE:
        return None

    key_sha1 = hashlib.sha1(possible_key).hexdigest().upper()
    if key_sha1 == expected_sha1.upper():
        return possible_key
    return None


def _unique_expected_sha1s() -> Tuple[str, ...]:
    return tuple(sorted({value.upper() for value in KEY_SHA1S.values()}))


def scan_for_known_key(exe_path, expected_sha1s: Iterable[str], step=4):
    """Scan an executable for already-known GTA IV AES key material.

    This is a compatibility fallback for executable builds whose key is present
    but not located at one of the hard-coded offsets. It does not discover new
    keys cryptographically; it only finds keys whose SHA-1 is already known.
    """
    expected = {value.upper() for value in expected_sha1s}
    with open(exe_path, "rb") as f:
        data = f.read()

    if len(data) < AES_KEY_SIZE:
        return None, None

    last_start = len(data) - AES_KEY_SIZE
    for offset in range(0, last_start + 1, step):
        possible_key = data[offset:offset + AES_KEY_SIZE]
        key_sha1 = hashlib.sha1(possible_key).hexdigest().upper()
        if key_sha1 in expected:
            return possible_key, offset

    return None, None


def _format_supported_versions():
    return ", ".join(KEY_OFFSETS.keys())


def extract_aes_key(exe_path, scan_unknown_offsets=True):
    """Extract a GTA IV RPF AES key from GTAIV.exe.

    Known offsets are checked first. If they fail, the extractor scans the
    executable for already-known GTA IV AES key bytes. This supports unknown
    executable builds where the same key is stored at a different offset.
    """
    fingerprint = get_exe_fingerprint(exe_path)
    print(f"Extracting AES key from {fingerprint['path']}...")

    checked_offsets = []
    for version, offset in KEY_OFFSETS.items():
        checked_offsets.append(f"{version}@0x{offset:X}")
        key = try_extract_key(exe_path, offset, KEY_SHA1S[version])
        if key:
            print(f"AES key found and verified at offset 0x{offset:X} (Version {version}).")
            return key

    if scan_unknown_offsets:
        key, offset = scan_for_known_key(exe_path, _unique_expected_sha1s(), step=4)
        if key:
            print(f"AES key found and verified by executable scan at offset 0x{offset:X}.")
            return key

    raise AESKeyExtractionError(
        "Could not extract a supported GTA IV RPF AES key.\n"
        f"Executable: {fingerprint['path']}\n"
        f"Size: {fingerprint['size']} bytes\n"
        f"SHA1: {fingerprint['sha1']}\n"
        f"Supported known-offset versions: {_format_supported_versions()}\n"
        f"Checked offsets: {', '.join(checked_offsets)}\n"
        f"Unknown-offset scan: {'enabled' if scan_unknown_offsets else 'disabled'}\n"
        "The fallback scan can only find already-known key bytes stored in a new location; "
        "it cannot derive a genuinely new or obfuscated key."
    )


def decrypt_toc(data, aes_key):
    """Decrypt TOC data using AES ECB mode."""
    if len(data) % 16 != 0:
        raise TOCDecryptionError("Encrypted TOC data size is not a multiple of 16 bytes.")

    cipher = AES.new(aes_key, AES.MODE_ECB)
    decrypted_data = data

    for _ in range(16):
        decrypted_data = cipher.decrypt(decrypted_data)

    return decrypted_data
