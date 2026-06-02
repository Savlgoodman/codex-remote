from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any, Callable

from .models import Message, ThreadDetail, ThreadSummary
from .normalizer import compact_text, detail_from_turns, message_from_item, sdk_model_to_json, summary_from_sdk_thread


StreamUpdate = Callable[[ThreadDetail], None]


class SdkReader:
    def __init__(self):
        self.available = False
        self.last_error: str | None = None
        try:
            from openai_codex import Codex, CodexConfig
        except Exception as exc:
            self.Codex = None
            self.CodexConfig = None
            self.last_error = str(exc)
        else:
            self.Codex = Codex
            self.CodexConfig = CodexConfig
            self.available = True

    async def list_threads(self, limit: int = 100) -> list[ThreadSummary]:
        return await asyncio.to_thread(self._list_threads_sync, limit)

    def _list_threads_sync(self, limit: int) -> list[ThreadSummary]:
        if self.Codex is None or self.CodexConfig is None:
            return []
        try:
            with self.Codex(config=self.CodexConfig()) as codex:
                response = codex.thread_list(limit=limit)
                data = sdk_model_to_json(response)
        except Exception as exc:
            self.last_error = str(exc)
            return []
        rows: list[ThreadSummary] = []
        for thread in data.get("data", []) if isinstance(data, dict) else []:
            if isinstance(thread, dict):
                summary = summary_from_sdk_thread(thread)
                if summary.conversation_id:
                    rows.append(summary)
        return rows

    async def read_thread(self, conversation_id: str) -> ThreadDetail | None:
        return await asyncio.to_thread(self._read_thread_sync, conversation_id)

    async def send_message(
        self,
        conversation_id: str,
        text: str,
        on_update: StreamUpdate | None = None,
        options: dict[str, Any] | None = None,
    ) -> ThreadDetail | None:
        return await asyncio.to_thread(self._send_message_sync, conversation_id, text, on_update, options)

    def _read_thread_sync(self, conversation_id: str) -> ThreadDetail | None:
        if self.Codex is None or self.CodexConfig is None:
            return None
        try:
            with self.Codex(config=self.CodexConfig()) as codex:
                thread = codex.thread_resume(conversation_id)
                response = thread.read(include_turns=True)
                data = sdk_model_to_json(response)
        except Exception as exc:
            self.last_error = str(exc)
            return None
        thread_data: dict[str, Any] = {}
        if isinstance(data, dict):
            thread_data = data.get("thread") if isinstance(data.get("thread"), dict) else data
        summary = summary_from_sdk_thread({**thread_data, "id": thread_data.get("id") or conversation_id})
        turns = thread_data.get("turns") if isinstance(thread_data.get("turns"), list) else []
        pagination = thread_data.get("turnsPagination") if isinstance(thread_data.get("turnsPagination"), dict) else None
        return detail_from_turns(summary, turns, pagination)

    def _send_message_sync(
        self,
        conversation_id: str,
        text: str,
        on_update: StreamUpdate | None = None,
        options: dict[str, Any] | None = None,
    ) -> ThreadDetail | None:
        if self.Codex is None or self.CodexConfig is None:
            self.last_error = "openai-codex SDK is unavailable"
            return None
        try:
            with self.Codex(config=self.CodexConfig()) as codex:
                thread = codex.thread_resume(conversation_id)
                initial_response = thread.read(include_turns=True)
                initial_detail = self._detail_from_thread_read(conversation_id, sdk_model_to_json(initial_response))
                handle = thread.turn(text, **self._sdk_turn_kwargs(options))
                stream_state = _SdkStreamState(conversation_id, text, handle.id, initial_detail)
                if on_update is not None:
                    on_update(stream_state.detail())
                for event in handle.stream():
                    if stream_state.apply_event(event) and on_update is not None:
                        on_update(stream_state.detail())
                response = thread.read(include_turns=True)
        except Exception as exc:
            self.last_error = str(exc)
            if on_update is not None:
                try:
                    on_update(_error_detail(conversation_id, text, str(exc)))
                except Exception:
                    pass
            return None
        return self._detail_from_thread_read(conversation_id, sdk_model_to_json(response))

    def _sdk_turn_kwargs(self, options: dict[str, Any] | None) -> dict[str, Any]:
        if not options:
            return {}
        kwargs: dict[str, Any] = {}
        model = _clean_option(options.get("model"))
        effort = _clean_option(options.get("reasoningEffort"))
        approval_policy = _clean_option(options.get("approvalPolicy"))
        sandbox_mode = _clean_option(options.get("sandboxMode"))
        if model:
            kwargs["model"] = model
        if effort:
            kwargs["effort"] = effort
        approval_mode = _sdk_approval_mode(approval_policy)
        if approval_mode is not None:
            kwargs["approval_mode"] = approval_mode
        sandbox = _sdk_sandbox(sandbox_mode)
        if sandbox is not None:
            kwargs["sandbox"] = sandbox
        return kwargs

    def _detail_from_thread_read(self, conversation_id: str, data: Any) -> ThreadDetail:
        thread_data: dict[str, Any] = {}
        if isinstance(data, dict):
            thread_data = data.get("thread") if isinstance(data.get("thread"), dict) else data
        summary = summary_from_sdk_thread({**thread_data, "id": thread_data.get("id") or conversation_id})
        turns = thread_data.get("turns") if isinstance(thread_data.get("turns"), list) else []
        pagination = thread_data.get("turnsPagination") if isinstance(thread_data.get("turnsPagination"), dict) else None
        return detail_from_turns(summary, turns, pagination)


