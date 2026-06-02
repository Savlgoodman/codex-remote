from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from .models import IpcSnapshot, IpcStatus, Message, ThreadDetail, ThreadSummary
from .normalizer import apply_patch_list, detail_from_turns, summary_from_ipc_state


class StateStore:
    def __init__(self):
        self._lock = threading.RLock()
        self.ipc_status = IpcStatus()
        self._summaries: dict[str, ThreadSummary] = {}
        self._details: dict[str, ThreadDetail] = {}
        self._snapshots: dict[str, IpcSnapshot] = {}
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
            if was_online and not status.online:
                for summary in self._summaries.values():
                    if summary.source == "live":
                        summary.source = "stale"
                        summary.has_live_owner = False
        self.publish({"type": "ipc.status", **status.to_json()})
        self.publish({"type": "threads.changed", "threads": [item.to_json() for item in self.list_threads()]})

    def handle_ipc_message(self, message: dict[str, Any]) -> None:
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
        self.publish({"type": "thread.patch", "conversationId": conversation_id, "summary": event_summary, "patches": patches or []})
        if event_detail is not None:
            self.publish(
                {
                    "type": "thread.snapshot",
                    "conversationId": conversation_id,
                    "summary": event_summary,
                    "messages": [message.to_json() for message in event_detail.messages],
                }
            )
        self.publish({"type": "threads.changed", "threads": [item.to_json() for item in self.list_threads()]})

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

    def _merge_summary(self, existing: ThreadSummary | None, incoming: ThreadSummary) -> ThreadSummary:
        if existing is None:
            return incoming
        if incoming.source == "live":
            incoming.active_at = incoming.active_at or existing.active_at
            return incoming
        if existing.source in {"live", "stale"}:
            existing.title = existing.title if existing.title != "(untitled)" else incoming.title
            existing.cwd = existing.cwd or incoming.cwd
            existing.active_at = max(existing.active_at or 0, incoming.active_at or 0) or None
            existing.updated_at = max(existing.updated_at or 0, incoming.updated_at or 0) or None
            if not existing.latest_item_preview:
                existing.latest_item_preview = incoming.latest_item_preview
            return existing
        return incoming if (incoming.active_at or 0) >= (existing.active_at or 0) else existing

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

