import json
import zipfile

import pytest

from core.support_bundle import create_support_bundle, redact_text


def test_redact_text_replaces_game_home_and_temp_paths():
    text = (
        r"C:\Users\Michael\Games\GTAIV\GTAIV.exe "
        r"C:/Users/Michael/AppData/Local/Temp/work "
        r"C:\Users\Michael\file.txt"
    )

    redacted = redact_text(
        text,
        gtaiv_path=r"C:\Users\Michael\Games\GTAIV",
        home_directory=r"C:\Users\Michael",
        temporary_directory=r"C:\Users\Michael\AppData\Local\Temp",
    )

    assert "Michael" not in redacted
    assert "<GTAIV_PATH>" in redacted
    assert "<TEMP_PATH>" in redacted
    assert "<USER_HOME>" in redacted


def test_create_support_bundle_contains_redacted_metadata_and_logs(tmp_path):
    home = tmp_path / "Users" / "Michael"
    game = home / "Games" / "GTAIV"
    temp = home / "AppData" / "Local" / "Temp"
    logs = home / "AppData" / "Local" / "GTAIVModdingToolkit" / "logs"

    (game / "pc" / "audio" / "sfx").mkdir(parents=True)
    (game / "pc" / "textures").mkdir(parents=True)
    temp.mkdir(parents=True)
    logs.mkdir(parents=True)
    (game / "GTAIV.exe").write_bytes(b"not included")
    (game / "pc" / "audio" / "sfx" / "radio_vladivostok.rpf").write_bytes(
        b"not included"
    )
    (game / "pc" / "textures" / "radio_hud.wtd").write_bytes(b"not included")
    (logs / "app.log").write_text(
        f"Opened {game / 'GTAIV.exe'} from {home}\n",
        encoding="utf-8",
    )

    output = tmp_path / "support.zip"
    result = create_support_bundle(
        output,
        gtaiv_path=game,
        log_directory=logs,
        home_directory=home,
        temporary_directory=temp,
    )

    assert result.output_path == output.resolve()
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert {"diagnostics.json", "privacy.txt", "logs/app.log"} <= names
        diagnostics = archive.read("diagnostics.json").decode("utf-8")
        log_text = archive.read("logs/app.log").decode("utf-8")
        parsed = json.loads(diagnostics)

    assert parsed["game"]["provided"] is True
    assert parsed["game"]["root"] == "<GTAIV_PATH>"
    assert any(
        "<GTAIV_PATH>" in entry["path"]
        for entry in parsed["game"]["paths"]
    )
    assert str(home) not in diagnostics
    assert str(game) not in diagnostics
    assert str(home) not in log_text
    assert str(game) not in log_text
    assert "<GTAIV_PATH>" in diagnostics
    assert "<GTAIV_PATH>" in log_text
    assert not any(name.endswith((".exe", ".rpf", ".wtd")) for name in names)


def test_create_support_bundle_requires_zip_extension(tmp_path):
    with pytest.raises(ValueError, match=r"\.zip extension"):
        create_support_bundle(tmp_path / "support.txt")
