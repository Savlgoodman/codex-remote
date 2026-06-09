from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from .models import IpcSnapshot, IpcStatus, Message, ThreadDetail, ThreadSummary
from .normalizer import apply_patch_list, detail_from_turns, latest_turn, summary_from_ipc_state, summary_from_ipc_summary


SNAPSHOT_MIN_INTERVAL_SECONDS = 30.0
IPC_RAW_PREVIEW_LIMIT = 2000


class StateStore:
    def __init__(self):
        self._lock = threading.RLock()
        self.ipc_status = IpcStatus()
        self._summaries: dict[str, ThreadSummary] = {}
        self._details: dict[str, ThreadDetail] = {}
        self._snapshots: dict[str, IpcSnapshot] = {}
        self._message_signatures: dict[str, dict[str, tuple[str | None, int, str]]] = {}
        self._last_snapshot_published_at: dict[str, float] = {}
        self._ipc_monitor_capturing = False
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=200)
        with self._lock:
            self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._lock:
            self._subscribers.discard(queue)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            queues = list(self._subscribers)
        for queue in queues:
            def put(q: asyncio.Queue[dict[str, Any]] = queue) -> None:
                if q.full():
                    try:
                        q.get_nowait()
                    except asyncio.QueueEmpty:
                        pass
                q.put_nowait(event)

            if self._loop is not None and self._loop.is_running():
                self._loop.call_soon_threadsafe(put)

    def set_ipc_status(self, status: IpcStatus) -> None:
        with self._lock:
            was_online = self.ipc_status.online
            self.ipc_status = status
            stale_summaries: list[dict[str, Any]] = []
            if was_online and not status.online:
                for summary in self._summaries.values():
                    if summary.source == "live":
                        summary.source = "stale"
                        summary.has_live_owner = False
                        stale_summaries.append(summary.to_json())
        self.publish({"type": "ipc.status", "version": 1, **status.to_json()})
        for summary in stale_summaries:
            self.publish({"type": "thread.summary", "version": 1, "conversationId": summary["conversationId"], "summary": summary})

    def handle_ipc_raw_event(self, direction: str, message: dict[str, Any]) -> None:
        with self._lock:
            capturing = self._ipc_monitor_capturing
        if not capturing:
            return
        ipc_type = str(message.get("type") or "unknown")
        method = message.get("method")
        conversation_id = _conversation_id_from_ipc_message(message)
        payload_text = _safe_json_dumps(message)
        self.publish(
            {
                "type": "ipc.raw",
                "version": 1,
                "direction": direction,
                "timestamp": time.time(),
                "size": len(payload_text.encode("utf-8")),
                "ipcType": ipc_type,
                "method": str(method) if method is not None else None,
                "requestId": str(message.get("requestId")) if message.get("requestId") is not None else None,
                "conversationId": conversation_id,
                "summary": _ipc_raw_summary(message),
                "payload": message,
                "payloadPreview": _truncate(payload_text, IPC_RAW_PREVIEW_LIMIT),
                "payloadTruncated": len(payload_text) > IPC_RAW_PREVIEW_LIMIT,
            }
        )

    def set_ipc_monitor_capturing(self, capturing: bool) -> dict[str, Any]:
        with self._lock:
            self._ipc_monitor_capturing = capturing
        event = {"type": "ipc.monitor.status", "version": 1, "capturing": capturing}
        self.publish(event)
        return event

    def ipc_monitor_status(self) -> dict[str, Any]:
        with self._lock:
            capturing = self._ipc_monitor_capturing
        return {"type": "ipc.monitor.status", "version": 1, "capturing": capturing}

    def handle_ipc_message(self, message: dict[str, Any]) -> None:
        if self._handle_thread_summary_message(message):
            return
        if message.get("type") != "broadcast":
            return
        method = message.get("method")
        params = message.get("params")
        if method != "thread-stream-state-changed" or not isinstance(params, dict):
            return
        conversation_id = str(params.get("conversationId") or "")
        if not conversation_id:
            return
        change = params.get("change") if isinstance(params.get("change"), dict) else {}
        change_type = change.get("type") if isinstance(change, dict) else None
        state = change.get("conversationState") if isinstance(change, dict) else None
        patches = change.get("patches") if isinstance(change, dict) else None
        now = time.time()
        with self._lock:
            previous = self._snapshots.get(conversation_id)
            if isinstance(state, dict):
                next_state = state
            elif change_type == "patches":
                next_state = apply_patch_list(previous.state if previous else None, patches)
            else:
                next_state = previous.state if previous else None
            snapshot = IpcSnapshot(
                conversation_id=conversation_id,
                host_id=params.get("hostId") or (previous.host_id if previous else None),
                state=next_state,
                change_type=change_type,
                seen_at=now,
                raw_params=params,
                last_patches=patches if isinstance(patches, list) else None,
            )
            self._snapshots[conversation_id] = snapshot
            summary = summary_from_ipc_state(conversation_id, next_state, seen_at=now)
            self._summaries[conversation_id] = self._merge_summary(self._summaries.get(conversation_id), summary)
            if isinstance(next_state, dict):
                turns = next_state.get("turns")
                if isinstance(turns, list):
                    detail = detail_from_turns(self._summaries[conversation_id], turns, next_state.get("turnsPagination"))
                    self._details[conversation_id] = self._merge_detail(self._details.get(conversation_id), detail)
            event_summary = self._summaries[conversation_id].to_json()
            event_detail = self._details.get(conversation_id)
            message_upserts: list[dict[str, Any]] = []
            snapshot_messages = None
            if event_detail is not None:
                previous_signatures = self._message_signatures.get(conversation_id, {})
                next_signatures = _message_signatures_by_id(event_detail.messages)
                self._message_signatures[conversation_id] = next_signatures
                changed_ids = [
                    message.id
                    for message in event_detail.messages
                    if next_signatures.get(message.id) != previous_signatures.get(message.id)
                ]
                message_upserts = [message.to_json() for message in event_detail.messages if message.id in changed_ids]
                last_snapshot_at = self._last_snapshot_published_at.get(conversation_id, 0)
                should_publish_snapshot = (
                    (previous_signatures == {} and bool(event_detail.messages))
                    or len(changed_ids) > 8
                    or now - last_snapshot_at >= SNAPSHOT_MIN_INTERVAL_SECONDS
                )
                if should_publish_snapshot:
                    self._last_snapshot_published_at[conversation_id] = now
                    snapshot_messages = [message.to_json() for message in event_detail.messages]
        self.publish({"type": "thread.summary", "version": 1, "conversationId": conversation_id, "summary": event_summary})
        for message in message_upserts:
            self.publish({"type": "thread.message.upsert", "version": 1, "conversationId": conversation_id, "message": message})
        if snapshot_messages is not None:
            self.publish(
                {
                    "type": "thread.snapshot",
                    "version": 1,
                    "reason": "initial_or_periodic",
                    "conversationId": conversation_id,
                    "summary": event_summary,
                    "messages": snapshot_messages,
                }
            )

    def _handle_thread_summary_message(self, message: dict[str, Any]) -> bool:
        payload = _thread_summary_payload(message)
        if payload is None:
            return False
        summary = summary_from_ipc_summary(payload["summary"], payload["conversation_id"])
        if not summary.conversation_id:
            return True
        with self._lock:
            self._summaries[summary.conversation_id] = self._merge_summary(self._summaries.get(summary.conversation_id), summary)
            detail = self._details.get(summary.conversation_id)
            if detail is not None:
                detail.summary = self._summaries[summary.conversation_id]
            event_summary = self._summaries[summary.conversation_id].to_json()
        self.publish({"type": "thread.summary", "version": 1, "conversationId": summary.conversation_id, "summary": event_summary})
        return True

    def upsert_history_summary(self, summary: ThreadSummary) -> None:
        if not summary.conversation_id:
            return
        with self._lock:
            existing = self._summaries.get(summary.conversation_id)
            self._summaries[summary.conversation_id] = self._merge_summary(existing, summary)

    def upsert_detail(self, detail: ThreadDetail) -> None:
        conversation_id = detail.summary.conversation_id
        if not conversation_id:
            return
        with self._lock:
            existing_summary = self._summaries.get(conversation_id)
            self._summaries[conversation_id] = self._merge_summary(existing_summary, detail.summary)
            detail.summary = self._summaries[conversation_id]
            self._details[conversation_id] = self._merge_detail(self._details.get(conversation_id), detail)
            self._message_signatures[conversation_id] = _message_signatures_by_id(self._details[conversation_id].messages)

    def list_threads(self) -> list[ThreadSummary]:
        with self._lock:
            rows = list(self._summaries.values())
        return sorted(rows, key=lambda item: item.active_at or item.updated_at or 0, reverse=True)

    def get_summary(self, conversation_id: str) -> ThreadSummary | None:
        with self._lock:
            return self._summaries.get(conversation_id)

    def get_detail(self, conversation_id: str) -> ThreadDetail | None:
        with self._lock:
            return self._details.get(conversation_id)

    def get_snapshot(self, conversation_id: str) -> IpcSnapshot | None:
        with self._lock:
            return self._snapshots.get(conversation_id)

    def update_summary_settings(self, conversation_id: str, updates: dict[str, str | None]) -> ThreadSummary:
        now = time.time()
        with self._lock:
            summary = self._summaries.get(conversation_id)
            if summary is None:
                summary = ThreadSummary(conversation_id=conversation_id, updated_at=now)
                self._summaries[conversation_id] = summary
            if "model" in updates:
                summary.latest_model = updates["model"]
            if "reasoningEffort" in updates:
                summary.latest_reasoning_effort = updates["reasoningEffort"]
            if "approvalPolicy" in updates:
                summary.approval_policy = updates["approvalPolicy"]
            if "sandboxMode" in updates:
                summary.sandbox_mode = updates["sandboxMode"]
            summary.updated_at = now
            detail = self._details.get(conversation_id)
            if detail is not None:
                detail.summary = summary
            snapshot = self._snapshots.get(conversation_id)
            if snapshot is not None and isinstance(snapshot.state, dict):
                _apply_settings_to_state(snapshot.state, updates)
            event_summary = summary.to_json()
        self.publish({"type": "thread.summary", "version": 1, "conversationId": conversation_id, "summary": event_summary})
        return summary

    def _merge_summary(self, existing: ThreadSummary | None, incoming: ThreadSummary) -> ThreadSummary:
        if existing is None:
            return incoming
        if incoming.source == "live":
            incoming.active_at = incoming.active_at or existing.active_at
            return incoming
        if existing.source == "live" and existing.has_live_owner:
            existing.title = existing.title if existing.title != "(untitled)" else incoming.title
            existing.cwd = existing.cwd or incoming.cwd
            existing.active_at = max(existing.active_at or 0, incoming.active_at or 0) or None
            existing.updated_at = max(existing.updated_at or 0, incoming.updated_at or 0) or None
            if not existing.latest_item_preview:
                existing.latest_item_preview = incoming.latest_item_preview
            self._merge_settings(existing, incoming)
            return existing
        if existing.source == "stale":
            existing.title = existing.title if existing.title != "(untitled)" else incoming.title
            existing.cwd = existing.cwd or incoming.cwd
            existing.runtime_status = incoming.runtime_status
            existing.latest_turn_status = incoming.latest_turn_status
            existing.latest_item_preview = incoming.latest_item_preview or existing.latest_item_preview
            existing.active_at = max(existing.active_at or 0, incoming.active_at or 0) or None
            existing.updated_at = max(existing.updated_at or 0, incoming.updated_at or 0) or None
            existing.has_live_owner = False
            self._merge_settings(existing, incoming)
            return existing
        if existing.source == "history-only":
            if (incoming.active_at or 0) >= (existing.active_at or 0):
                self._merge_settings(incoming, existing)
                return incoming
            self._merge_settings(existing, incoming)
            return existing
        return incoming if (incoming.active_at or 0) >= (existing.active_at or 0) else existing

    def _merge_settings(self, existing: ThreadSummary, incoming: ThreadSummary) -> None:
        existing.latest_model = incoming.latest_model or existing.latest_model
        existing.latest_reasoning_effort = incoming.latest_reasoning_effort or existing.latest_reasoning_effort
        existing.approval_policy = incoming.approval_policy or existing.approval_policy
        existing.sandbox_mode = incoming.sandbox_mode or existing.sandbox_mode

    def _merge_detail(self, existing: ThreadDetail | None, incoming: ThreadDetail) -> ThreadDetail:
        if existing is None:
            return incoming
        if len(incoming.messages) >= len(existing.messages):
            return incoming
        merged = ThreadDetail(summary=incoming.summary, messages=list(existing.messages), raw_turns=existing.raw_turns, pagination=existing.pagination)
        return merged

    def messages_for(self, conversation_id: str) -> list[Message]:
        detail = self.get_detail(conversation_id)
        return detail.messages if detail is not None else []


