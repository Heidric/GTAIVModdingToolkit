import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from core.archive_texture_batch import (
    TextureReplacementRequest,
    replace_archive_textures_transactional,
)


class JsonArchiveParser:
    OFFSETS = {"shop.wdr": 0x1000, "lod.wtd": 0x2000}

    def __init__(self, archive_path: str, _executable_path: str):
        self.archive_path = Path(archive_path)
        self._load()

    def _load(self):
        encoded = json.loads(self.archive_path.read_text(encoding="utf-8"))
        self.payloads = {key: base64.b64decode(value) for key, value in encoded.items()}
        self.paths = [
            {"path": path, "size": len(payload), "offset": self.OFFSETS[path]}
            for path, payload in self.payloads.items()
        ]

    def read_file(self, file_path: str) -> bytes:
        return self.payloads[file_path]

    def add_file(self, source_file: str, entry_path: str) -> None:
        self.payloads[entry_path] = Path(source_file).read_bytes()
        encoded = {
            key: base64.b64encode(value).decode("ascii")
            for key, value in self.payloads.items()
        }
        self.archive_path.write_text(json.dumps(encoded, sort_keys=True), encoding="utf-8")
        self._load()


def _resource_replacer(source, texture_index, image, output, **_kwargs):
    names = {1: "front", 2: "signs", 3: "lod"}
    payload = Path(source).read_bytes() + f"|{texture_index}:{Path(image).name}".encode()
    Path(output).write_bytes(payload)
    return SimpleNamespace(
        texture=SimpleNamespace(index=texture_index, name=names[texture_index]),
        output_path=Path(output).resolve(),
        output_size=len(payload),
        output_sha256=hashlib.sha256(payload).hexdigest(),
    )


def test_batch_replaces_multiple_resources_with_one_backup(tmp_path):
    archive = tmp_path / "brook_s3.img"
    executable = tmp_path / "GTAIV.exe"
    executable.write_bytes(b"exe")
    initial = {
        "shop.wdr": base64.b64encode(b"shop").decode("ascii"),
        "lod.wtd": base64.b64encode(b"lod").decode("ascii"),
    }
    archive.write_text(json.dumps(initial), encoding="utf-8")
    images = []
    for name in ("front", "signs", "lod"):
        image = tmp_path / f"{name}.png"
        image.write_bytes(name.encode())
        images.append(image)

    result = replace_archive_textures_transactional(
        archive,
        executable,
        (
            TextureReplacementRequest("shop.wdr", 1, "front", images[0]),
            TextureReplacementRequest("shop.wdr", 2, "signs", images[1]),
            TextureReplacementRequest("lod.wtd", 3, "lod", images[2]),
        ),
        parser_factory=JsonArchiveParser,
        wtd_replacer=_resource_replacer,
        wdr_replacer=_resource_replacer,
        rolling_backup_limit=2,
    )

    parser = JsonArchiveParser(str(archive), str(executable))
    assert parser.read_file("shop.wdr") == b"shop|1:front.png|2:signs.png"
    assert parser.read_file("lod.wtd") == b"lod|3:lod.png"
    assert result.backup_path.is_file()
    assert len(tuple(tmp_path.glob("brook_s3.backup-*.img"))) == 1
    assert len(result.replacements) == 3
    assert len(result.entries) == 2
