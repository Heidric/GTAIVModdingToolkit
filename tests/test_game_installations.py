from pathlib import Path

from core.game_installations import (
    discover_gtaiv_installations,
    is_gtaiv_installation,
    parse_steam_libraryfolders,
)


def make_installation(root: Path) -> Path:
    (root / "pc" / "audio" / "sfx").mkdir(parents=True)
    (root / "GTAIV.exe").write_bytes(b"test")
    return root


def test_is_gtaiv_installation_requires_executable_and_audio_directory(tmp_path):
    root = tmp_path / "GTAIV"
    (root / "pc" / "audio" / "sfx").mkdir(parents=True)

    assert is_gtaiv_installation(root) is False

    (root / "GTAIV.exe").write_bytes(b"test")
    assert is_gtaiv_installation(root) is True


def test_parse_steam_libraryfolders_supports_modern_and_legacy_entries():
    text = r'''
"libraryfolders"
{
    "0"    "C:\\Program Files (x86)\\Steam"
    "1"
    {
        "path"    "D:\\SteamLibrary"
        "apps"
        {
            "12210"    "1"
        }
    }
}
'''

    assert parse_steam_libraryfolders(text) == (
        Path(r"C:\Program Files (x86)\Steam"),
        Path(r"D:\SteamLibrary"),
    )


def test_discovery_reads_gtaiv_path_environment_variable(tmp_path):
    game = make_installation(tmp_path / "Game")

    detected = discover_gtaiv_installations(
        environment={"GTAIV_PATH": str(game)},
        steam_roots=(),
    )

    assert len(detected) == 1
    assert detected[0].path == game.resolve()
    assert detected[0].source == "GTAIV_PATH"


def test_discovery_reads_additional_steam_library_and_deduplicates(tmp_path):
    steam = tmp_path / "Steam"
    library = tmp_path / "Library"
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        f'"path" "{str(library).replace(chr(92), chr(92) * 2)}"\n',
        encoding="utf-8",
    )
    game = make_installation(
        library / "steamapps" / "common" / "Grand Theft Auto IV" / "GTAIV"
    )

    detected = discover_gtaiv_installations(
        additional_candidates=(game,),
        environment={},
        steam_roots=(steam,),
    )

    assert len(detected) == 1
    assert detected[0].path == game.resolve()
    assert detected[0].source == "Saved or selected path"
