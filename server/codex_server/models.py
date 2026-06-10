from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal


ThreadSource = Literal["live", "stale", "history-only"]
RuntimeStatus = Literal["idle", "active", "unknown"]
TurnStatus = Literal["inProgress", "completed", "interrupted", "failed", "unknown", "-"]
MessageRole = Literal["user", "assistant", "reasoning", "tool", "command", "system"]


@dataclass
class IpcConnectionStatus:
    online: bool = False
    client_id: str | None = None
    connected_at: float | None = None
    last_seen_at: float | None = None
    last_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SdkStatus:
    available: bool = False
    last_refresh_at: float | None = None
    last_error: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "lastRefreshAt": self.last_refresh_at,
            "lastError": self.last_error,
        }


@dataclass
class ThreadSettings:
    model: str | None = None
    reasoning_effort: str | None = None
    approval_policy: str | None = None
    approvals_reviewer: str | None = None
    sandbox_type: str | None = None
    service_tier: str | None = None
    permissions: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "reasoningEffort": self.reasoning_effort,
            "approvalPolicy": self.approval_policy,
            "approvalsReviewer": self.approvals_reviewer,
            "sandboxType": self.sandbox_type,
            "serviceTier": self.service_tier,
            "permissions": self.permissions,
        }


@dataclass
class ThreadSummary:
    conversation_id: str
    title: str | None = None
    cwd: str | None = None
    source: ThreadSource = "history-only"
    owner_source_client_id: str | None = None
    has_live_owner: bool = False
    runtime_status: RuntimeStatus = "unknown"
    latest_turn_status: TurnStatus = "unknown"
    latest_model: str | None = None
    latest_reasoning_effort: str | None = None
    approval_policy: str | None = None
    sandbox_type: str | None = None
    latest_preview: str | None = None
    updated_at: float | None = None
    active_at: float | None = None
    token_total: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "conversationId": self.conversation_id,
            "title": self.title,
            "cwd": self.cwd,
            "source": self.source,
            "ownerSourceClientId": self.owner_source_client_id,
            "hasLiveOwner": self.has_live_owner,
            "runtimeStatus": self.runtime_status,
            "latestTurnStatus": self.latest_turn_status,
            "latestModel": self.latest_model,
            "latestReasoningEffort": self.latest_reasoning_effort,
            "approvalPolicy": self.approval_policy,
            "sandboxType": self.sandbox_type,
            "latestPreview": self.latest_preview,
            "updatedAt": self.updated_at,
            "activeAt": self.active_at,
            "tokenTotal": self.token_total,
        }


@dataclass
class MessageProjection:
    id: str
    conversation_id: str
    turn_id: str | None
    role: MessageRole
    phase: str | None = None
    text: str = ""
    status: str | None = None
    created_at: float | None = None
    updated_at: float | None = None
    ordinal: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversationId": self.conversation_id,
            "turnId": self.turn_id,
            "role": self.role,
            "phase": self.phase,
            "text": self.text,
            "status": self.status,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
            "ordinal": self.ordinal,
        }


@dataclass
class TurnProjection:
    id: str | None
    index: int
    status: TurnStatus
    started_at: float | None = None
    duration_ms: int | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "index": self.index,
            "status": self.status,
            "startedAt": self.started_at,
            "durationMs": self.duration_ms,
        }


@dataclass
class ThreadProjection:
    summary: ThreadSummary
    settings: ThreadSettings
    turns: list[TurnProjection] = field(default_factory=list)
    messages: list[MessageProjection] = field(default_factory=list)
    raw_revision: int | None = None
    rollout_path: str | None = None

    def to_detail_json(self) -> dict[str, Any]:
        return {
            "summary": self.summary.to_json(),
            "settings": self.settings.to_json(),
            "turns": [turn.to_json() for turn in self.turns],
            "messages": [message.to_json() for message in self.messages],
            "rawRevision": self.raw_revision,
            "rolloutPath": self.rollout_path,
        }


@dataclass
class ServerEvent:
    type: str
    payload: dict[str, Any]
    conversation_id: str | None = None
    event_id: int | None = None
    timestamp: float = field(default_factory=time.time)

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "eventId": self.event_id,
            "time": self.timestamp,
            "conversationId": self.conversation_id,
            "payload": self.payload,
        }

