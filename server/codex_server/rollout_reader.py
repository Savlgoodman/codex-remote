from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from .models import ThreadDetail
from .normalizer import compact_text, detail_from_turns, model_settings_from_state, permission_settings_from_state, summary_from_sdk_thread


class RolloutReader:
    async def read_thread(self, conversation_id: str, rollout_path: str | None = None) -> ThreadDetail | None:
        return await asyncio.to_thread(self._read_thread_sync, conversation_id, rollout_path)

    def _read_thread_sync(self, conversation_id: str, rollout_path: str | None = None) -> ThreadDetail | None:
        path = _resolve_rollout_path(conversation_id, rollout_path)
        if path is None:
            return None
        meta: dict[str, Any] = {}
        turn_contexts: list[dict[str, Any]] = []
        latest_state: dict[str, Any] | None = None
        latest_timestamp: float | None = None
        try:
            with path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    row = _load_json_line(line)
                    if row is None:
                        continue
                    latest_timestamp = _timestamp_seconds(row.get("timestamp")) or latest_timestamp
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                    if row.get("type") == "session_meta" and isinstance(payload, dict):
                        meta = payload
                    elif row.get("type") == "turn_context" and isinstance(payload, dict):
                        turn_contexts.append(payload)
                    state = _conversation_state_from_payload(payload)
                    if state is not None:
                        latest_state = state
        except OSError:
            return None

        state = _state_from_rollout(conversation_id, meta, turn_contexts, latest_state)
        summary = summary_from_sdk_thread(
            {
                "id": conversation_id,
                "name": meta.get("title") or meta.get("name"),
                "preview": meta.get("title") or meta.get("name"),
                "cwd": state.get("cwd") or meta.get("cwd"),
                "source": meta.get("source"),
                "updatedAt": latest_timestamp,
                "status": {"type": "idle"},
                **state,
            }
        )
        if not summary.title or summary.title == "(untitled)":
            summary.title = compact_text(meta.get("id") or conversation_id, 90)
        turns = state.get("turns") if isinstance(state.get("turns"), list) else []
        return detail_from_turns(summary, turns, None)


def enrich_detail_from_rollout(detail: ThreadDetail, rollout_path: str | None = None) -> ThreadDetail:
    path = _resolve_rollout_path(detail.summary.conversation_id, rollout_path)
    if path is None:
        return detail
    meta: dict[str, Any] = {}
    turn_contexts: list[dict[str, Any]] = []
    latest_state: dict[str, Any] | None = None
    latest_timestamp: float | None = None
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                row = _load_json_line(line)
                if row is None:
                    continue
                latest_timestamp = _timestamp_seconds(row.get("timestamp")) or latest_timestamp
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                if row.get("type") == "session_meta" and isinstance(payload, dict):
                    meta = payload
                elif row.get("type") == "turn_context" and isinstance(payload, dict):
                    turn_contexts.append(payload)
                state = _conversation_state_from_payload(payload)
                if state is not None:
                    latest_state = state
    except OSError:
        return detail
    state = _state_from_rollout(detail.summary.conversation_id, meta, turn_contexts, latest_state)
    latest_model, latest_reasoning_effort = model_settings_from_state(state)
    approval_policy, sandbox_mode = permission_settings_from_state(state)
    detail.summary.latest_model = detail.summary.latest_model or latest_model
    detail.summary.latest_reasoning_effort = detail.summary.latest_reasoning_effort or latest_reasoning_effort
    detail.summary.approval_policy = detail.summary.approval_policy or approval_policy
    detail.summary.sandbox_mode = detail.summary.sandbox_mode or sandbox_mode
    detail.summary.cwd = detail.summary.cwd or str(state.get("cwd") or meta.get("cwd") or "")
    detail.summary.updated_at = max(detail.summary.updated_at or 0, latest_timestamp or 0) or detail.summary.updated_at
    detail.summary.active_at = max(detail.summary.active_at or 0, latest_timestamp or 0) or detail.summary.active_at
    return detail


def _resolve_rollout_path(conversation_id: str, rollout_path: str | None) -> Path | None:
    if rollout_path:
        path = Path(os.path.normpath(rollout_path.replace("\\\\?\\", "")))
        if path.exists():
            return path
    sessions = Path.home() / ".codex" / "sessions"
    if not sessions.exists():
        return None
    pattern = f"rollout-*{conversation_id}.jsonl"
    matches = sorted(sessions.rglob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    return matches[0] if matches else None


def _state_from_rollout(
    conversation_id: str,
    meta: dict[str, Any],
    turn_contexts: list[dict[str, Any]],
    latest_state: dict[str, Any] | None,
) -> dict[str, Any]:
    state = dict(latest_state or {})
    state["id"] = state.get("id") or conversation_id
    state["cwd"] = state.get("cwd") or meta.get("cwd")
    if turn_contexts:
        latest = turn_contexts[-1]
        state["latestModel"] = state.get("latestModel") or latest.get("model")
        state["latestReasoningEffort"] = state.get("latestReasoningEffort") or latest.get("reasoning_effort") or latest.get("effort")
        state["latestCollaborationMode"] = state.get("latestCollaborationMode") or latest.get("collaboration_mode")
        state["currentPermissions"] = state.get("currentPermissions") or {
            "approvalPolicy": latest.get("approval_policy"),
            "sandboxPolicy": latest.get("sandbox_policy"),
        }
        state["turns"] = state.get("turns") or [_turn_from_context(context, index) for index, context in enumerate(turn_contexts)]
    return state


def _turn_from_context(context: dict[str, Any], index: int) -> dict[str, Any]:
    params = {
        "threadId": context.get("thread_id"),
        "model": context.get("model"),
        "effort": context.get("effort"),
        "reasoningEffort": context.get("reasoning_effort") or context.get("effort"),
        "approvalPolicy": context.get("approval_policy"),
        "sandboxPolicy": context.get("sandbox_policy"),
        "cwd": context.get("cwd"),
        "collaborationMode": context.get("collaboration_mode"),
    }
    return {
        "turnId": context.get("turn_id") or index,
        "status": "completed",
        "params": {key: value for key, value in params.items() if value is not None},
        "items": [],
    }


def _conversation_state_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    change = payload.get("change") if isinstance(payload.get("change"), dict) else None
    if isinstance(change, dict) and isinstance(change.get("conversationState"), dict):
        return change["conversationState"]
    if isinstance(payload.get("conversationState"), dict):
        return payload["conversationState"]
    if isinstance(payload.get("thread"), dict):
        return payload["thread"]
    return None


def _load_json_line(line: str) -> dict[str, Any] | None:
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    return row if isinstance(row, dict) else None


def _timestamp_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) / 1000 if value > 10_000_000_000 else float(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        from datetime import datetime

        return datetime.fromisoformat(text).timestamp()
    except ValueError:
        return None

