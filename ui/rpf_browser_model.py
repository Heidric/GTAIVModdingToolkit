"""Pure tree model used by the RPF browser UI."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class RPFEntryLike(Protocol):
    path: str
    size: int
    offset: int


@dataclass(frozen=True)
class RPFBrowserNode:
    """One directory or file node in the user-facing RPF tree."""

    name: str
    path: str
    entry: RPFEntryLike | None
    children: tuple["RPFBrowserNode", ...] = ()

    @property
    def is_directory(self) -> bool:
        return self.entry is None


class _MutableNode:
    def __init__(self, name: str, path: str):
        self.name = name
        self.path = path
        self.entry: RPFEntryLike | None = None
        self.children: dict[str, _MutableNode] = {}


def build_rpf_browser_tree(
    entries: tuple[RPFEntryLike, ...] | list[RPFEntryLike],
) -> tuple[RPFBrowserNode, ...]:
    """Build a deterministic directory tree from normalized RPF entry paths."""
    root = _MutableNode("", "")

    for entry in entries:
        parts = entry.path.split("/")
        current = root
        prefix: list[str] = []
        for index, part in enumerate(parts):
            prefix.append(part)
            child_path = "/".join(prefix)
            child = current.children.get(part)
            if child is None:
                child = _MutableNode(part, child_path)
                current.children[part] = child
            if index == len(parts) - 1:
                if child.entry is not None:
                    raise ValueError(f"duplicate RPF browser path: {entry.path}")
                if child.children:
                    raise ValueError(
                        f"RPF path is both a file and directory: {entry.path}"
                    )
                child.entry = entry
            elif child.entry is not None:
                raise ValueError(
                    f"RPF path is both a file and directory: {child.path}"
                )
            current = child

    def freeze(node: _MutableNode) -> RPFBrowserNode:
        children = tuple(
            freeze(child)
            for child in sorted(
                node.children.values(),
                key=lambda item: (
                    item.entry is not None,
                    item.name.casefold(),
                    item.name,
                ),
            )
        )
        return RPFBrowserNode(
            name=node.name,
            path=node.path,
            entry=node.entry,
            children=children,
        )

    return tuple(freeze(child) for child in sorted(
        root.children.values(),
        key=lambda item: (
            item.entry is not None,
            item.name.casefold(),
            item.name,
        ),
    ))