def _message_signatures_by_id(messages: list[Message]) -> dict[str, tuple[str | None, int, str]]:
    return {message.id: (message.status, len(message.text), message.text[-80:]) for message in messages}


def _conversation_id_from_ipc_message(message: dict[str, Any]) -> str | None:
    for container in (message, message.get("params"), message.get("summary"), message.get("result")):
        if isinstance(container, dict):
            value = container.get("conversationId") or container.get("conversation_id")
            if value:
                return str(value)
    params = message.get("params")
    if isinstance(params, dict):
        turn_start_params = params.get("turnStartParams")
        if isinstance(turn_start_params, dict):
            value = turn_start_params.get("threadId") or turn_start_params.get("conversationId")
            if value:
                return str(value)
    return None


def _ipc_raw_summary(message: dict[str, Any]) -> str:
    message_type = str(message.get("type") or "unknown")
    method = message.get("method")
    if message_type == "broadcast":
        return f"broadcast {method or '-'}"
    if message_type == "request":
        return f"request {method or '-'}"
    if message_type == "response":
        result_type = message.get("resultType") or "ok"
        return f"response {method or '-'} {result_type}"
    if message_type == "thread.summary":
        summary = message.get("summary") if isinstance(message.get("summary"), dict) else {}
        model = summary.get("latestModel")
        effort = summary.get("latestReasoningEffort")
        return " ".join(str(part) for part in ("thread.summary", model, effort) if part)
    if message_type == "client-discovery-request":
        return "client discovery request"
    if message_type == "client-discovery-response":
        return "client discovery response"
    return message_type


