"""Application version and user-visible build metadata."""

from __future__ import annotations

APP_NAME = "GTA IV Modding Toolkit"
APP_VERSION = "0.15.0"


def application_title() -> str:
    return f"{APP_NAME} {APP_VERSION}"
