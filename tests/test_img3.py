from __future__ import annotations

import struct
from pathlib import Path

import pytest
from Crypto.Cipher import AES

import core.rpf as archive_facade
from core.img3 import IMG3Parser
from core.rpf_archive import replace_rpf_entry_transactional


KEY = bytes(range(32))
MAGIC = 0xA94E2A52
RSC5_MAGIC = 0x05435352
SECTOR_SIZE = 0x800
RESOURCE_FLAGS = 0xD4100010
RESOURCE_TYPE = 8
RAW_TYPE = 0x72657355


def _encrypt(payload: bytes) -> bytes:
    assert len(payload) % 16 == 0
    encrypted = payload
    for _ in range(16):
        encrypted = AES.new(KEY, AES.MODE_ECB).encrypt(encrypted)
    return encrypted


def _encrypt_aligned_prefix(payload: bytes) -> bytes:
    aligned_size = len(payload) & ~0x0F
    return _encrypt(payload[:aligned_size]) + payload[aligned_size:]


def _resource_payload(body: bytes, *, flags: int = RESOURCE_FLAGS) -> bytes:
    return struct.pack("<III", RSC5_MAGIC, RESOURCE_TYPE, flags) + body


def _build_img3(
    tmp_path: Path,
    *,
    encrypted: bool = True,
    align_toc: bool = True,
) -> tuple[Path, bytes, bytes]:
    resource_payload = _resource_payload(b"billboard")
    raw_payload = b"data"
    resource_padding = SECTOR_SIZE - len(resource_payload)
    raw_padding = SECTOR_SIZE - len(raw_payload)

    entries = b"".join(
        (
            struct.pack(
                "<IIIHH",
                RESOURCE_FLAGS,
                RESOURCE_TYPE,
                1,
                1,
                resource_padding,
            ),
            struct.pack(
                "<IIIHH",
                len(raw_payload),
                RAW_TYPE,
                2,
                1,
                raw_padding,
            ),
        )
    )
    names = b"billboard.wtd\x00data.bin\x00"
    toc = entries + names
    if align_toc:
        toc += b"\x00" * ((16 - len(toc) % 16) % 16)

    header = struct.pack("<IIIIHH", MAGIC, 3, 2, len(toc), 16, 0x00E9)
    if encrypted:
        stored_header = _encrypt_aligned_prefix(header)
        stored_toc = _encrypt_aligned_prefix(toc)
    else:
        stored_header = header
        stored_toc = toc

    archive = tmp_path / "example.img"
    with archive.open("wb") as output:
        output.write(stored_header)
        output.write(stored_toc)
        output.write(b"\x00" * (SECTOR_SIZE - output.tell()))
        output.write(resource_payload)
        output.write(b"\x00" * resource_padding)
        output.write(raw_payload)
        output.write(b"\x00" * raw_padding)

    return archive, resource_payload, raw_payload


@pytest.mark.parametrize("encrypted", (False, True))
def test_img3_parser_reads_resource_and_raw_entries(tmp_path, encrypted):
    archive, resource_payload, raw_payload = _build_img3(
        tmp_path,
        encrypted=encrypted,
    )

    parser = IMG3Parser(archive, aes_key=KEY)

    assert parser.encrypted is encrypted
    assert parser.paths == [
        {
            "path": "billboard.wtd",
            "size": len(resource_payload),
            "offset": SECTOR_SIZE,
        },
        {
            "path": "data.bin",
            "size": len(raw_payload),
            "offset": SECTOR_SIZE * 2,
        },
    ]
    assert parser.read_file("billboard.wtd") == resource_payload
    assert parser.read_file("data.bin") == raw_payload
    assert parser.entries[0]["is_resource"] is True
    assert parser.entries[0]["padding"] == SECTOR_SIZE - len(resource_payload)


@pytest.mark.parametrize("encrypted", (False, True))
def test_img3_parser_reads_unaligned_toc(tmp_path, encrypted):
    archive, resource_payload, raw_payload = _build_img3(
        tmp_path,
        encrypted=encrypted,
        align_toc=False,
    )

    parser = IMG3Parser(archive, aes_key=KEY)

    assert parser.toc_size % 16 != 0
    assert parser.read_file("billboard.wtd") == resource_payload
    assert parser.read_file("data.bin") == raw_payload


def test_img3_parser_rewrites_unaligned_encrypted_toc(tmp_path):
    archive, resource_payload, _ = _build_img3(
        tmp_path,
        encrypted=True,
        align_toc=False,
    )
    replacement_payload = b"updated raw payload"
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(replacement_payload)

    before = archive.read_bytes()
    parser = IMG3Parser(archive, aes_key=KEY)
    toc_size = parser.toc_size
    aligned_size = toc_size & ~0x0F
    trailing_size = toc_size - aligned_size
    assert trailing_size > 0

    parser.add_file(replacement, "data.bin")

    reopened = IMG3Parser(archive, aes_key=KEY)
    assert reopened.read_file("billboard.wtd") == resource_payload
    assert reopened.read_file("data.bin") == replacement_payload

    after = archive.read_bytes()
    toc_start = 20
    assert (
        after[toc_start + aligned_size:toc_start + toc_size]
        == before[toc_start + aligned_size:toc_start + toc_size]
    )


