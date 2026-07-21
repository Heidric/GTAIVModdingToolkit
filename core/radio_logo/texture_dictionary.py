"""Experimental full GTA IV WTD reconstruction through texfury.

The production radio-logo workflow uses :mod:`payload_patcher` instead.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable

from .experimental import require_experimental_wtd_rebuild
from .wtd import WTDArchive, read_wtd


class TextureDictionaryError(RuntimeError):
    """Base error for texture-dictionary operations."""


class TexfuryUnavailableError(TextureDictionaryError):
    """Raised when texfury cannot be imported or initialised."""


class TextureDictionaryValidationError(TextureDictionaryError):
    """Raised when a generated WTD does not preserve the required structure."""


@dataclass(frozen=True)
class TextureSignature:
    """Stable metadata and payload fingerprint for one WTD texture."""

    name: str
    width: int
    height: int
    format_name: str
    mip_count: int
    data_size: int | None
    data_sha256: str | None

    @property
    def metadata(self) -> tuple[int, int, str, int, int | None]:
        return (
            self.width,
            self.height,
            self.format_name,
            self.mip_count,
            self.data_size,
        )


@dataclass(frozen=True)
class DictionaryComparison:
    """Structural comparison between an original and generated WTD."""

    missing: tuple[str, ...]
    extra: tuple[str, ...]
    metadata_changed: tuple[str, ...]
    payload_changed: tuple[str, ...]
    unexpected_payload_changed: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not (
            self.missing
            or self.extra
            or self.metadata_changed
            or self.unexpected_payload_changed
        )

    @property
    def identical(self) -> bool:
        return self.valid and not self.payload_changed


@dataclass(frozen=True)
class TextureDictionaryResult:
    """Result of a validated WTD write."""

    source_path: Path
    output_path: Path
    texture_count: int
    output_size: int
    output_sha256: str
    replaced_texture: str | None
    comparison: DictionaryComparison


def _load_texfury() -> SimpleNamespace:
    try:
        from texfury import Game, ITD, Texture
    except (ImportError, OSError) as exc:
        raise TexfuryUnavailableError(
            "texfury 1.6.2 is required for WTD writing; "
            "install runtime dependencies from requirements.txt"
        ) from exc
    return SimpleNamespace(Game=Game, ITD=ITD, Texture=Texture)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _texture_signature(texture) -> TextureSignature:
    data = texture.data
    return TextureSignature(
        name=texture.name,
        width=texture.width,
        height=texture.height,
        format_name=texture.format_name,
        mip_count=texture.mip_count,
        data_size=texture.data_size,
        data_sha256=hashlib.sha256(data).hexdigest() if data is not None else None,
    )


def archive_signatures(archive: WTDArchive) -> dict[str, TextureSignature]:
    """Return exact-name signatures and reject duplicate names."""

    signatures: dict[str, TextureSignature] = {}
    folded_names: dict[str, str] = {}
    for texture in archive.textures:
        folded = texture.name.casefold()
        previous = folded_names.get(folded)
        if previous is not None:
            raise TextureDictionaryValidationError(
                f"duplicate texture names differ only by case: {previous!r}, "
                f"{texture.name!r}"
            )
        folded_names[folded] = texture.name
        signatures[texture.name] = _texture_signature(texture)
    return signatures


def compare_wtd_files(
    source: str | os.PathLike[str],
    candidate: str | os.PathLike[str],
    *,
    allowed_payload_changes: Iterable[str] = (),
) -> DictionaryComparison:
    """Compare two WTD files while optionally allowing selected payload changes."""

    original = archive_signatures(read_wtd(source))
    generated = archive_signatures(read_wtd(candidate))

    original_names = set(original)
    generated_names = set(generated)
    common = original_names & generated_names

    missing = tuple(sorted(original_names - generated_names))
    extra = tuple(sorted(generated_names - original_names))
    metadata_changed = tuple(
        sorted(
            name
            for name in common
            if original[name].metadata != generated[name].metadata
        )
    )
    payload_changed = tuple(
        sorted(
            name
            for name in common
            if original[name].data_sha256 != generated[name].data_sha256
        )
    )

    allowed = {name.casefold() for name in allowed_payload_changes}
    unexpected_payload_changed = tuple(
        name for name in payload_changed if name.casefold() not in allowed
    )

    return DictionaryComparison(
        missing=missing,
        extra=extra,
        metadata_changed=metadata_changed,
        payload_changed=payload_changed,
        unexpected_payload_changed=unexpected_payload_changed,
    )


def _format_comparison(comparison: DictionaryComparison) -> str:
    parts: list[str] = []
    if comparison.missing:
        parts.append(f"missing={list(comparison.missing)!r}")
    if comparison.extra:
        parts.append(f"extra={list(comparison.extra)!r}")
    if comparison.metadata_changed:
        parts.append(f"metadata_changed={list(comparison.metadata_changed)!r}")
    if comparison.unexpected_payload_changed:
        parts.append(
            "unexpected_payload_changed="
            f"{list(comparison.unexpected_payload_changed)!r}"
        )
    return ", ".join(parts) or "no differences"


def _normalise_wtd_paths(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    overwrite: bool,
) -> tuple[Path, Path]:
    source_path = Path(source).expanduser().resolve()
    output_path = Path(output).expanduser().resolve()

    if not source_path.is_file():
        raise FileNotFoundError(source_path)
    if source_path.suffix.lower() != ".wtd":
        raise ValueError(f"source must be a .wtd file: {source_path}")
    if output_path.suffix.lower() != ".wtd":
        raise ValueError(f"output must be a .wtd file: {output_path}")
    if source_path == output_path:
        raise ValueError("source and output WTD paths must be different")
    if output_path.exists() and not overwrite:
        raise FileExistsError(output_path)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    return source_path, output_path


def _load_gta4_dictionary(source_path: Path, texfury) -> object:
    dictionary = texfury.ITD.load(source_path)
    if dictionary.game != texfury.Game.GTA4:
        raise TextureDictionaryError(
            f"expected a GTA IV WTD, texfury detected {dictionary.game!r}"
        )
    return dictionary


def _save_staged(dictionary: object, output_path: Path) -> Path:
    handle, raw_path = tempfile.mkstemp(
        prefix=f".{output_path.stem}.",
        suffix=".wtd.tmp",
        dir=output_path.parent,
    )
    os.close(handle)
    stage_path = Path(raw_path)
    stage_path.unlink(missing_ok=True)

    try:
        dictionary.save(stage_path)
        if not stage_path.is_file() or stage_path.stat().st_size <= 12:
            raise TextureDictionaryError(
                f"texfury did not create a valid staged WTD: {stage_path}"
            )
        return stage_path
    except Exception:
        stage_path.unlink(missing_ok=True)
        raise


def _commit_validated(
    source_path: Path,
    output_path: Path,
    stage_path: Path,
    *,
    allowed_payload_changes: Iterable[str],
    replaced_texture: str | None,
) -> TextureDictionaryResult:
    try:
        comparison = compare_wtd_files(
            source_path,
            stage_path,
            allowed_payload_changes=allowed_payload_changes,
        )
        if not comparison.valid:
            raise TextureDictionaryValidationError(
                "generated WTD failed validation: "
                f"{_format_comparison(comparison)}"
            )

        archive = read_wtd(stage_path)
        os.replace(stage_path, output_path)
        return TextureDictionaryResult(
            source_path=source_path,
            output_path=output_path,
            texture_count=len(archive.textures),
            output_size=output_path.stat().st_size,
            output_sha256=_sha256_file(output_path),
            replaced_texture=replaced_texture,
            comparison=comparison,
        )
    finally:
        stage_path.unlink(missing_ok=True)


def round_trip_wtd(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    overwrite: bool = False,
    allow_experimental: bool = False,
) -> TextureDictionaryResult:
    """Load and re-save a WTD through the experimental full rebuild path."""

    require_experimental_wtd_rebuild(allow_experimental)
    source_path, output_path = _normalise_wtd_paths(
        source,
        output,
        overwrite=overwrite,
    )
    texfury = _load_texfury()
    dictionary = _load_gta4_dictionary(source_path, texfury)
    stage_path = _save_staged(dictionary, output_path)
    return _commit_validated(
        source_path,
        output_path,
        stage_path,
        allowed_payload_changes=(),
        replaced_texture=None,
    )


def _mip_min_size(width: int, height: int, mip_count: int) -> int:
    if mip_count <= 1:
        return max(width, height)
    return max(1, max(width, height) >> (mip_count - 1))


def replace_texture_from_image(
    source: str | os.PathLike[str],
    output: str | os.PathLike[str],
    texture_name: str,
    image: str | os.PathLike[str],
    *,
    quality: float = 0.9,
    overwrite: bool = False,
    allow_experimental: bool = False,
) -> TextureDictionaryResult:
    """Replace one texture through the experimental full rebuild path."""

    require_experimental_wtd_rebuild(allow_experimental)
    if not texture_name or not texture_name.strip():
        raise ValueError("texture_name must not be empty")
    if not 0.0 <= quality <= 1.0:
        raise ValueError("quality must be between 0.0 and 1.0")

    image_path = Path(image).expanduser().resolve()
    if not image_path.is_file():
        raise FileNotFoundError(image_path)

    source_path, output_path = _normalise_wtd_paths(
        source,
        output,
        overwrite=overwrite,
    )
    texfury = _load_texfury()
    dictionary = _load_gta4_dictionary(source_path, texfury)

    try:
        original = dictionary.get(texture_name)
    except KeyError as exc:
        available = ", ".join(dictionary.names())
        raise KeyError(
            f"texture {texture_name!r} is not present in {source_path.name}; "
            f"available: {available}"
        ) from exc

    format_name = original.format.name
    if format_name not in {"BC1", "BC3", "A8R8G8B8"}:
        raise TextureDictionaryError(
            f"cannot encode replacement for {texture_name!r} in {format_name}; "
            "supported writable GTA IV formats are BC1, BC3 and A8R8G8B8"
        )

    replacement = texfury.Texture.from_image(
        image_path,
        format=original.format,
        quality=quality,
        generate_mipmaps=original.mip_count > 1,
        min_mip_size=_mip_min_size(
            original.width,
            original.height,
            original.mip_count,
        ),
        resize=(original.width, original.height),
        resize_to_pot=False,
        name=original.name,
    )

    replacement_metadata = (
        replacement.width,
        replacement.height,
        replacement.format,
        replacement.mip_count,
    )
    expected_metadata = (
        original.width,
        original.height,
        original.format,
        original.mip_count,
    )
    if replacement_metadata != expected_metadata:
        raise TextureDictionaryValidationError(
            f"replacement metadata mismatch for {original.name!r}: "
            f"expected {expected_metadata!r}, got {replacement_metadata!r}"
        )

    dictionary.replace(original.name, replacement)
    stage_path = _save_staged(dictionary, output_path)
    return _commit_validated(
        source_path,
        output_path,
        stage_path,
        allowed_payload_changes=(original.name,),
        replaced_texture=original.name,
    )


def _print_result(result: TextureDictionaryResult) -> None:
    print(f"Output: {result.output_path}")
    print(f"Textures: {result.texture_count}")
    print(f"Size: {result.output_size} bytes")
    print(f"SHA-256: {result.output_sha256}")
    if result.replaced_texture is not None:
        print(f"Replaced: {result.replaced_texture}")
    print(f"Payload changes: {list(result.comparison.payload_changed)}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Round-trip or patch GTA IV WTD files through texfury.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    roundtrip = subparsers.add_parser("roundtrip", help="load and re-save a WTD")
    roundtrip.add_argument("source")
    roundtrip.add_argument("output")
    roundtrip.add_argument("--overwrite", action="store_true")
    roundtrip.add_argument(
        "--experimental",
        action="store_true",
        help="acknowledge that full WTD reconstruction is unsafe for production",
    )

    replace = subparsers.add_parser("replace", help="replace one named texture")
    replace.add_argument("source")
    replace.add_argument("output")
    replace.add_argument("texture")
    replace.add_argument("image")
    replace.add_argument("--quality", type=float, default=0.9)
    replace.add_argument("--overwrite", action="store_true")
    replace.add_argument(
        "--experimental",
        action="store_true",
        help="acknowledge that full WTD reconstruction is unsafe for production",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.command == "roundtrip":
        result = round_trip_wtd(
            args.source,
            args.output,
            overwrite=args.overwrite,
            allow_experimental=args.experimental,
        )
    else:
        result = replace_texture_from_image(
            args.source,
            args.output,
            args.texture,
            args.image,
            quality=args.quality,
            overwrite=args.overwrite,
            allow_experimental=args.experimental,
        )
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