def _safe_json_dumps(value: Any) -> str:
    try:
        return json_dumps(value)
    except Exception:
        return str(value)


def json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."


def _thread_summary_payload(message: dict[str, Any]) -> dict[str, Any] | None:
    if message.get("type") == "thread.summary":
        summary = message.get("summary")
        if isinstance(summary, dict):
            return {"conversation_id": str(message.get("conversationId") or ""), "summary": summary}
        return None
    if message.get("type") != "broadcast" or message.get("method") not in {"thread.summary", "thread-summary"}:
        return None
    params = message.get("params")
    if not isinstance(params, dict):
        return None
    summary = params.get("summary")
    if isinstance(summary, dict):
        return {"conversation_id": str(params.get("conversationId") or ""), "summary": summary}
    if any(key in params for key in ("latestModel", "latestReasoningEffort", "approvalPolicy", "sandboxMode")):
        return {"conversation_id": str(params.get("conversationId") or ""), "summary": params}
    return None


def _apply_settings_to_state(state: dict[str, Any], updates: dict[str, str | None]) -> None:
    thread_settings = state.get("latestThreadSettings")
    if not isinstance(thread_settings, dict):
        thread_settings = {}
        state["latestThreadSettings"] = thread_settings
    collaboration_mode = state.get("latestCollaborationMode")
    collaboration_settings = collaboration_mode.get("settings") if isinstance(collaboration_mode, dict) else None
    if not isinstance(collaboration_settings, dict):
        collaboration_settings = None
    if "model" in updates:
        _set_or_remove(state, "latestModel", updates["model"])
        _set_or_remove(thread_settings, "model", updates["model"])
        if collaboration_settings is not None:
            _set_or_remove(collaboration_settings, "model", updates["model"])
    if "reasoningEffort" in updates:
        _set_or_remove(state, "latestReasoningEffort", updates["reasoningEffort"])
        _set_or_remove(thread_settings, "effort", updates["reasoningEffort"])
        if collaboration_settings is not None:
            _set_or_remove(collaboration_settings, "reasoning_effort", updates["reasoningEffort"])
            _set_or_remove(collaboration_settings, "reasoningEffort", updates["reasoningEffort"])
    permissions = state.get("currentPermissions")
    if not isinstance(permissions, dict):
        permissions = {}
        state["currentPermissions"] = permissions
    if "approvalPolicy" in updates:
        _set_or_remove(permissions, "approvalPolicy", updates["approvalPolicy"])
        _set_or_remove(thread_settings, "approvalPolicy", updates["approvalPolicy"])
    if "sandboxMode" in updates:
        sandbox_policy = _sandbox_policy_from_mode(updates["sandboxMode"])
        if sandbox_policy is None:
            permissions.pop("sandboxPolicy", None)
            thread_settings.pop("sandboxPolicy", None)
        else:
            permissions["sandboxPolicy"] = sandbox_policy
            thread_settings["sandboxPolicy"] = sandbox_policy
    turn = latest_turn(state)
    params = turn.get("params") if isinstance(turn, dict) and isinstance(turn.get("params"), dict) else None
    if isinstance(params, dict):
        if "model" in updates:
            _set_or_remove(params, "model", updates["model"])
        if "reasoningEffort" in updates:
            _set_or_remove(params, "effort", updates["reasoningEffort"])
            _set_or_remove(params, "reasoningEffort", updates["reasoningEffort"])
        if "approvalPolicy" in updates:
            _set_or_remove(params, "approvalPolicy", updates["approvalPolicy"])
        if "sandboxMode" in updates:
            sandbox_policy = _sandbox_policy_from_mode(updates["sandboxMode"])
            if sandbox_policy is None:
                params.pop("sandboxPolicy", None)
            else:
                params["sandboxPolicy"] = sandbox_policy


def _set_or_remove(target: dict[str, Any], key: str, value: str | None) -> None:
    if value is None:
        target.pop(key, None)
    else:
        target[key] = value


def _sandbox_policy_from_mode(mode: str | None) -> dict[str, Any] | None:
    if mode is None:
        return None
    if mode == "read-only":
        return {"type": "readOnly", "networkAccess": False}
    if mode == "workspace-write":
        return {"type": "workspaceWrite", "networkAccess": False}
    if mode == "workspace-write-network":
        return {"type": "workspaceWrite", "networkAccess": True}
    if mode in {"danger-full-access", "full-access"}:
        return {"type": "dangerFullAccess"}
    return {"type": "readOnly", "networkAccess": False}

