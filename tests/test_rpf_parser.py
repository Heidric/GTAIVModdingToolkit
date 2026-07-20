import hashlib
from pathlib import Path
import struct

import pytest
from Crypto.Cipher import AES

from vendor.pyrpfiv.constants import TOC_START_OFFSET
from vendor.pyrpfiv.exceptions import FileNotFoundInRPFError, RPFParsingError
from vendor.pyrpfiv.parser import RPFParser


AES_KEY = bytes(range(32))
TOC_SIZE = 0x800
FIRST_OFFSET = 0x1000
SECOND_OFFSET = 0x1800
FIRST_PATH = "TEST/FIRST"
SECOND_PATH = "TEST/SECOND"
NAME_MAP = {
    1: "ROOT",
    2: "TEST",
    3: "FIRST",
    4: "SECOND",
}


def _encrypt_toc(toc: bytes, key: bytes) -> bytes:
    encrypted = toc
    for _ in range(16):
        encrypted = AES.new(key, AES.MODE_ECB).encrypt(encrypted)
    return encrypted


def _build_rpf(
    path: Path,
    *,
    encrypted: bool = True,
    first_data: bytes = b"A" * 0x200,
    second_data: bytes = b"B" * 0x200,
) -> None:
    entries = [
        struct.pack("<IIII", 1, 1, 0x80000001, 0),
        struct.pack("<IIII", 2, 2, 0x80000002, 0),
        struct.pack("<IIII", 3, len(first_data), FIRST_OFFSET, 0),
        struct.pack("<IIII", 4, len(second_data), SECOND_OFFSET, 0),
    ]
    toc = b"".join(entries).ljust(TOC_SIZE, b"\x00")
    stored_toc = _encrypt_toc(toc, AES_KEY) if encrypted else toc
    header = struct.pack("<4sIiiI", b"RPF3", TOC_SIZE, len(entries), 0, int(encrypted))

    archive = bytearray(SECOND_OFFSET + len(second_data))
    archive[: len(header)] = header
    archive[TOC_START_OFFSET:TOC_START_OFFSET + TOC_SIZE] = stored_toc
    archive[FIRST_OFFSET:FIRST_OFFSET + len(first_data)] = first_data
    archive[SECOND_OFFSET:SECOND_OFFSET + len(second_data)] = second_data
    path.write_bytes(archive)


def _parser(path: Path) -> RPFParser:
    return RPFParser(str(path), aes_key=AES_KEY)


def _entry(parser: RPFParser, file_path: str) -> dict:
    return next(entry for entry in parser.paths if entry["path"] == file_path)


def _extract(parser: RPFParser, file_path: str, output_dir: Path) -> bytes:
    parser.extract_file(file_path, str(output_dir))
    return (output_dir / file_path.rsplit("/", 1)[-1]).read_bytes()


@pytest.fixture(autouse=True)
def use_synthetic_filename_map(monkeypatch):
    def init_known_filenames(parser):
        parser.known_filenames = dict(NAME_MAP)

    monkeypatch.setattr(RPFParser, "init_known_filenames", init_known_filenames)


def test_get_file_capacity_uses_next_file_offset(tmp_path):
    archive = tmp_path / "radio.rpf"
    _build_rpf(archive)

    assert _parser(archive).get_file_capacity(FIRST_PATH) == SECOND_OFFSET - FIRST_OFFSET


def test_get_file_capacity_uses_eof_for_last_file(tmp_path):
    archive = tmp_path / "radio.rpf"
    second_data = b"B" * 0x280
    _build_rpf(archive, second_data=second_data)

    assert _parser(archive).get_file_capacity(SECOND_PATH) == len(second_data)


def test_get_file_capacity_rejects_unknown_path(tmp_path):
    archive = tmp_path / "radio.rpf"
    _build_rpf(archive)

    with pytest.raises(FileNotFoundInRPFError):
        _parser(archive).get_file_capacity("TEST/MISSING")


