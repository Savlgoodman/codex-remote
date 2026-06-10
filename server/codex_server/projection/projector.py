from __future__ import annotations

import time
from typing import Any

from ..models import MessageProjection, ThreadProjection, ThreadSettings, ThreadSummary, TurnProjection
from .normalizer import compact_text, find_text, item_role, latest_item, latest_turn, runtime_status, summarize_item, turn_status


def project_state(
    *,
    conversation_id: str,
    state: dict[str, Any],
    revision: int | None,
    owner_source_client_id: str | None,
    seen_at: float | None = None,
) -> ThreadProjection:
    seen_at = seen_at or time.time()
    settings = settings_from_state(state)
    latest = latest_turn(state)
    runtime = _runtime_literal(runtime_status(state))
    latest_status = _turn_status_literal(turn_status(latest))
    token_total = _token_total(state)
    active_at = _ms_to_seconds((latest or {}).get("turnStartedAtMs")) or _ms_to_seconds(state.get("updatedAt")) or seen_at
    updated_at = _ms_to_seconds(state.get("updatedAt")) or active_at
    summary = ThreadSummary(
        conversation_id=conversation_id,
        title=compact_text(state.get("title") or state.get("preview") or "(untitled)", 90),
        cwd=str(state.get("cwd") or settings_cwd(state) or ""),
        source="live",
        owner_source_client_id=owner_source_client_id,
        has_live_owner=True,
        runtime_status=runtime,
        latest_turn_status=latest_status,
        latest_model=str(state.get("latestModel") or settings.model or "") or None,
        latest_reasoning_effort=str(state.get("latestReasoningEffort") or settings.reasoning_effort or "") or None,
        approval_policy=settings.approval_policy,
        sandbox_type=settings.sandbox_type,
        latest_preview=summarize_item(latest_item(state)),
        updated_at=updated_at,
        active_at=active_at,
        token_total=token_total,
    )
    turns, messages = project_turns(conversation_id, state)
    return ThreadProjection(
        summary=summary,
        settings=settings,
        turns=turns,
        messages=messages,
        raw_revision=revision,
        rollout_path=state.get("rolloutPath") if isinstance(state.get("rolloutPath"), str) else None,
    )


def settings_from_state(state: dict[str, Any]) -> ThreadSettings:
    latest_settings = state.get("latestThreadSettings") if isinstance(state.get("latestThreadSettings"), dict) else {}
    collab = state.get("latestCollaborationMode") if isinstance(state.get("latestCollaborationMode"), dict) else {}
    collab_settings = collab.get("settings") if isinstance(collab.get("settings"), dict) else {}
    current_permissions = state.get("currentPermissions") if isinstance(state.get("currentPermissions"), dict) else {}
    sandbox = latest_settings.get("sandboxPolicy") if isinstance(latest_settings.get("sandboxPolicy"), dict) else None
    if sandbox is None:
        sandbox = current_permissions.get("sandboxPolicy") if isinstance(current_permissions.get("sandboxPolicy"), dict) else None
    return ThreadSettings(
        model=_string_or_none(latest_settings.get("model") or state.get("latestModel") or collab_settings.get("model")),
        reasoning_effort=_string_or_none(
            latest_settings.get("effort")
            or latest_settings.get("reasoning_effort")
            or state.get("latestReasoningEffort")
            or collab_settings.get("reasoning_effort")
        ),
        approval_policy=_string_or_none(latest_settings.get("approvalPolicy") or current_permissions.get("approvalPolicy")),
        approvals_reviewer=_string_or_none(latest_settings.get("approvalsReviewer") or current_permissions.get("approvalsReviewer")),
        sandbox_type=_string_or_none(sandbox.get("type") if isinstance(sandbox, dict) else None),
        service_tier=_string_or_none(latest_settings.get("serviceTier")),
        permissions=_string_or_none(latest_settings.get("permissions")),
    )


def settings_cwd(state: dict[str, Any]) -> str | None:
    latest_settings = state.get("latestThreadSettings") if isinstance(state.get("latestThreadSettings"), dict) else {}
    cwd = latest_settings.get("cwd")
    return cwd if isinstance(cwd, str) else None


def project_turns(conversation_id: str, state: dict[str, Any]) -> tuple[list[TurnProjection], list[MessageProjection]]:
    turns_value = state.get("turns")
    if not isinstance(turns_value, list):
        return [], []
    turns: list[TurnProjection] = []
    messages: list[MessageProjection] = []
    ordinal = 0
    for turn_index, turn in enumerate(turns_value):
        if not isinstance(turn, dict):
            continue
        turn_id = _string_or_none(turn.get("turnId")) or f"turn-{turn_index}"
        status = _turn_status_literal(turn_status(turn))
        started = _ms_to_seconds(turn.get("turnStartedAtMs"))
        turns.append(
            TurnProjection(
                id=turn_id,
                index=turn_index,
                status=status,
                started_at=started,
                duration_ms=turn.get("durationMs") if isinstance(turn.get("durationMs"), int) else None,
            )
        )
        items = turn.get("items")
        if not isinstance(items, list):
            continue
        for item_index, raw_item in enumerate(items):
            item = raw_item.get("root", raw_item) if isinstance(raw_item, dict) else raw_item
            if not isinstance(item, dict):
                continue
            role = item_role(item)
            message_id = _string_or_none(item.get("id")) or f"{turn_id}-item-{item_index}"
            text = find_text(item.get("text") if "text" in item else item.get("content") or item)
            messages.append(
                MessageProjection(
                    id=message_id,
                    conversation_id=conversation_id,
                    turn_id=turn_id,
                    role=role,  # type: ignore[arg-type]
                    phase=_string_or_none(item.get("phase")),
                    text=text,
                    status=status,
                    created_at=started,
                    updated_at=_message_updated_at(turn, started),
                    ordinal=ordinal,
                )
            )
            ordinal += 1
    return turns, messages


def _token_total(state: dict[str, Any]) -> int | None:
    usage = state.get("latestTokenUsageInfo")
    if not isinstance(usage, dict):
        return None
    total = usage.get("total")
    if isinstance(total, dict) and isinstance(total.get("totalTokens"), int):
        return total["totalTokens"]
    return None


def _ms_to_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)) and value > 0:
        return float(value) / 1000.0
    return None


def _message_updated_at(turn: dict[str, Any], fallback: float | None) -> float | None:
    return (
        _ms_to_seconds(turn.get("finalAssistantStartedAtMs"))
        or _ms_to_seconds(turn.get("turnStartedAtMs"))
        or fallback
    )


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text or None


def _runtime_literal(value: str) -> str:
    return value if value in {"idle", "active"} else "unknown"


def _turn_status_literal(value: str) -> str:
    return value if value in {"inProgress", "completed", "interrupted", "failed", "-"} else "unknown"
