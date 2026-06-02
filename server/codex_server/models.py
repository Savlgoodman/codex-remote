from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


Json = dict[str, Any]
ThreadSource = Literal["live", "stale", "history-only"]
MessageRole = Literal["user", "assistant", "system", "tool", "command", "reasoning"]


@dataclass
class IpcStatus:
    online: bool = False
    client_id: str | None = None
    connected_at: float | None = None
    last_error: str | None = None
    last_seen_at: float | None = None

    def to_json(self) -> Json:
        return {
            "online": self.online,
            "clientId": self.client_id,
            "connectedAt": self.connected_at,
            "lastError": self.last_error,
            "lastSeenAt": self.last_seen_at,
        }


@dataclass
class Message:
    id: str
    role: MessageRole
    text: str = ""
    status: str | None = None
    created_at: float | None = None
    raw: Any = field(default_factory=dict)

    def to_json(self, include_raw: bool = False) -> Json:
        data: Json = {
            "id": self.id,
            "role": self.role,
            "text": self.text,
            "status": self.status,
            "createdAt": self.created_at,
        }
        if include_raw:
            data["raw"] = self.raw
        elif self.role == "tool":
            data["raw"] = lightweight_tool_raw(self.raw)
        return data


@dataclass
class ThreadSummary:
    conversation_id: str
    title: str = "(untitled)"
    cwd: str = ""
    source: ThreadSource = "history-only"
    runtime_status: str = "unknown"
    latest_turn_status: str = "-"
    latest_item_preview: str = ""
    active_at: float | None = None
    updated_at: float | None = None
    has_live_owner: bool = False
    latest_model: str | None = None
    latest_reasoning_effort: str | None = None
    approval_policy: str | None = None
    sandbox_mode: str | None = None

    def to_json(self) -> Json:
        return {
            "conversationId": self.conversation_id,
            "title": self.title,
            "cwd": self.cwd,
            "source": self.source,
            "runtimeStatus": self.runtime_status,
            "latestTurnStatus": self.latest_turn_status,
            "latestItemPreview": self.latest_item_preview,
            "activeAt": self.active_at,
            "updatedAt": self.updated_at,
            "hasLiveOwner": self.has_live_owner,
            "latestModel": self.latest_model,
            "latestReasoningEffort": self.latest_reasoning_effort,
            "approvalPolicy": self.approval_policy,
            "sandboxMode": self.sandbox_mode,
        }


@dataclass
class ThreadDetail:
    summary: ThreadSummary
    messages: list[Message] = field(default_factory=list)
    raw_turns: list[Any] = field(default_factory=list)
    pagination: Json | None = None

    def to_json(self, include_raw: bool = False) -> Json:
        data: Json = {
            "summary": self.summary.to_json(),
            "messages": [message.to_json(include_raw=include_raw) for message in self.messages],
            "pagination": self.pagination,
        }
        if include_raw:
            data["rawTurns"] = self.raw_turns
        return data


@dataclass
class IpcSnapshot:
    conversation_id: str
    host_id: str | None = None
    state: Json | None = None
    change_type: str | None = None
    seen_at: float | None = None
    raw_params: Json | None = None
    last_patches: list[Json] | None = None


def lightweight_tool_raw(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    root = value.get("root")
    if isinstance(root, dict):
        return {**_pick_fields(value, ("id", "type", "kind", "status", "phase")), "root": lightweight_tool_raw(root)}

    item_type = value.get("type") or value.get("kind")
    if item_type == "fileChange":
        changes = value.get("changes") if isinstance(value.get("changes"), list) else []
        return {
            **_pick_fields(value, ("id", "type", "kind", "status", "phase")),
            "type": "fileChange",
            "changes": [_lightweight_file_change(change) for change in changes if isinstance(change, dict)],
        }
    return _truncate_nested(value)


def lightweight_patch_list(value: Any) -> list[Json]:
    if not isinstance(value, list):
        return []
    rows: list[Json] = []
    for patch in value[:100]:
        if not isinstance(patch, dict):
            continue
        row = _pick_fields(patch, ("op", "path"))
        if "value" in patch:
            row["value"] = _lightweight_patch_value(patch["value"])
        rows.append(row)
    if len(value) > 100:
        rows.append({"truncated": len(value) - 100})
    return rows


def _lightweight_file_change(change: dict[str, Any]) -> Json:
    diff = change.get("diff")
    diff_text = diff if isinstance(diff, str) else ""
    data: Json = {
        **_pick_fields(change, ("kind", "path", "oldPath", "newPath")),
        "diffSummary": _diff_summary(diff_text),
    }
    preview = _truncate_text(diff_text, 900)
    if preview:
        data["diffPreview"] = preview
    return data


def _lightweight_patch_value(value: Any) -> Any:
    if isinstance(value, dict):
        root = value.get("root")
        item = root if isinstance(root, dict) else value
        item_type = item.get("type") or item.get("kind")
        if item_type == "fileChange":
            return lightweight_tool_raw(value)
    return _truncate_nested(value, text_limit=600, sequence_limit=8)


def _truncate_nested(value: Any, *, text_limit: int = 1200, sequence_limit: int = 20, depth: int = 0) -> Any:
    if isinstance(value, str):
        return _truncate_text(value, text_limit)
    if depth >= 4:
        return _truncate_text(str(value), text_limit)
    if isinstance(value, list):
        rows = [_truncate_nested(item, text_limit=text_limit, sequence_limit=sequence_limit, depth=depth + 1) for item in value[:sequence_limit]]
        if len(value) > sequence_limit:
            rows.append({"truncated": len(value) - sequence_limit})
        return rows
    if isinstance(value, dict):
        return {
            str(key): _truncate_nested(item, text_limit=text_limit, sequence_limit=sequence_limit, depth=depth + 1)
            for key, item in value.items()
        }
    return value


def _pick_fields(value: dict[str, Any], keys: tuple[str, ...]) -> Json:
    return {key: value[key] for key in keys if key in value}


def _truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."


def _diff_summary(diff: str) -> Json:
    added = 0
    removed = 0
    chunks = 0
    for line in diff.splitlines():
        if line.startswith("@@"):
            chunks += 1
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    return {"added": added, "removed": removed, "chunks": chunks}

