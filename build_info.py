"""Application version and user-visible build metadata."""

from __future__ import annotations

from build_metadata import BUILD_CHANNEL, BUILD_COMMIT, BUILD_DATE_UTC

APP_NAME = "GTA IV Modding Toolkit"
APP_VERSION = "0.15.0"


def application_title() -> str:
    return f"{APP_NAME} {APP_VERSION}"


def release_tag() -> str:
    return f"v{APP_VERSION}"


def validate_release_ref(ref_name: str) -> None:
    expected = release_tag()
    if ref_name != expected:
        raise ValueError(
            f"Release tag {ref_name!r} does not match application version; "
            f"expected {expected!r}."
        )


def short_commit() -> str:
    commit = BUILD_COMMIT.strip()
    if not commit or commit == "development":
        return "development"
    return commit[:12]


def build_summary() -> str:
    lines = [
        application_title(),
        f"Channel: {BUILD_CHANNEL or 'source'}",
        f"Commit: {short_commit()}",
    ]
    if BUILD_DATE_UTC:
        lines.append(f"Built: {BUILD_DATE_UTC}")
    return "\n".join(lines)
