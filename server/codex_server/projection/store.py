from __future__ import annotations

import threading
import time
from dataclasses import replace
from typing import Any

from ..events.bus import EventBus
from ..ipc.patcher import apply_patch_list
from ..models import IpcConnectionStatus, ServerEvent, ThreadProjection, ThreadSummary
from .projector import project_state


class ProjectionStore:
    def __init__(self, event_bus: EventBus):
        self.event_bus = event_bus
        self.ipc_status = IpcConnectionStatus()
        self._raw_states: dict[str, dict[str, Any]] = {}
        self._projections: dict[str, ThreadProjection] = {}
        self._lock = threading.RLock()

    def list_threads(self) -> list[ThreadSummary]:
        with self._lock:
            rows = [projection.summary for projection in self._projections.values()]
        return sorted(rows, key=lambda item: item.active_at or item.updated_at or 0, reverse=True)

    def get_projection(self, conversation_id: str) -> ThreadProjection | None:
        with self._lock:
            return self._projections.get(conversation_id)

    def set_ipc_status(self, status: IpcConnectionStatus) -> None:
        stale_events: list[ServerEvent] = []
        with self._lock:
            was_online = self.ipc_status.online
            self.ipc_status = status
            if was_online and not status.online:
                for conversation_id, projection in list(self._projections.items()):
                    if projection.summary.source == "live":
                        summary = replace(projection.summary, source="stale", has_live_owner=False)
                        self._projections[conversation_id] = replace(projection, summary=summary)
                        stale_events.append(ServerEvent("thread.upsert", {"summary": summary.to_json()}, conversation_id))
        self.event_bus.publish_threadsafe(ServerEvent("status.changed", {"ipc": status.to_json()}))
        for event in stale_events:
            self.event_bus.publish_threadsafe(event)

    def handle_ipc_message(self, message: dict[str, Any]) -> None:
        if message.get("type") != "broadcast":
            return
        method = message.get("method")
        if method == "thread-read-state-changed":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            conversation_id = str(params.get("conversationId") or "")
            if conversation_id:
                self.event_bus.publish_threadsafe(ServerEvent("thread.read", dict(params), conversation_id))
            return
        if method != "thread-stream-state-changed":
            return
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        conversation_id = str(params.get("conversationId") or "")
        change = params.get("change") if isinstance(params.get("change"), dict) else None
        if not conversation_id or not isinstance(change, dict):
            return
        source_client_id = message.get("sourceClientId") if isinstance(message.get("sourceClientId"), str) else None
        events = self._apply_change(conversation_id, change, source_client_id)
        for event in events:
            self.event_bus.publish_threadsafe(event)

    def _apply_change(self, conversation_id: str, change: dict[str, Any], source_client_id: str | None) -> list[ServerEvent]:
        with self._lock:
            old_projection = self._projections.get(conversation_id)
            old_state = self._raw_states.get(conversation_id)
            change_type = change.get("type")
            if change_type == "snapshot" and isinstance(change.get("conversationState"), dict):
                next_state = change["conversationState"]
            elif change_type == "patches":
                next_state = apply_patch_list(old_state, change.get("patches"))
                if next_state is None:
                    return [
                        ServerEvent(
                            "resync.required",
                            {"reason": "missing_state_for_patch", "changeType": change_type},
                            conversation_id,
                        )
                    ]
            else:
                return []
            revision = change.get("revision") if isinstance(change.get("revision"), int) else None
            self._raw_states[conversation_id] = next_state
            next_projection = project_state(
                conversation_id=conversation_id,
                state=next_state,
                revision=revision,
                owner_source_client_id=source_client_id,
                seen_at=time.time(),
            )
            self._projections[conversation_id] = next_projection
        return projection_events(old_projection, next_projection)


def projection_events(old: ThreadProjection | None, new: ThreadProjection) -> list[ServerEvent]:
    events: list[ServerEvent] = []
    conversation_id = new.summary.conversation_id
    if old is None:
        events.append(ServerEvent("thread.upsert", {"summary": new.summary.to_json()}, conversation_id))
        return events

    if old.summary.to_json() != new.summary.to_json():
        events.append(ServerEvent("thread.upsert", {"summary": new.summary.to_json()}, conversation_id))

    if old.settings.to_json() != new.settings.to_json():
        payload = new.settings.to_json()
        payload["previousModel"] = old.settings.model if old.settings.model != new.settings.model else None
        events.append(ServerEvent("settings.changed", payload, conversation_id))

    old_turns = {turn.index: turn for turn in old.turns}
    for turn in new.turns:
        previous = old_turns.get(turn.index)
        if previous is None and turn.status == "inProgress":
            events.append(ServerEvent("turn.started", {"turn": turn.to_json()}, conversation_id))
        elif previous is not None and previous.status != turn.status and turn.status in {"completed", "interrupted", "failed"}:
            events.append(ServerEvent("turn.finished", {"turn": turn.to_json()}, conversation_id))

    old_messages = {message.id: message for message in old.messages}
    for message in new.messages:
        previous = old_messages.get(message.id)
        if previous is None:
            events.append(ServerEvent("message.upsert", {"message": message.to_json()}, conversation_id))
            continue
        if previous.text != message.text:
            if message.text.startswith(previous.text):
                events.append(
                    ServerEvent(
                        "message.append",
                        {"messageId": message.id, "delta": message.text[len(previous.text) :]},
                        conversation_id,
                    )
                )
            else:
                events.append(ServerEvent("message.replace", {"messageId": message.id, "text": message.text}, conversation_id))
            continue
        changes = message_metadata_changes(previous.to_json(), message.to_json())
        if changes:
            events.append(ServerEvent("message.patch", {"messageId": message.id, "changes": changes}, conversation_id))

    if old.summary.token_total != new.summary.token_total:
        events.append(ServerEvent("token.changed", {"totalTokens": new.summary.token_total}, conversation_id))
    return events


def message_metadata_changes(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    changes: dict[str, Any] = {}
    for key in ("turnId", "role", "phase", "status", "createdAt", "ordinal"):
        if previous.get(key) != current.get(key):
            changes[key] = current.get(key)
    if changes and previous.get("updatedAt") != current.get("updatedAt"):
        changes["updatedAt"] = current.get("updatedAt")
    return changes
