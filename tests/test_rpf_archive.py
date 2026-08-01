from pathlib import Path

import pytest

from core.rpf_archive import (
    RPFArchiveEntry,
    export_rpf_entry,
    inspect_rpf_archive,
    normalize_entry_path,
)
from vendor.pyrpfiv.exceptions import FileExtractionError, InvalidTOCEntryError
from vendor.pyrpfiv.parser import RPFParser


class FakeParser:
    paths = [
        {"path": "textures/HUD.WTD", "size": 7, "offset": 4096},
        {"path": "audio/RADIO.RPF", "size": 5, "offset": 8192},
    ]
    payloads = {
        "textures/HUD.WTD": b"texture",
        "audio/RADIO.RPF": b"radio",
    }

    def __init__(self, archive_path: str, executable_path: str):
        self.archive_path = archive_path
        self.executable_path = executable_path

    def read_file(self, file_path: str) -> bytes:
        return self.payloads[file_path]


def _files(tmp_path: Path) -> tuple[Path, Path]:
    archive = tmp_path / "example.rpf"
    executable = tmp_path / "GTAIV.exe"
    archive.write_bytes(b"archive")
    executable.write_bytes(b"executable")
    return archive, executable


def _directory(index: int, name: str, content_index: int, content_count: int) -> dict:
    return {
        "type": "directory",
        "index": index,
        "name": name,
        "content_index": content_index,
        "content_count": content_count,
    }


def _file(index: int, name: str, size: int, offset: int) -> dict:
    return {
        "type": "file",
        "index": index,
        "name": name,
        "size": size,
        "offset": offset,
    }


def test_build_file_list_preserves_nested_directory_paths():
    parser = object.__new__(RPFParser)
    parser.entries = [
        _directory(0, "ROOT", 1, 2),
        _directory(1, "TEXTURES", 3, 2),
        _directory(2, "AUDIO", 5, 1),
        _file(3, "HUD.WTD", 10, 0x1000),
        _directory(4, "MAPS", 6, 1),
        _file(5, "RADIO.RPF", 20, 0x2000),
        _file(6, "CITY.WTD", 30, 0x3000),
    ]

    parser.build_file_list()

    assert parser.paths == [
        {"path": "TEXTURES/HUD.WTD", "size": 10, "offset": 0x1000},
        {"path": "TEXTURES/MAPS/CITY.WTD", "size": 30, "offset": 0x3000},
        {"path": "AUDIO/RADIO.RPF", "size": 20, "offset": 0x2000},
    ]


def test_build_file_list_rejects_invalid_child_range():
    parser = object.__new__(RPFParser)
    parser.entries = [_directory(0, "ROOT", 4, 1)]

    with pytest.raises(InvalidTOCEntryError, match="invalid child range"):
        parser.build_file_list()


def test_build_file_list_rejects_directory_cycles():
    parser = object.__new__(RPFParser)
    parser.entries = [
        _directory(0, "ROOT", 1, 1),
        _directory(1, "LOOP", 1, 1),
    ]

    with pytest.raises(InvalidTOCEntryError, match="cycle"):
        parser.build_file_list()


def test_read_file_returns_exact_entry_bytes(tmp_path):
    archive = tmp_path / "example.rpf"
    archive.write_bytes(b"0123456789")
    parser = object.__new__(RPFParser)
    parser.rpf_filename = str(archive)
    parser.paths = [{"path": "ROOT/FILE", "size": 4, "offset": 3}]

    assert parser.read_file("ROOT/FILE") == b"3456"


def test_read_file_rejects_entry_outside_archive(tmp_path):
    archive = tmp_path / "example.rpf"
    archive.write_bytes(b"short")
    parser = object.__new__(RPFParser)
    parser.rpf_filename = str(archive)
    parser.paths = [{"path": "ROOT/FILE", "size": 10, "offset": 1}]

    with pytest.raises(FileExtractionError, match="exceeds the RPF archive bounds"):
        parser.read_file("ROOT/FILE")


def test_inspect_rpf_archive_returns_sorted_immutable_metadata(tmp_path):
    archive, executable = _files(tmp_path)

    snapshot = inspect_rpf_archive(
        archive,
        executable,
        parser_factory=FakeParser,
    )

    assert snapshot.archive_path == archive.resolve()
    assert snapshot.entries == (
        RPFArchiveEntry(path="audio/RADIO.RPF", size=5, offset=8192),
        RPFArchiveEntry(path="textures/HUD.WTD", size=7, offset=4096),
    )
    assert snapshot.entry("textures\\HUD.WTD").name == "HUD.WTD"
    assert snapshot.entry("textures/HUD.WTD").parent == "textures"


def test_inspect_rpf_archive_accepts_img_container_extension(tmp_path):
    archive = tmp_path / "example.img"
    executable = tmp_path / "GTAIV.exe"
    archive.write_bytes(b"archive")
    executable.write_bytes(b"executable")

    snapshot = inspect_rpf_archive(
        archive,
        executable,
        parser_factory=FakeParser,
    )

    assert snapshot.archive_path == archive.resolve()
    assert snapshot.entry("textures/HUD.WTD").size == 7


def test_export_rpf_entry_uses_explicit_destination(tmp_path):
    archive, executable = _files(tmp_path)
    destination = tmp_path / "exports" / "custom-name.wtd"

    result = export_rpf_entry(
        archive,
        executable,
        "textures\\HUD.WTD",
        destination,
        parser_factory=FakeParser,
    )

    assert result == destination.resolve()
    assert destination.read_bytes() == b"texture"


def test_export_rpf_entry_refuses_to_overwrite_by_default(tmp_path):
    archive, executable = _files(tmp_path)
    destination = tmp_path / "existing.wtd"
    destination.write_bytes(b"keep")

    with pytest.raises(FileExistsError):
        export_rpf_entry(
            archive,
            executable,
            "textures/HUD.WTD",
            destination,
            parser_factory=FakeParser,
        )

    assert destination.read_bytes() == b"keep"


@pytest.mark.parametrize(
    "value",
    ("", "/ROOT/FILE", "ROOT/", "ROOT//FILE", "ROOT/../FILE"),
)
def test_normalize_entry_path_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        normalize_entry_path(value)


def test_export_rpf_entry_overwrites_atomically_when_requested(tmp_path):
    archive, executable = _files(tmp_path)
    destination = tmp_path / "existing.wtd"
    destination.write_bytes(b"old")

    export_rpf_entry(
        archive,
        executable,
        "textures/HUD.WTD",
        destination,
        overwrite=True,
        parser_factory=FakeParser,
    )

    assert destination.read_bytes() == b"texture"
    assert not list(tmp_path.glob(".existing.wtd.*.tmp"))