class _SdkStreamState:
    def __init__(self, conversation_id: str, text: str, turn_id: str, initial_detail: ThreadDetail):
        self.conversation_id = conversation_id
        self.turn_id = turn_id
        self.summary = replace(
            initial_detail.summary,
            runtime_status="active",
            latest_turn_status="inProgress",
            latest_item_preview=compact_text(f"user: {text}", 180),
            active_at=time.time(),
            updated_at=time.time(),
        )
        self.messages = list(initial_detail.messages)
        self.message_indexes: dict[str, int] = {}
        self.messages.append(Message(f"{turn_id}-params-input", "user", text, "inProgress", time.time(), {"input": text}))

    def detail(self) -> ThreadDetail:
        return ThreadDetail(summary=self.summary, messages=list(self.messages), raw_turns=[], pagination=None)

    def apply_event(self, event: Any) -> bool:
        payload = getattr(event, "payload", None)
        method = getattr(event, "method", None) or type(payload).__name__
        data = sdk_model_to_json(payload)
        if not isinstance(data, dict):
            return False

        if method == "AgentMessageDeltaNotification" or "delta" in data:
            delta = str(data.get("delta") or "")
            if not delta:
                return False
            item_id = str(data.get("itemId") or data.get("item_id") or "agent")
            message = self._message_for_delta(item_id, data)
            message.text += delta
            message.status = "inProgress"
            self._touch(f"agent: {message.text}")
            return True

        if method == "ItemStartedNotification" or ("item" in data and "startedAtMs" in data):
            item = _root_item(data.get("item"))
            if not isinstance(item, dict):
                return False
            item_type = item.get("type") or item.get("kind")
            if item_type in {"agentMessage", "agent-message", "assistantMessage"}:
                return False
            message = message_from_item(item, self._message_id(item), _millis_to_seconds(data.get("startedAtMs")))
            if message is None:
                return False
            message.status = message.status or "inProgress"
            self._upsert_message(message)
            self._touch(message.text)
            return True

        if method == "ItemCompletedNotification" or ("item" in data and "completedAtMs" in data):
            item = _root_item(data.get("item"))
            if not isinstance(item, dict):
                return False
            message = message_from_item(item, self._message_id(item), _millis_to_seconds(data.get("completedAtMs")))
            if message is None:
                return False
            message.status = message.status or "completed"
            existing = self._existing_message(message.id)
            if existing is None or message.text:
                self._upsert_message(message)
            else:
                existing.status = message.status
            self._touch(message.text or self.summary.latest_item_preview)
            return True

        if method == "TurnCompletedNotification" or "turn" in data:
            turn = data.get("turn") if isinstance(data.get("turn"), dict) else {}
            status = str(turn.get("status") or "completed")
            self.summary = replace(self.summary, runtime_status="idle", latest_turn_status=status, updated_at=time.time())
            for message in self.messages:
                if message.status == "inProgress":
                    message.status = status
            return True

        return False

    def _message_for_delta(self, item_id: str, raw: Any) -> Message:
        message_id = f"{self.turn_id}-sdk-delta-{item_id}"
        existing = self._existing_message(message_id)
        if existing is not None:
            return existing
        message = Message(message_id, "assistant", "", "inProgress", time.time(), raw)
        self._upsert_message(message)
        return message

    def _message_id(self, item: dict[str, Any]) -> str:
        item_type = item.get("type") or item.get("kind")
        if item_type in {"agentMessage", "agent-message", "assistantMessage"}:
            return f"{self.turn_id}-sdk-delta-{item.get('id') or 'agent'}"
        return f"{self.turn_id}-sdk-item-{item.get('id') or len(self.messages)}"

    def _existing_message(self, message_id: str) -> Message | None:
        index = self.message_indexes.get(message_id)
        if index is None:
            return None
        if index >= len(self.messages):
            return None
        return self.messages[index]

    def _upsert_message(self, message: Message) -> None:
        index = self.message_indexes.get(message.id)
        if index is None or index >= len(self.messages):
            self.message_indexes[message.id] = len(self.messages)
            self.messages.append(message)
            return
        self.messages[index] = message

    def _touch(self, preview: str) -> None:
        self.summary = replace(
            self.summary,
            latest_item_preview=compact_text(preview, 180),
            active_at=time.time(),
            updated_at=time.time(),
        )


