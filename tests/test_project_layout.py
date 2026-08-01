"""Repository-layout regression checks for release-facing source files."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_modules_are_grouped_under_packages():
    expected = (
        ROOT / "core" / "build_info.py",
        ROOT / "core" / "build_metadata.py",
        ROOT / "core" / "audio_replacement" / "audio_utils.py",
        ROOT / "core" / "audio_replacement" / "batch_replacement.py",
        ROOT / "core" / "audio_replacement" / "single_replacement.py",
        ROOT / "core" / "audio_replacement" / "replacement_strategy.py",
        ROOT / "ui" / "application_utils.py",
    )
    assert all(path.is_file() for path in expected)


def test_legacy_root_modules_and_release_files_are_absent():
    obsolete = (
        "audio_utils.py",
        "batch_replacement.py",
        "build_info.py",
        "build_metadata.py",
        "replacement_strategy.py",
        "single_replacement.py",
        "utils.py",
        "GTAIVModdingToolkit.spec",
        "ivradio.spec",
        ".github/workflows/release.yml",
    )
    assert all(not (ROOT / relative).exists() for relative in obsolete)


def test_portable_spec_and_readme_images_have_named_locations():
    assert (ROOT / "packaging" / "GTAIVModdingToolkit.spec").is_file()
    assert (ROOT / "docs" / "images" / "radio-station-selection.png").is_file()
    assert (ROOT / "docs" / "images" / "radio-track-selection.png").is_file()
