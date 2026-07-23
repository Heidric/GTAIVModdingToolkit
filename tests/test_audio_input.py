from pathlib import Path

import pytest

from core.audio_input import (
    AUDIO_FILE_FILTER,
    SUPPORTED_AUDIO_EXTENSIONS,
    validate_replacement_audio,
)


@pytest.mark.parametrize("extension", SUPPORTED_AUDIO_EXTENSIONS)
def test_supported_audio_extensions_are_accepted_case_insensitively(
    tmp_path: Path,
    extension: str,
):
    source = tmp_path / f"replacement{extension.upper()}"
    source.write_bytes(b"audio")

    assert validate_replacement_audio(source) == source.resolve()


def test_audio_file_filter_exposes_every_supported_extension():
    for extension in SUPPORTED_AUDIO_EXTENSIONS:
        assert f"*{extension}" in AUDIO_FILE_FILTER


def test_unsupported_audio_extension_is_rejected(tmp_path: Path):
    source = tmp_path / "replacement.txt"
    source.write_bytes(b"not audio")

    with pytest.raises(ValueError, match="Unsupported replacement audio format: TXT"):
        validate_replacement_audio(source)


def test_missing_audio_file_is_rejected_before_extension_validation(tmp_path: Path):
    missing = tmp_path / "missing.ogg"

    with pytest.raises(FileNotFoundError, match="Replacement audio not found"):
        validate_replacement_audio(missing)


def test_empty_audio_path_is_rejected_explicitly():
    with pytest.raises(ValueError, match="must identify a file"):
        validate_replacement_audio("   ")
