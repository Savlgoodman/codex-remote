from __future__ import annotations

from .models import ThreadDetail


class RolloutReader:
    async def read_thread(self, conversation_id: str, rollout_path: str | None = None) -> ThreadDetail | None:
        # Placeholder for the next iteration. The SDK path is preferred for MVP.
        return None

