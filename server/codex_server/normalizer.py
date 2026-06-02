from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Any

from .models import Message, ThreadDetail, ThreadSummary


def compact_text(value: Any, limit: int = 180) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."


def find_text(value: Any, limit: int = 10000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(part for part in (find_text(item, limit) for item in value) if part)
    if isinstance(value, dict):
        root = value.get("root")
        if isinstance(root, dict):
            text = find_text(root, limit)
            if text:
                return text
        for key in ("text", "message", "input", "content", "value", "aggregatedOutput"):
            if key in value:
                text = find_text(value.get(key), limit)
                if text:
                    return text[:limit]
    return ""


def latest_turn(state: dict[str, Any] | None) -> dict[str, Any] | None:
    turns = state.get("turns") if isinstance(state, dict) else None
    if isinstance(turns, list) and turns:
        turn = turns[-1]
        return turn if isinstance(turn, dict) else None
    return None


def latest_item(state: dict[str, Any] | None) -> Any:
    turn = latest_turn(state)
    items = turn.get("items") if isinstance(turn, dict) else None
    if isinstance(items, list) and items:
        item = items[-1]
        return item.get("root", item) if isinstance(item, dict) else item
    return None


def thread_title(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return "(untitled)"
    return compact_text(state.get("title") or state.get("preview") or "(untitled)", 90)


def runtime_status(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return "unknown"
    runtime = state.get("threadRuntimeStatus")
    if isinstance(runtime, dict):
        return str(runtime.get("type") or runtime.get("status") or "unknown")
    return str(runtime or "unknown")


def thread_cwd(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return ""
    return str(state.get("cwd") or "")


def turn_status(state: dict[str, Any] | None) -> str:
    turn = latest_turn(state)
    if not isinstance(turn, dict):
        return "-"
    return str(turn.get("status") or "-")


def summarize_item(item: Any, limit: int = 180) -> str:
    if not isinstance(item, dict):
        return compact_text(item, limit)
    root = item.get("root")
    if isinstance(root, dict):
        item = root
    item_type = item.get("type") or item.get("kind") or "item"
    if item_type in {"userMessage", "user-message", "user_message"}:
        text = find_text(item.get("content") or item.get("message") or item.get("input"))
        return compact_text(f"user: {text}" if text else "user", limit)
    if item_type in {"agentMessage", "agent-message", "assistantMessage"}:
        text = find_text(item.get("text") or item.get("content") or item.get("message"))
        return compact_text(f"agent: {text}" if text else "agent", limit)
    if item_type in {"steeringUserMessage", "steering-user-message"}:
        text = find_text(item.get("input") or item.get("content"))
        status = item.get("status")
        suffix = f" [{status}]" if status else ""
        return compact_text(f"steer: {text}{suffix}" if text else f"steer{suffix}", limit)
    if item_type in {"commandExecution", "command-execution"}:
        command = compact_text(item.get("command") or item.get("cmd") or "", 110)
        status = item.get("status") or "unknown"
        return compact_text(f"command:{status} {command}".strip(), limit)
    if item_type in {"mcpToolCall", "mcp-tool-call", "toolCall"}:
        name = item.get("name") or item.get("toolName") or item.get("method") or ""
        status = item.get("status") or ""
        return compact_text(f"{item_type}:{status} {name}".strip(), limit)
    if item_type == "reasoning":
        return "reasoning"
    text = find_text(item)
    return compact_text(f"{item_type}: {text}" if text else str(item_type), limit)


def get_patch_parent(root: Any, path: list[Any]) -> tuple[Any, Any] | None:
    if not path:
        return None
    current = root
    for part in path[:-1]:
        if isinstance(current, list):
            if not isinstance(part, int) or part < 0 or part >= len(current):
                return None
            current = current[part]
            continue
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
            continue
        return None
    return current, path[-1]


def apply_patch_list(state: dict[str, Any] | None, patches: Any) -> dict[str, Any] | None:
    if not isinstance(state, dict) or not isinstance(patches, list):
        return state
    next_state: Any = deepcopy(state)
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        op = patch.get("op")
        path = patch.get("path")
        if not isinstance(path, list):
            continue
        if not path:
            if op in {"add", "replace"} and isinstance(patch.get("value"), dict):
                next_state = deepcopy(patch["value"])
            continue
        parent_pair = get_patch_parent(next_state, path)
        if parent_pair is None:
            continue
        parent, key = parent_pair
        if isinstance(parent, list):
            index = len(parent) if key == "-" else key
            if not isinstance(index, int):
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


def active_time_from_state(state: dict[str, Any] | None, fallback: float | None = None) -> float | None:
    turn = latest_turn(state)
    if isinstance(turn, dict):
        for key in ("completedAtMs", "turnCompletedAtMs", "turnStartedAtMs", "startedAtMs"):
            value = turn.get(key)
            if isinstance(value, (int, float)):
                return float(value) / 1000 if value > 10_000_000_000 else float(value)
    return fallback


def summary_from_ipc_state(conversation_id: str, state: dict[str, Any] | None, *, seen_at: float | None = None) -> ThreadSummary:
    seen_at = seen_at or time.time()
    return ThreadSummary(
        conversation_id=conversation_id,
        title=thread_title(state),
        cwd=thread_cwd(state),
        source="live",
        runtime_status=runtime_status(state),
        latest_turn_status=turn_status(state),
        latest_item_preview=summarize_item(latest_item(state)),
        active_at=active_time_from_state(state, seen_at),
        updated_at=seen_at,
        has_live_owner=True,
    )


def message_from_item(item: dict[str, Any], message_id: str, created_at: float | None = None) -> Message | None:
    root = item.get("root")
    if isinstance(root, dict):
        item = root
    item_type = item.get("type") or item.get("kind")
    status = item.get("status") or item.get("phase")
    if item_type in {"userMessage", "user-message", "user_message"}:
        return Message(message_id, "user", find_text(item.get("content") or item.get("message") or item.get("input")), status, created_at, item)
    if item_type in {"agentMessage", "agent-message", "assistantMessage"}:
        return Message(message_id, "assistant", find_text(item.get("text") or item.get("content") or item.get("message")), status, created_at, item)
    if item_type in {"commandExecution", "command-execution"}:
        command = item.get("command") or item.get("cmd") or ""
        output = item.get("aggregatedOutput") or item.get("output") or ""
        text = command if not output else f"{command}\n\n{output}"
        return Message(message_id, "command", str(text), status, created_at, item)
    if item_type in {"mcpToolCall", "mcp-tool-call", "toolCall"}:
        return Message(message_id, "tool", summarize_item(item, 1000), status, created_at, item)
    if item_type == "reasoning":
        text = find_text(item.get("summary") or item.get("content") or item)
        return Message(message_id, "reasoning", text, status, created_at, item)
    text = find_text(item)
    if text:
        return Message(message_id, "system", text, status, created_at, item)
    return None


def detail_from_turns(summary: ThreadSummary, turns: list[Any], pagination: dict[str, Any] | None = None) -> ThreadDetail:
    messages: list[Message] = []
    for turn_index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            continue
        turn_id = str(turn.get("turnId") or turn.get("id") or turn_index)
        created_at = None
        started = turn.get("turnStartedAtMs") or turn.get("startedAtMs")
        if isinstance(started, (int, float)):
            created_at = float(started) / 1000 if started > 10_000_000_000 else float(started)
        params = turn.get("params") if isinstance(turn.get("params"), dict) else {}
        items = turn.get("items") or []
        has_user_item = False
        if isinstance(items, list):
            for item in items:
                root = item.get("root", item) if isinstance(item, dict) else item
                if isinstance(root, dict) and root.get("type") in {"userMessage", "user-message", "user_message"}:
                    has_user_item = True
                    break
        input_text = find_text(params.get("input")) if isinstance(params, dict) else ""
        if input_text and not has_user_item:
            messages.append(Message(f"{turn_id}-params-input", "user", input_text, turn.get("status"), created_at, params))
        if isinstance(items, list):
            for item_index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                message = message_from_item(item, f"{turn_id}-item-{item_index}", created_at)
                if message is not None:
                    messages.append(message)
    return ThreadDetail(summary=summary, messages=messages, raw_turns=turns, pagination=pagination)


def sdk_model_to_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value


def summary_from_sdk_thread(thread: dict[str, Any]) -> ThreadSummary:
    updated = thread.get("updatedAt")
    active_at = None
    if isinstance(updated, (int, float)):
        active_at = float(updated)
    status = thread.get("status")
    if isinstance(status, dict):
        runtime = str(status.get("type") or status.get("status") or status.get("root") or "unknown")
    else:
        runtime = str(status or "unknown")
    return ThreadSummary(
        conversation_id=str(thread.get("id") or ""),
        title=compact_text(thread.get("name") or thread.get("title") or thread.get("preview") or "(untitled)", 90),
        cwd=str(thread.get("cwd") or ""),
        source="history-only",
        runtime_status=runtime,
        latest_turn_status="-",
        latest_item_preview=compact_text(thread.get("preview") or "", 180),
        active_at=active_at,
        updated_at=active_at,
        has_live_owner=False,
    )


def safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)
