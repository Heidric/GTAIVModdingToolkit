import base64
import json
import os
from pathlib import Path

import pytest

from core.installation_lock import installation_lock_path
from core.rpf_archive import replace_rpf_entry_transactional


class MutableArchiveParser:
    def __init__(
        self,
        archive_path: str,
        executable_path: str,
        *,
        corrupt_reads: bool = False,
        corrupt_neighbor_metadata: bool = False,
    ):
        self.archive_path = Path(archive_path)
        self.executable_path = executable_path
        self.corrupt_reads = corrupt_reads
        self.corrupt_neighbor_metadata = corrupt_neighbor_metadata
        self._load()

    def _load(self):
        self.data = json.loads(self.archive_path.read_text(encoding="utf-8"))
        self.paths = [
            {
                "path": entry["path"],
                "size": int(entry["size"]),
                "offset": int(entry["offset"]),
            }
            for entry in self.data["entries"]
        ]

    def _entry(self, file_path: str) -> dict:
        return next(
            entry for entry in self.data["entries"] if entry["path"] == file_path
        )

    def read_file(self, file_path: str) -> bytes:
        payload = base64.b64decode(self._entry(file_path)["payload"])
        if self.corrupt_reads and payload:
            return bytes([payload[0] ^ 0xFF]) + payload[1:]
        return payload

    def add_file(self, source_file: str, rpf_path: str) -> None:
        payload = Path(source_file).read_bytes()
        target = self._entry(rpf_path)
        previous_size = int(target["size"])
        target["payload"] = base64.b64encode(payload).decode("ascii")
        target["size"] = len(payload)
        if len(payload) > previous_size:
            target["offset"] = max(
                int(entry["offset"]) for entry in self.data["entries"]
            ) + 0x800
        if self.corrupt_neighbor_metadata:
            neighbor = next(
                entry for entry in self.data["entries"] if entry["path"] != rpf_path
            )
            neighbor["offset"] = int(neighbor["offset"]) + 1
        self.archive_path.write_text(
            json.dumps(self.data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._load()


class MutableParserFactory:
    def __init__(
        self,
        *,
        corrupt_final_read: bool = False,
        corrupt_neighbor_metadata: bool = False,
    ):
        self.calls = 0
        self.corrupt_final_read = corrupt_final_read
        self.corrupt_neighbor_metadata = corrupt_neighbor_metadata

    def __call__(self, archive_path: str, executable_path: str):
        self.calls += 1
        return MutableArchiveParser(
            archive_path,
            executable_path,
            corrupt_reads=self.corrupt_final_read and self.calls == 4,
            corrupt_neighbor_metadata=(
                self.corrupt_neighbor_metadata and self.calls == 2
            ),
        )


def _write_mutable_archive(path: Path) -> None:
    entries = [
        {
            "path": "textures/HUD.WTD",
            "size": 3,
            "offset": 0x1000,
            "payload": base64.b64encode(b"old").decode("ascii"),
        },
        {
            "path": "textures/MAP.WTD",
            "size": 8,
            "offset": 0x2000,
            "payload": base64.b64encode(b"neighbor").decode("ascii"),
        },
    ]
    path.write_text(
        json.dumps({"entries": entries}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _mutable_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    archive = tmp_path / "example.rpf"
    executable = tmp_path / "GTAIV.exe"
    replacement = tmp_path / "replacement.wtd"
    _write_mutable_archive(archive)
    executable.write_bytes(b"executable")
    replacement.write_bytes(b"replacement-payload")
    return archive, executable, replacement


def _transaction_temporary_files(tmp_path: Path) -> list[Path]:
    return [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(".gtaiv_toolkit_rpf_")
    ]


def test_transactional_replacement_commits_verified_archive_and_retains_backup(
    tmp_path,
):
    archive, executable, replacement = _mutable_files(tmp_path)
    original_archive = archive.read_bytes()

    result = replace_rpf_entry_transactional(
        archive,
        executable,
        "textures\\HUD.WTD",
        replacement,
        parser_factory=MutableParserFactory(),
    )

    committed = MutableArchiveParser(str(archive), str(executable))
    backup = MutableArchiveParser(str(result.backup_path), str(executable))
    assert committed.read_file("textures/HUD.WTD") == replacement.read_bytes()
    assert committed.read_file("textures/MAP.WTD") == b"neighbor"
    assert backup.read_file("textures/HUD.WTD") == b"old"
    assert result.archive_path == archive.resolve()
    assert result.entry_path == "textures/HUD.WTD"
    assert result.previous_size == 3
    assert result.replacement_size == replacement.stat().st_size
    assert result.relocated is True
    assert result.backup_path.suffix.casefold() == ".rpf"
    assert result.backup_path.read_bytes() == original_archive
    assert installation_lock_path(tmp_path, scope="rpf").is_file()
    assert _transaction_temporary_files(tmp_path) == []


def test_transactional_replacement_rejects_unrelated_metadata_changes(tmp_path):
    archive, executable, replacement = _mutable_files(tmp_path)
    original_archive = archive.read_bytes()

    with pytest.raises(RuntimeError, match="unrelated entry metadata changed"):
        replace_rpf_entry_transactional(
            archive,
            executable,
            "textures/HUD.WTD",
            replacement,
            parser_factory=MutableParserFactory(
                corrupt_neighbor_metadata=True,
            ),
        )

    assert archive.read_bytes() == original_archive
    assert not list(tmp_path.glob("example.backup-*.rpf"))
    assert _transaction_temporary_files(tmp_path) == []


def test_transactional_replacement_rolls_back_commit_failure(tmp_path):
    archive, executable, replacement = _mutable_files(tmp_path)
    original_archive = archive.read_bytes()

    def fail_commit(source: str, destination: str) -> None:
        raise OSError("commit failed")

    with pytest.raises(OSError, match="commit failed"):
        replace_rpf_entry_transactional(
            archive,
            executable,
            "textures/HUD.WTD",
            replacement,
            parser_factory=MutableParserFactory(),
            replace_file=fail_commit,
        )

    assert archive.read_bytes() == original_archive
    assert not list(tmp_path.glob("example.backup-*.rpf"))
    assert _transaction_temporary_files(tmp_path) == []


def test_transactional_replacement_restores_original_after_failed_final_check(
    tmp_path,
):
    archive, executable, replacement = _mutable_files(tmp_path)
    original_archive = archive.read_bytes()

    with pytest.raises(RuntimeError, match="replacement bytes do not match"):
        replace_rpf_entry_transactional(
            archive,
            executable,
            "textures/HUD.WTD",
            replacement,
            parser_factory=MutableParserFactory(corrupt_final_read=True),
        )

    assert archive.read_bytes() == original_archive
    assert not list(tmp_path.glob("example.backup-*.rpf"))
    assert _transaction_temporary_files(tmp_path) == []


def test_transactional_replacement_restores_if_commit_replaces_then_raises(tmp_path):
    archive, executable, replacement = _mutable_files(tmp_path)
    original_archive = archive.read_bytes()

    def replace_then_fail(source: str, destination: str) -> None:
        os.replace(source, destination)
        raise OSError("post-replace failure")

    with pytest.raises(OSError, match="post-replace failure"):
        replace_rpf_entry_transactional(
            archive,
            executable,
            "textures/HUD.WTD",
            replacement,
            parser_factory=MutableParserFactory(),
            replace_file=replace_then_fail,
        )

    assert archive.read_bytes() == original_archive
    assert not list(tmp_path.glob("example.backup-*.rpf"))
    assert _transaction_temporary_files(tmp_path) == []


def test_transactional_replacement_rejects_archive_as_payload(tmp_path):
    archive, executable, _ = _mutable_files(tmp_path)

    with pytest.raises(ValueError, match="must not be the archive itself"):
        replace_rpf_entry_transactional(
            archive,
            executable,
            "textures/HUD.WTD",
            archive,
            parser_factory=MutableParserFactory(),
        )