def _root_item(value: Any) -> Any:
    item = sdk_model_to_json(value)
    if isinstance(item, dict) and isinstance(item.get("root"), dict):
        return item["root"]
    return item


def _millis_to_seconds(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value) / 1000
    return None


def _error_detail(conversation_id: str, text: str, error: str) -> ThreadDetail:
    summary = ThreadSummary(
        conversation_id=conversation_id,
        source="history-only",
        runtime_status="systemError",
        latest_turn_status="failed",
        latest_item_preview=compact_text(error, 180),
        active_at=time.time(),
        updated_at=time.time(),
    )
    messages = [
        Message(f"sdk-error-user-{int(time.time() * 1000)}", "user", text, "failed", time.time(), {"input": text}),
        Message(f"sdk-error-{int(time.time() * 1000)}", "system", error, "failed", time.time(), {"error": error}),
    ]
    return ThreadDetail(summary=summary, messages=messages)


def _clean_option(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value == "inherit":
        return None
    return value


def _sdk_approval_mode(value: str | None) -> Any:
    if value is None:
        return None
    try:
        from openai_codex import ApprovalMode
    except Exception:
        return value
    if value in {"never", "deny_all", "deny-all"}:
        return ApprovalMode.deny_all
    if value in {"on-request", "auto_review", "auto-review"}:
        return ApprovalMode.auto_review
    return value


def _sdk_sandbox(value: str | None) -> Any:
    if value is None:
        return None
    try:
        from openai_codex import Sandbox
    except Exception:
        return value
    if value == "read-only":
        return Sandbox.read_only
    if value in {"workspace-write", "workspace-write-network"}:
        return Sandbox.workspace_write
    if value in {"danger-full-access", "full-access"}:
        return Sandbox.full_access
    return value

