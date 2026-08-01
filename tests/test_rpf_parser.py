import hashlib
from pathlib import Path
import struct
import zlib

import pytest
from Crypto.Cipher import AES

from vendor.pyrpfiv.constants import TOC_START_OFFSET
from vendor.pyrpfiv.exceptions import (
    FileNotFoundInRPFError,
    InvalidTOCEntryError,
    RPFParsingError,
)
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


def _metadata_entry(parser: RPFParser, file_path: str) -> dict:
    return parser._get_file_entry(file_path)


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

    replaced = _entry(reopened, FIRST_PATH)
    assert replaced["path"] == FIRST_PATH
    assert replaced["size"] == replacement.stat().st_size
    assert replaced["offset"] == FIRST_OFFSET
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


RPF2_TOC_SIZE = 0x800
RPF2_RESOURCE_OFFSET = 0x2000
RPF2_PLAIN_OFFSET = 0x2800
RPF2_RESOURCE_TYPE = 0x0D
RPF2_RESOURCE_FLAGS = 0xC0001234
RPF2_RESOURCE_PATH = "textures/uppr_diff_000_a_uni.wtd"
RPF2_PLAIN_PATH = "readme.txt"


def _raw_deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(level=9, wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


def _rsc5(resource_type: int, flags: int, payload: bytes) -> bytes:
    return struct.pack("<III", 0x05435352, resource_type, flags) + payload


def _rpf2_string_table(names: tuple[str, ...]) -> tuple[bytes, dict[str, int]]:
    table = bytearray(b"\x00")
    offsets = {"": 0}
    for name in names:
        offsets[name] = len(table)
        table.extend(name.encode("ascii") + b"\x00")
    return bytes(table), offsets


def _build_rpf2(
    path: Path,
    *,
    encrypted: bool = True,
    resource_data: bytes | None = None,
    plain_data: bytes = b"compressed RPF2 file" * 20,
) -> None:
    if resource_data is None:
        resource_data = _rsc5(
            RPF2_RESOURCE_TYPE,
            RPF2_RESOURCE_FLAGS,
            b"W" * 0x180,
        )
    stored_plain = _raw_deflate(plain_data)
    string_table, offsets = _rpf2_string_table(
        (
            "textures",
            "readme.txt",
            "uppr_diff_000_a_uni.wtd",
        )
    )
    entries = [
        # RPF2 directory count is stored in the fourth word, not the second.
        struct.pack("<IIII", offsets[""], 0, 0x80000001, 2),
        struct.pack("<IIII", offsets["textures"], 0, 0x80000003, 1),
        struct.pack(
            "<IIII",
            offsets["readme.txt"],
            len(plain_data),
            RPF2_PLAIN_OFFSET,
            len(stored_plain) | 0x40000000,
        ),
        struct.pack(
            "<IIII",
            offsets["uppr_diff_000_a_uni.wtd"],
            len(resource_data),
            RPF2_RESOURCE_OFFSET | RPF2_RESOURCE_TYPE,
            RPF2_RESOURCE_FLAGS,
        ),
    ]
    toc = b"".join(entries) + string_table
    toc = toc.ljust(RPF2_TOC_SIZE, b"\x00")
    stored_toc = _encrypt_toc(toc, AES_KEY) if encrypted else toc
    encrypted_flag = 0xFFFFFFFF if encrypted else 0
    header = struct.pack(
        "<4sIiiI",
        b"RPF2",
        RPF2_TOC_SIZE,
        len(entries),
        0,
        encrypted_flag,
    )

    archive_size = max(
        RPF2_RESOURCE_OFFSET + len(resource_data),
        RPF2_PLAIN_OFFSET + len(stored_plain),
    )
    archive = bytearray(archive_size)
    archive[: len(header)] = header
    archive[TOC_START_OFFSET:TOC_START_OFFSET + RPF2_TOC_SIZE] = stored_toc
    archive[
        RPF2_RESOURCE_OFFSET:RPF2_RESOURCE_OFFSET + len(resource_data)
    ] = resource_data
    archive[RPF2_PLAIN_OFFSET:RPF2_PLAIN_OFFSET + len(stored_plain)] = stored_plain
    path.write_bytes(archive)


def test_rpf2_uses_string_table_names_and_directory_count_word(tmp_path):
    archive = tmp_path / "playerped.rpf"
    _build_rpf2(archive)

    parser = _parser(archive)

    assert parser.identifier == "RPF2"
    assert [entry["path"] for entry in parser.paths] == [
        RPF2_RESOURCE_PATH,
        RPF2_PLAIN_PATH,
    ]
    assert parser.entries[0]["content_count"] == 2
    assert parser.entries[1]["content_count"] == 1
    assert parser.entries[1]["name"] == "textures"


def test_rpf2_resource_offset_type_and_flags_are_unpacked(tmp_path):
    archive = tmp_path / "playerped.rpf"
    _build_rpf2(archive)

    entry = _metadata_entry(_parser(archive), RPF2_RESOURCE_PATH)

    assert entry["offset"] == RPF2_RESOURCE_OFFSET
    assert entry["raw_offset"] == RPF2_RESOURCE_OFFSET | RPF2_RESOURCE_TYPE
    assert entry["resource"] is True
    assert entry["resource_type"] == RPF2_RESOURCE_TYPE
    assert entry["resource_flags"] == RPF2_RESOURCE_FLAGS
    assert entry["compressed"] is False


def test_rpf2_reads_complete_rsc5_resource_from_aligned_offset(tmp_path):
    archive = tmp_path / "playerped.rpf"
    expected = _rsc5(RPF2_RESOURCE_TYPE, RPF2_RESOURCE_FLAGS, b"texture" * 80)
    _build_rpf2(archive, resource_data=expected)

    assert _parser(archive).read_file(RPF2_RESOURCE_PATH) == expected


def test_rpf2_decompresses_non_resource_entries(tmp_path):
    archive = tmp_path / "playerped.rpf"
    expected = bytes(range(256)) * 5
    _build_rpf2(archive, plain_data=expected)

    parser = _parser(archive)
    entry = _metadata_entry(parser, RPF2_PLAIN_PATH)

    assert entry["compressed"] is True
    assert entry["stored_size"] < entry["size"]
    assert parser.read_file(RPF2_PLAIN_PATH) == expected


def test_rpf2_resource_replacement_preserves_name_and_updates_flags(tmp_path):
    archive = tmp_path / "playerped.rpf"
    original_plain = b"neighbor" * 100
    _build_rpf2(archive, plain_data=original_plain)
    replacement_flags = 0xC0005678
    replacement_data = _rsc5(
        RPF2_RESOURCE_TYPE,
        replacement_flags,
        b"replacement" * 30,
    )
    replacement = tmp_path / "replacement.wtd"
    replacement.write_bytes(replacement_data)

    _parser(archive).add_file(str(replacement), RPF2_RESOURCE_PATH)
    reopened = _parser(archive)
    entry = _metadata_entry(reopened, RPF2_RESOURCE_PATH)

    assert entry["path"] == RPF2_RESOURCE_PATH
    assert entry["offset"] == RPF2_RESOURCE_OFFSET
    assert entry["raw_offset"] == RPF2_RESOURCE_OFFSET | RPF2_RESOURCE_TYPE
    assert entry["resource_flags"] == replacement_flags
    assert reopened.read_file(RPF2_RESOURCE_PATH) == replacement_data
    assert reopened.read_file(RPF2_PLAIN_PATH) == original_plain


def test_rpf2_oversized_resource_relocates_and_keeps_type_in_low_byte(tmp_path):
    archive = tmp_path / "playerped.rpf"
    _build_rpf2(archive)
    original_eof = archive.stat().st_size
    expected_offset = RPFParser._align_up(original_eof)
    replacement_data = _rsc5(
        RPF2_RESOURCE_TYPE,
        RPF2_RESOURCE_FLAGS,
        b"R" * ((RPF2_PLAIN_OFFSET - RPF2_RESOURCE_OFFSET) + 1),
    )
    replacement = tmp_path / "replacement.wtd"
    replacement.write_bytes(replacement_data)

    _parser(archive).add_file(str(replacement), RPF2_RESOURCE_PATH)
    reopened = _parser(archive)
    entry = _metadata_entry(reopened, RPF2_RESOURCE_PATH)

    assert entry["offset"] == expected_offset
    assert entry["raw_offset"] == expected_offset | RPF2_RESOURCE_TYPE
    assert entry["raw_offset"] & 0xFF == RPF2_RESOURCE_TYPE
    assert reopened.read_file(RPF2_RESOURCE_PATH) == replacement_data


def test_rpf2_resource_replacement_rejects_different_resource_type(tmp_path):
    archive = tmp_path / "playerped.rpf"
    _build_rpf2(archive)
    replacement = tmp_path / "replacement.wtd"
    replacement.write_bytes(
        _rsc5(RPF2_RESOURCE_TYPE + 1, RPF2_RESOURCE_FLAGS, b"wrong type")
    )

    with pytest.raises(RPFParsingError, match="does not match RPF2 entry type"):
        _parser(archive).add_file(str(replacement), RPF2_RESOURCE_PATH)


def test_rpf2_compressed_replacement_round_trips(tmp_path):
    archive = tmp_path / "playerped.rpf"
    _build_rpf2(archive)
    replacement_data = b"new compressed payload" * 80
    replacement = tmp_path / "replacement.txt"
    replacement.write_bytes(replacement_data)

    _parser(archive).add_file(str(replacement), RPF2_PLAIN_PATH)
    reopened = _parser(archive)
    entry = _metadata_entry(reopened, RPF2_PLAIN_PATH)

    assert entry["compressed"] is True
    assert entry["stored_size"] < len(replacement_data)
    assert reopened.read_file(RPF2_PLAIN_PATH) == replacement_data


def test_rpf2_rejects_invalid_name_offset(tmp_path):
    archive = tmp_path / "invalid.rpf"
    _build_rpf2(archive, encrypted=False)
    data = bytearray(archive.read_bytes())
    # Corrupt the name offset of the first file entry.
    struct.pack_into("<I", data, TOC_START_OFFSET + (2 * 16), RPF2_TOC_SIZE)
    archive.write_bytes(data)

    with pytest.raises(InvalidTOCEntryError, match="invalid name offset"):
        _parser(archive)
