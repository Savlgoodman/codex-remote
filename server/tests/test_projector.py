from __future__ import annotations

import json
from collections import Counter
import unittest
from pathlib import Path

from codex_server.events.bus import EventBus
from codex_server.models import ServerEvent
from codex_server.projection.store import ProjectionStore


ROOT = Path(__file__).resolve().parents[2]


class ProjectorReplayTests(unittest.TestCase):
    def replay(self, *stamps: str) -> ProjectionStore:
        bus = EventBus()
        store = ProjectionStore(bus)
        for stamp in stamps:
            path = next((ROOT / "ipc-data").glob(f"*{stamp}*.jsonl"))
            for line in path.read_text(encoding="utf-8").splitlines():
                packet = json.loads(line)
                store.handle_ipc_message(packet["raw"])
        return store

    def test_continue_owner_session_projection(self) -> None:
        store = self.replay("T08-48-53-089Z")
        threads = store.list_threads()
        self.assertEqual(len(threads), 1)
        summary = threads[0]
        self.assertEqual(summary.conversation_id, "019eab90-b721-7000-a4a9-ca0dcebe7c78")
        self.assertEqual(summary.source, "live")
        self.assertEqual(summary.runtime_status, "idle")
        self.assertEqual(summary.latest_turn_status, "completed")
        self.assertEqual(summary.sandbox_type, "dangerFullAccess")
        projection = store.get_projection(summary.conversation_id)
        self.assertIsNotNone(projection)
        assert projection is not None
        self.assertEqual(len(projection.turns), 2)
        self.assertGreaterEqual(len(projection.messages), 4)
        self.assertIn("我是 Codex", projection.messages[-1].text)

    def test_settings_projection(self) -> None:
        store = self.replay("T08-48-53-089Z", "T08-49-58-210Z")
        threads = store.list_threads()
        self.assertEqual(len(threads), 1)
        summary = threads[0]
        self.assertEqual(summary.latest_model, "gpt-5.4")
        self.assertEqual(summary.latest_reasoning_effort, "high")
        projection = store.get_projection(summary.conversation_id)
        self.assertIsNotNone(projection)
        assert projection is not None
        self.assertEqual(projection.settings.model, "gpt-5.4")
        self.assertEqual(projection.settings.reasoning_effort, "high")

    def replay_events(self, stamp: str) -> list[ServerEvent]:
        bus = EventBus()
        store = ProjectionStore(bus)
        events: list[ServerEvent] = []
        path = next((ROOT / "ipc-data").glob(f"*{stamp}*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines():
            packet = json.loads(line)
            raw = packet["raw"]
            if raw.get("type") != "broadcast" or raw.get("method") != "thread-stream-state-changed":
                continue
            params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
            change = params.get("change") if isinstance(params.get("change"), dict) else None
            conversation_id = str(params.get("conversationId") or "")
            if not conversation_id or change is None:
                continue
            source_client_id = raw.get("sourceClientId") if isinstance(raw.get("sourceClientId"), str) else None
            events.extend(store._apply_change(conversation_id, change, source_client_id))
        return events

    def test_sse_uses_compact_projection_events(self) -> None:
        events = self.replay_events("T08-48-53-089Z")
        event_types = Counter(event.type for event in events)
        self.assertNotIn("thread.snapshot", event_types)
        self.assertEqual(event_types["turn.started"], 1)

        upsert_ids = [
            event.payload["message"]["id"]
            for event in events
            if event.type == "message.upsert" and isinstance(event.payload.get("message"), dict)
        ]
        self.assertEqual(Counter(upsert_ids).most_common(1)[0][1], 1)

        append_events = [event for event in events if event.type == "message.append"]
        self.assertGreater(len(append_events), 0)
        for event in append_events:
            self.assertIn("delta", event.payload)
            self.assertNotIn("text", event.payload)

        patch_events = [event for event in events if event.type == "message.patch"]
        self.assertGreater(len(patch_events), 0)
        for event in patch_events:
            self.assertIn("changes", event.payload)
            self.assertNotIn("text", event.payload)


if __name__ == "__main__":
    unittest.main()