def test_smaller_replacement_keeps_original_offset_and_neighbor(tmp_path):
    archive = tmp_path / "radio.rpf"
    original_second = b"B" * 0x200
    _build_rpf(archive, second_data=original_second)
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"R" * 0x100)

    parser = _parser(archive)
    parser.add_file(str(replacement), FIRST_PATH)
    reopened = _parser(archive)

    assert _entry(reopened, FIRST_PATH) == {
        "path": FIRST_PATH,
        "size": replacement.stat().st_size,
        "offset": FIRST_OFFSET,
    }
    assert _extract(reopened, FIRST_PATH, tmp_path / "first") == replacement.read_bytes()
    assert _extract(reopened, SECOND_PATH, tmp_path / "second") == original_second


def test_replacement_equal_to_capacity_does_not_relocate(tmp_path):
    archive = tmp_path / "radio.rpf"
    original_second = b"B" * 0x200
    _build_rpf(archive, second_data=original_second)
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"R" * (SECOND_OFFSET - FIRST_OFFSET))

    parser = _parser(archive)
    parser.add_file(str(replacement), FIRST_PATH)
    reopened = _parser(archive)

    assert _entry(reopened, FIRST_PATH)["offset"] == FIRST_OFFSET
    assert _extract(reopened, FIRST_PATH, tmp_path / "first") == replacement.read_bytes()
    assert _extract(reopened, SECOND_PATH, tmp_path / "second") == original_second


def test_oversized_replacement_relocates_to_aligned_eof(tmp_path):
    archive = tmp_path / "radio.rpf"
    original_second = b"B" * 0x200
    _build_rpf(archive, second_data=original_second)
    original_eof = archive.stat().st_size
    expected_offset = RPFParser._align_up(original_eof)
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"R" * ((SECOND_OFFSET - FIRST_OFFSET) + 1))

    parser = _parser(archive)
    parser.add_file(str(replacement), FIRST_PATH)
    reopened = _parser(archive)

    relocated = _entry(reopened, FIRST_PATH)
    assert relocated["offset"] == expected_offset
    assert relocated["offset"] % 0x800 == 0
    assert relocated["size"] == replacement.stat().st_size
    assert _extract(reopened, FIRST_PATH, tmp_path / "first") == replacement.read_bytes()
    assert _extract(reopened, SECOND_PATH, tmp_path / "second") == original_second


def test_relocated_replacement_survives_reopen_with_matching_sha256(tmp_path):
    archive = tmp_path / "radio.rpf"
    _build_rpf(archive)
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(bytes(range(256)) * 9)
    expected_hash = hashlib.sha256(replacement.read_bytes()).hexdigest()

    _parser(archive).add_file(str(replacement), FIRST_PATH)
    extracted = _extract(_parser(archive), FIRST_PATH, tmp_path / "verification")

    assert hashlib.sha256(extracted).hexdigest() == expected_hash


def test_unencrypted_toc_is_updated_and_reopened(tmp_path):
    archive = tmp_path / "radio.rpf"
    _build_rpf(archive, encrypted=False)
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"plain" * 100)

    _parser(archive).add_file(str(replacement), FIRST_PATH)
    reopened = _parser(archive)

    assert reopened.encrypted is False
    assert _extract(reopened, FIRST_PATH, tmp_path / "first") == replacement.read_bytes()


def test_add_file_rejects_missing_source(tmp_path):
    archive = tmp_path / "radio.rpf"
    _build_rpf(archive)

    with pytest.raises(FileNotFoundError):
        _parser(archive).add_file(str(tmp_path / "missing.bin"), FIRST_PATH)


def test_add_file_rejects_relocated_offset_outside_rpf3_range(tmp_path, monkeypatch):
    archive = tmp_path / "radio.rpf"
    _build_rpf(archive)
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(b"R" * ((SECOND_OFFSET - FIRST_OFFSET) + 1))
    parser = _parser(archive)
    monkeypatch.setattr(parser, "_align_up", lambda value, alignment=0x800: 0x80000000)

    with pytest.raises(RPFParsingError, match="exceeds the RPF3 file offset range"):
        parser.add_file(str(replacement), FIRST_PATH)
