from __future__ import annotations

from copy import deepcopy
from typing import Any


def apply_patch_list(state: dict[str, Any] | None, patches: Any) -> dict[str, Any] | None:
    if not isinstance(state, dict) or not isinstance(patches, list):
        return state
    next_state: Any = deepcopy(state)
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        op = patch.get("op")
        path = _patch_path_parts(patch.get("path"))
        if path is None:
            continue
        if not path:
            if op in {"add", "replace"} and isinstance(patch.get("value"), dict):
                next_state = deepcopy(patch["value"])
            continue
        parent_pair = _get_patch_parent(next_state, path)
        if parent_pair is None:
            continue
        parent, key = parent_pair
        if isinstance(parent, list):
            index = len(parent) if key == "-" else _list_index(key)
            if index is None:
                continue
            if op == "add" and 0 <= index <= len(parent):
                parent.insert(index, deepcopy(patch.get("value")))
            elif op == "replace" and 0 <= index < len(parent):
                parent[index] = deepcopy(patch.get("value"))
            elif op == "remove" and 0 <= index < len(parent):
                parent.pop(index)
            continue
        if isinstance(parent, dict):
            if op in {"add", "replace"}:
                parent[key] = deepcopy(patch.get("value"))
            elif op == "remove":
                parent.pop(key, None)
    return next_state if isinstance(next_state, dict) else state


def _get_patch_parent(root: Any, path: list[Any]) -> tuple[Any, Any] | None:
    if not path:
        return None
    current = root
    for part in path[:-1]:
        if isinstance(current, list):
            index = _list_index(part)
            if index is None or index < 0 or index >= len(current):
                return None
            current = current[index]
            continue
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
            continue
        return None
    return current, path[-1]


def _patch_path_parts(path: Any) -> list[Any] | None:
    if isinstance(path, list):
        return path
    if not isinstance(path, str):
        return None
    if path == "":
        return []
    parts: list[Any] = []
    for part in path.strip("/").split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        index = _list_index(part)
        parts.append(index if index is not None else part)
    return parts


def _list_index(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None

