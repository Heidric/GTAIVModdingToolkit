"""Explicit guard for unsafe full WTD reconstruction experiments."""

from __future__ import annotations

import os
from collections.abc import Mapping


EXPERIMENTAL_WTD_ENVIRONMENT_VARIABLE = (
    "GTAIV_TOOLKIT_ENABLE_EXPERIMENTAL_WTD_REBUILD"
)
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


class ExperimentalWtdRebuildDisabledError(RuntimeError):
    """Raised when an unsafe full-WTD rebuild path is used implicitly."""


def experimental_wtd_rebuild_enabled(
    explicit: bool = False,
    *,
    environment: Mapping[str, str] | None = None,
) -> bool:
    """Return whether full WTD reconstruction was explicitly enabled."""

    if explicit:
        return True
    env = os.environ if environment is None else environment
    return env.get(EXPERIMENTAL_WTD_ENVIRONMENT_VARIABLE, "").strip().casefold() in _TRUE_VALUES


def require_experimental_wtd_rebuild(
    explicit: bool = False,
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Reject full WTD reconstruction unless the caller opted in explicitly."""

    if experimental_wtd_rebuild_enabled(explicit, environment=environment):
        return
    raise ExperimentalWtdRebuildDisabledError(
        "Full WTD reconstruction is experimental and is not used by the "
        "production radio-logo workflow. Pass allow_experimental=True, use the "
        "CLI --experimental flag, or set "
        f"{EXPERIMENTAL_WTD_ENVIRONMENT_VARIABLE}=1 to acknowledge the risk."
    )
