from __future__ import annotations

import asyncio
from typing import Any

from .control_service import ControlService
from .models import ReadMessagesQuery, SendMessageCommand, ThreadDetail
from .rollout_reader import RolloutReader, enrich_detail_from_rollout
from .sdk_reader import SdkReader
from .state_store import StateStore


class MessageService:
    def __init__(self, store: StateStore, sdk: SdkReader, rollout: RolloutReader, control: ControlService):
        self.store = store
        self.sdk = sdk
        self.rollout = rollout
        self.control = control

    async def read_messages(self, query: ReadMessagesQuery) -> ThreadDetail | None:
        rollout_path = self._rollout_path_for(query.conversation_id)
        detail = await self.sdk.read_thread(query.conversation_id)
        if detail is not None:
            detail = await asyncio.to_thread(enrich_detail_from_rollout, detail, rollout_path)
        else:
            detail = await self.rollout.read_thread(query.conversation_id, rollout_path)

        if detail is not None:
            self.store.upsert_detail(detail)
        return self.store.get_detail(query.conversation_id)

    async def send_message(self, command: SendMessageCommand) -> dict[str, Any]:
        return await self.control.send_message(command)

    def _rollout_path_for(self, conversation_id: str) -> str | None:
        snapshot = self.store.get_snapshot(conversation_id)
        if snapshot is None or not isinstance(snapshot.state, dict):
            return None
        rollout_path = snapshot.state.get("rolloutPath")
        return rollout_path if isinstance(rollout_path, str) else None
