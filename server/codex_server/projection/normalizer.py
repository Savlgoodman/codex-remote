from __future__ import annotations

from typing import Any


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
        return value[:limit]
    if isinstance(value, list):
        text = " ".join(part for part in (find_text(item, limit) for item in value) if part)
        return text[:limit]
    if isinstance(value, dict):
        root = value.get("root")
        if isinstance(root, dict):
            text = find_text(root, limit)
            if text:
                return text[:limit]
        for key in ("text", "message", "input", "content", "value", "aggregatedOutput"):
            if key in value:
                text = find_text(value.get(key), limit)
                if text:
                    return text[:limit]
    return ""


def item_role(item: dict[str, Any]) -> str:
    item_type = str(item.get("type") or item.get("kind") or "")
    if item_type in {"userMessage", "user-message", "user_message", "steeringUserMessage"}:
        return "user"
    if item_type in {"agentMessage", "agent-message", "assistantMessage"}:
        return "assistant"
    if item_type == "reasoning":
        return "reasoning"
    if item_type in {"commandExecution", "command-execution"}:
        return "command"
    if "tool" in item_type.lower() or item_type in {"webSearch", "web_search_call"}:
        return "tool"
    return "system"


def summarize_item(item: Any, limit: int = 180) -> str:
    if not isinstance(item, dict):
        return compact_text(item, limit)
    root = item.get("root")
    if isinstance(root, dict):
        item = root
    role = item_role(item)
    text = find_text(item)
    if role == "assistant":
        return compact_text(f"agent: {text}" if text else "agent", limit)
    if role == "user":
        return compact_text(f"user: {text}" if text else "user", limit)
    if role == "reasoning":
        return "reasoning"
    return compact_text(f"{role}: {text}" if text else role, limit)


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


def runtime_status(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return "unknown"
    runtime = state.get("threadRuntimeStatus")
    if isinstance(runtime, dict):
        return str(runtime.get("type") or runtime.get("status") or "unknown")
    return str(runtime or "unknown")


def turn_status(turn: dict[str, Any] | None) -> str:
    if not isinstance(turn, dict):
        return "unknown"
    return str(turn.get("status") or "unknown")

