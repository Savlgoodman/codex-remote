from __future__ import annotations

import asyncio
from typing import Any

from .models import ThreadDetail, ThreadSummary
from .normalizer import detail_from_turns, sdk_model_to_json, summary_from_sdk_thread


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

