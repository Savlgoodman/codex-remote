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