@pytest.mark.parametrize("encrypted", (False, True))
def test_img3_parser_replaces_resource_in_place(tmp_path, encrypted):
    archive, _, raw_payload = _build_img3(tmp_path, encrypted=encrypted)
    initial_size = archive.stat().st_size
    replacement_flags = 0xD4080010
    replacement_payload = _resource_payload(
        b"replacement billboard",
        flags=replacement_flags,
    )
    replacement = tmp_path / "replacement.wtd"
    replacement.write_bytes(replacement_payload)

    parser = IMG3Parser(archive, aes_key=KEY)
    parser.add_file(replacement, "billboard.wtd")

    reopened = IMG3Parser(archive, aes_key=KEY)
    entry = reopened.entries[0]
    assert archive.stat().st_size == initial_size
    assert reopened.read_file("billboard.wtd") == replacement_payload
    assert reopened.read_file("data.bin") == raw_payload
    assert entry["offset"] == SECTOR_SIZE
    assert entry["size"] == len(replacement_payload)
    assert entry["raw_size_or_flags"] == replacement_flags
    assert entry["resource_type"] == RESOURCE_TYPE
    assert entry["used_blocks"] == 1
    assert entry["padding"] == SECTOR_SIZE - len(replacement_payload)

    with archive.open("rb") as source:
        source.seek(20)
        stored_entry = source.read(16)
    expected_entry = struct.pack(
        "<IIIHH",
        replacement_flags,
        RESOURCE_TYPE,
        1,
        1,
        SECTOR_SIZE - len(replacement_payload),
    )
    assert (stored_entry != expected_entry) is encrypted


def test_img3_parser_relocates_oversized_raw_entry(tmp_path):
    archive, resource_payload, _ = _build_img3(tmp_path)
    replacement_payload = b"X" * (SECTOR_SIZE + 17)
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(replacement_payload)

    parser = IMG3Parser(archive, aes_key=KEY)
    parser.add_file(replacement, "data.bin")

    reopened = IMG3Parser(archive, aes_key=KEY)
    entry = reopened.entries[1]
    assert reopened.read_file("billboard.wtd") == resource_payload
    assert reopened.read_file("data.bin") == replacement_payload
    assert entry["offset"] == SECTOR_SIZE * 3
    assert entry["size"] == len(replacement_payload)
    assert entry["used_blocks"] == 2
    assert entry["padding"] == SECTOR_SIZE * 2 - len(replacement_payload)

    with archive.open("rb") as source:
        source.seek(SECTOR_SIZE * 2)
        assert source.read(SECTOR_SIZE) == b"\x00" * SECTOR_SIZE


def test_img3_parser_rejects_invalid_resource_replacement(tmp_path):
    archive, _, _ = _build_img3(tmp_path)
    replacement = tmp_path / "invalid.wtd"
    replacement.write_bytes(b"not an RSC5 resource")

    parser = IMG3Parser(archive, aes_key=KEY)
    with pytest.raises(Exception, match="RSC5"):
        parser.add_file(replacement, "billboard.wtd")


def test_transactional_replacement_preserves_img_container_suffix(tmp_path):
    archive, _, raw_payload = _build_img3(tmp_path)
    executable = tmp_path / "GTAIV.exe"
    executable.write_bytes(b"executable")
    replacement_payload = b"transactional replacement"
    replacement = tmp_path / "replacement.bin"
    replacement.write_bytes(replacement_payload)
    parser_paths = []

    def parser_factory(archive_path: str, _executable_path: str):
        parser_paths.append(Path(archive_path))
        assert Path(archive_path).suffix.casefold() == ".img"
        return IMG3Parser(archive_path, aes_key=KEY)

    result = replace_rpf_entry_transactional(
        archive,
        executable,
        "data.bin",
        replacement,
        parser_factory=parser_factory,
    )

    assert parser_paths
    assert any(path.name.endswith(".staged.img") for path in parser_paths)
    assert result.archive_path == archive.resolve()
    assert result.backup_path.suffix == ".img"
    assert IMG3Parser(archive, aes_key=KEY).read_file("data.bin") == replacement_payload
    restored = IMG3Parser(result.backup_path, aes_key=KEY)
    assert restored.read_file("data.bin") == raw_payload


def test_archive_facade_routes_img_and_rpf_by_extension(monkeypatch):
    calls = []

    class FakeIMG3:
        def __init__(self, *args, **kwargs):
            calls.append(("img", args, kwargs))

    class FakeRPF:
        def __init__(self, *args, **kwargs):
            calls.append(("rpf", args, kwargs))

    monkeypatch.setattr(archive_facade, "_IMG3Parser", FakeIMG3)
    monkeypatch.setattr(archive_facade, "_VendorRPFParser", FakeRPF)

    archive_facade.RPFParser("city.IMG", gtaiv_exe_path="GTAIV.exe")
    archive_facade.RPFParser("playerped.rpf", aes_key=KEY)

    assert calls == [
        (
            "img",
            ("city.IMG",),
            {"gtaiv_exe_path": "GTAIV.exe", "aes_key": None},
        ),
        (
            "rpf",
            ("playerped.rpf",),
            {"gtaiv_exe_path": None, "aes_key": KEY},
        ),
    ]
