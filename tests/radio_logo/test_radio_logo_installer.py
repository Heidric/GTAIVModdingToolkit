from pathlib import Path

import pytest

import core.radio_logo.installer as installer
from core.radio_logo.installer import (
    KNOWN_RADIO_LOGO_WTD_NAMES,
    RadioLogoInstallError,
    RadioLogoTarget,
    get_radio_logo_destination_dir,
    install_radio_logo_pack,
)


def _make_game(tmp_path: Path, *targets: RadioLogoTarget) -> Path:
    game_root = tmp_path / "GTAIV"
    game_root.mkdir()
    relative_directories = {
        RadioLogoTarget.GTA_IV: Path("pc") / "textures",
        RadioLogoTarget.TLAD: Path("TLAD") / "pc" / "textures",
        RadioLogoTarget.TBOGT: Path("TBoGT") / "pc" / "textures",
    }
    for target in targets:
        (game_root / relative_directories[target]).mkdir(parents=True)
    return game_root


def _source(tmp_path: Path, name: str, data: bytes = b"replacement-wtd") -> Path:
    source_dir = tmp_path / "pack"
    source_dir.mkdir(exist_ok=True)
    source = source_dir / name
    source.write_bytes(data)
    return source


@pytest.mark.parametrize(
    ("target", "relative"),
    [
        (RadioLogoTarget.GTA_IV, Path("pc") / "textures"),
        (RadioLogoTarget.TLAD, Path("TLAD") / "pc" / "textures"),
        (RadioLogoTarget.TBOGT, Path("TBoGT") / "pc" / "textures"),
    ],
)
def test_destination_directory_supports_direct_and_fusionfix(
    tmp_path: Path,
    target: RadioLogoTarget,
    relative: Path,
):
    direct = get_radio_logo_destination_dir(tmp_path, target, use_direct=True)
    fusion = get_radio_logo_destination_dir(tmp_path, target, use_direct=False)

    assert direct == tmp_path.resolve() / relative
    assert fusion == tmp_path.resolve() / "update" / relative


@pytest.mark.parametrize("filename", sorted(KNOWN_RADIO_LOGO_WTD_NAMES))
def test_every_known_radio_logo_filename_can_be_installed(tmp_path: Path, filename: str):
    game_root = _make_game(tmp_path, RadioLogoTarget.GTA_IV)
    source = _source(tmp_path, filename)

    result = install_radio_logo_pack(
        game_root,
        [source],
        RadioLogoTarget.GTA_IV,
        use_direct=False,
    )

    destination = game_root / "update" / "pc" / "textures" / filename
    assert destination.read_bytes() == source.read_bytes()
    assert result[0].destination_path == str(destination)
    assert result[0].backup_path is None


def test_direct_install_replaces_existing_file_and_creates_backup(tmp_path: Path):
    game_root = _make_game(tmp_path, RadioLogoTarget.GTA_IV)
    destination = game_root / "pc" / "textures" / "radio_hud_noncolored.wtd"
    destination.write_bytes(b"vanilla")
    source = _source(tmp_path, destination.name, b"modded")

    result = install_radio_logo_pack(
        game_root,
        [source],
        RadioLogoTarget.GTA_IV,
        use_direct=True,
    )

    assert destination.read_bytes() == b"modded"
    backup = Path(result[0].backup_path)
    assert backup.is_file()
    assert backup.read_bytes() == b"vanilla"


def test_multiple_files_commit_together(tmp_path: Path):
    game_root = _make_game(tmp_path, RadioLogoTarget.TLAD)
    first = _source(tmp_path, "radio_hud_colored_eflc.wtd", b"colored")
    second = _source(tmp_path, "radio_hud_noncolored_eflc.wtd", b"plain")

    result = install_radio_logo_pack(
        game_root,
        [first, second],
        RadioLogoTarget.TLAD,
        use_direct=False,
    )

    destination_dir = game_root / "update" / "TLAD" / "pc" / "textures"
    assert (destination_dir / first.name).read_bytes() == b"colored"
    assert (destination_dir / second.name).read_bytes() == b"plain"
    assert len(result) == 2


def test_unknown_filename_is_rejected_before_destination_is_created(tmp_path: Path):
    game_root = _make_game(tmp_path, RadioLogoTarget.GTA_IV)
    source = _source(tmp_path, "arbitrary.wtd")

    with pytest.raises(ValueError, match="Unsupported radio-logo WTD filename"):
        install_radio_logo_pack(
            game_root,
            [source],
            RadioLogoTarget.GTA_IV,
            use_direct=False,
        )

    assert not (game_root / "update").exists()


def test_duplicate_filenames_are_rejected_case_insensitively(tmp_path: Path):
    game_root = _make_game(tmp_path, RadioLogoTarget.GTA_IV)
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    first_dir.mkdir()
    second_dir.mkdir()
    first = first_dir / "radio_hud.wtd"
    second = second_dir / "RADIO_HUD.WTD"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    with pytest.raises(ValueError, match="Duplicate radio-logo WTD filename"):
        install_radio_logo_pack(
            game_root,
            [first, second],
            RadioLogoTarget.GTA_IV,
            use_direct=False,
        )


def test_missing_episode_target_is_rejected_in_fusionfix_mode(tmp_path: Path):
    game_root = _make_game(tmp_path, RadioLogoTarget.GTA_IV)
    source = _source(tmp_path, "radio_hud_colored_eflc.wtd")

    with pytest.raises(FileNotFoundError, match="selected GTA IV target is not installed"):
        install_radio_logo_pack(
            game_root,
            [source],
            RadioLogoTarget.TLAD,
            use_direct=False,
        )


def test_empty_wtd_is_rejected(tmp_path: Path):
    game_root = _make_game(tmp_path, RadioLogoTarget.GTA_IV)
    source = _source(tmp_path, "radio_hud.wtd", b"")

    with pytest.raises(ValueError, match="WTD file is empty"):
        install_radio_logo_pack(
            game_root,
            [source],
            RadioLogoTarget.GTA_IV,
            use_direct=False,
        )


def test_commit_failure_restores_every_existing_destination(tmp_path: Path, monkeypatch):
    game_root = _make_game(tmp_path, RadioLogoTarget.GTA_IV)
    destination_dir = game_root / "pc" / "textures"
    first_destination = destination_dir / "radio_hud_colored.wtd"
    second_destination = destination_dir / "radio_hud_noncolored.wtd"
    first_destination.write_bytes(b"first-vanilla")
    second_destination.write_bytes(b"second-vanilla")

    first_source = _source(tmp_path, first_destination.name, b"first-modded")
    second_source = _source(tmp_path, second_destination.name, b"second-modded")

    real_atomic_replace = installer._atomic_replace
    staged_replacements = 0

    def fail_on_second_staged_replace(source: Path, destination: Path) -> None:
        nonlocal staged_replacements
        if source.name.startswith(".gtaiv_toolkit_logo_stage_"):
            staged_replacements += 1
            if staged_replacements == 2:
                raise OSError("simulated commit failure")
        real_atomic_replace(source, destination)

    monkeypatch.setattr(installer, "_atomic_replace", fail_on_second_staged_replace)

    with pytest.raises(RadioLogoInstallError, match="was rolled back"):
        install_radio_logo_pack(
            game_root,
            [first_source, second_source],
            RadioLogoTarget.GTA_IV,
            use_direct=True,
        )

    assert first_destination.read_bytes() == b"first-vanilla"
    assert second_destination.read_bytes() == b"second-vanilla"
    assert not list(destination_dir.glob("*.backup-*"))
    assert not list(destination_dir.glob(".gtaiv_toolkit_logo_*"))
