"""Release metadata consistency checks."""

import re
from pathlib import Path

from build_info import APP_VERSION, release_tag


ROOT = Path(__file__).resolve().parents[1]
WINDOWS_VERSION_INFO = ROOT / "packaging" / "windows_version_info.txt"


def _fixed_version(source: str, field: str) -> tuple[int, int, int, int]:
    match = re.search(rf"\b{field}\s*=\s*\(([^)]*)\)", source)
    assert match is not None, f"{field} is missing from Windows version metadata"
    values = tuple(
        int(part.strip())
        for part in match.group(1).split(",")
        if part.strip()
    )
    assert len(values) == 4, f"{field} must contain four numeric components"
    return values


def _string_version(source: str, field: str) -> str:
    match = re.search(
        rf"StringStruct\('{field}',\s*'([^']+)'\)",
        source,
    )
    assert match is not None, f"{field} is missing from Windows version metadata"
    return match.group(1)


def test_release_versions_are_consistent():
    numeric_version = APP_VERSION.split("-", 1)[0]
    components = tuple(int(part) for part in numeric_version.split("."))
    assert len(components) <= 4
    expected_fixed = components + (0,) * (4 - len(components))

    source = WINDOWS_VERSION_INFO.read_text(encoding="utf-8")

    assert release_tag() == f"v{APP_VERSION}"
    assert _fixed_version(source, "filevers") == expected_fixed
    assert _fixed_version(source, "prodvers") == expected_fixed
    assert _string_version(source, "FileVersion") == APP_VERSION
    assert _string_version(source, "ProductVersion") == APP_VERSION
