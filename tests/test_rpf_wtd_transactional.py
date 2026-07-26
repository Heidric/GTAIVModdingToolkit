import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from core.rpf_wtd import replace_rpf_wtd_texture_from_image_transactional
from core.wtd_archive import WTDTextureEntry, WTDTextureReplacementResult


class MutableArchiveParser:
    def __init__(self, archive_path: str, executable_path: str):
        self.archive_path = Path(archive_path)
        self.executable_path = executable_path
        self._load()

    def _load(self) -> None:
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
        return base64.b64decode(self._entry(file_path)["payload"])

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
        self.archive_path.write_text(
            json.dumps(self.data, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self._load()


class MutableParserFactory:
    def __call__(self, archive_path: str, executable_path: str):
        return MutableArchiveParser(archive_path, executable_path)


def _write_archive(path: Path) -> None:
    source_wtd = b"source-wtd"
    entries = [
        {
            "path": "textures/HUD.WTD",
            "size": len(source_wtd),
            "offset": 0x1000,
            "payload": base64.b64encode(source_wtd).decode("ascii"),
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


def _files(tmp_path: Path) -> tuple[Path, Path, Path]:
    archive = tmp_path / "example.rpf"
    executable = tmp_path / "GTAIV.exe"
    image = tmp_path / "replacement.png"
    _write_archive(archive)
    executable.write_bytes(b"executable")
    image.write_bytes(b"image")
    return archive, executable, image


def _texture_entry(index: int = 3) -> WTDTextureEntry:
    return WTDTextureEntry(
        index=index,
        hash=0x12345678,
        name="hud_icon",
        raw_name="pack:/hud_icon.dds",
        width=128,
        height=128,
        format_code=0x35545844,
        format_name="DXT5",
        stride=512,
        texture_type=1,
        mip_count=8,
        data_size=21856,
        extractable=True,
        replaceable=True,
    )


class FakeWTDReplacer:
    def __init__(
        self,
        *,
        payload: bytes = b"patched-wtd-payload",
        fail: Exception | None = None,
        wrong_hash: bool = False,
    ):
        self.payload = payload
        self.fail = fail
        self.wrong_hash = wrong_hash
        self.calls: list[dict] = []

    def __call__(
        self,
        source_path,
        texture_index,
        image_path,
        destination_path,
        *,
        quality,
        overwrite,
    ) -> WTDTextureReplacementResult:
        source = Path(source_path)
        image = Path(image_path)
        destination = Path(destination_path)
        self.calls.append(
            {
                "source": source,
                "texture_index": texture_index,
                "image": image,
                "destination": destination,
                "quality": quality,
                "overwrite": overwrite,
            }
        )
        if self.fail is not None:
            raise self.fail
        assert source.read_bytes() == b"source-wtd"
        destination.write_bytes(self.payload)
        output_hash = hashlib.sha256(self.payload).hexdigest()
        if self.wrong_hash:
            output_hash = "0" * 64
        return WTDTextureReplacementResult(
            source_path=source.resolve(),
            output_path=destination.resolve(),
            replacement_image_path=image.resolve(),
            texture=_texture_entry(texture_index),
            output_size=len(self.payload),
            output_sha256=output_hash,
            virtual_sha256="1" * 64,
        )


def _temporary_artifacts(tmp_path: Path) -> list[Path]:
    return [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith(".gtaiv_toolkit_rpf_")
    ]


def test_combined_transaction_commits_patched_wtd_and_retains_rpf_backup(tmp_path):
    archive, executable, image = _files(tmp_path)
    original_archive = archive.read_bytes()
    replacer = FakeWTDReplacer()

    result = replace_rpf_wtd_texture_from_image_transactional(
        archive,
        executable,
        "textures\\HUD.WTD",
        3,
        image,
        quality=0.75,
        parser_factory=MutableParserFactory(),
        wtd_replacer=replacer,
    )

    committed = MutableArchiveParser(str(archive), str(executable))
    backup = MutableArchiveParser(str(result.backup_path), str(executable))
    assert committed.read_file("textures/HUD.WTD") == replacer.payload
    assert committed.read_file("textures/MAP.WTD") == b"neighbor"
    assert backup.read_file("textures/HUD.WTD") == b"source-wtd"
    assert result.archive_path == archive.resolve()
    assert result.entry_path == "textures/HUD.WTD"
    assert result.replacement_image_path == image.resolve()
    assert result.texture == _texture_entry(3)
    assert result.previous_entry_size == len(b"source-wtd")
    assert result.replacement_entry_size == len(replacer.payload)
    assert result.relocated is True
    assert result.wtd_sha256 == hashlib.sha256(replacer.payload).hexdigest()
    assert result.virtual_sha256 == "1" * 64
    assert result.backup_path.read_bytes() == original_archive
    assert replacer.calls[0]["quality"] == 0.75
    assert replacer.calls[0]["overwrite"] is False
    assert _temporary_artifacts(tmp_path) == []


def test_combined_transaction_leaves_archive_untouched_when_wtd_patch_fails(tmp_path):
    archive, executable, image = _files(tmp_path)
    original_archive = archive.read_bytes()

    with pytest.raises(RuntimeError, match="WTD patch failed"):
        replace_rpf_wtd_texture_from_image_transactional(
            archive,
            executable,
            "textures/HUD.WTD",
            3,
            image,
            parser_factory=MutableParserFactory(),
            wtd_replacer=FakeWTDReplacer(fail=RuntimeError("WTD patch failed")),
        )

    assert archive.read_bytes() == original_archive
    assert not list(tmp_path.glob("example.backup-*.rpf"))
    assert _temporary_artifacts(tmp_path) == []


def test_combined_transaction_rejects_unverified_wtd_output(tmp_path):
    archive, executable, image = _files(tmp_path)
    original_archive = archive.read_bytes()

    with pytest.raises(RuntimeError, match="output hash does not match"):
        replace_rpf_wtd_texture_from_image_transactional(
            archive,
            executable,
            "textures/HUD.WTD",
            3,
            image,
            parser_factory=MutableParserFactory(),
            wtd_replacer=FakeWTDReplacer(wrong_hash=True),
        )

    assert archive.read_bytes() == original_archive
    assert not list(tmp_path.glob("example.backup-*.rpf"))
    assert _temporary_artifacts(tmp_path) == []


def test_combined_transaction_rolls_back_if_rpf_commit_replaces_then_raises(tmp_path):
    archive, executable, image = _files(tmp_path)
    original_archive = archive.read_bytes()

    def replace_then_fail(source: str, destination: str) -> None:
        os.replace(source, destination)
        raise OSError("post-replace failure")

    with pytest.raises(OSError, match="post-replace failure"):
        replace_rpf_wtd_texture_from_image_transactional(
            archive,
            executable,
            "textures/HUD.WTD",
            3,
            image,
            parser_factory=MutableParserFactory(),
            replace_file=replace_then_fail,
            wtd_replacer=FakeWTDReplacer(),
        )

    assert archive.read_bytes() == original_archive
    assert not list(tmp_path.glob("example.backup-*.rpf"))
    assert _temporary_artifacts(tmp_path) == []


def test_combined_transaction_requires_wtd_entry_path(tmp_path):
    archive, executable, image = _files(tmp_path)

    with pytest.raises(ValueError, match="must identify a .wtd"):
        replace_rpf_wtd_texture_from_image_transactional(
            archive,
            executable,
            "textures/HUD.DDS",
            3,
            image,
            parser_factory=MutableParserFactory(),
            wtd_replacer=FakeWTDReplacer(),
        )
