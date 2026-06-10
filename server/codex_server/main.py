from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from .events.bus import EventBus, ping_event, sse_encode
from .ipc.client import IpcClient
from .models import ServerEvent, SdkStatus
from .projection.store import ProjectionStore


event_bus = EventBus()
store = ProjectionStore(event_bus)
sdk_status = SdkStatus(available=False)
ipc_client = IpcClient(on_message=store.handle_ipc_message, on_status=store.set_ipc_status)


@asynccontextmanager
async def lifespan(app: FastAPI):
    event_bus.bind_loop(asyncio.get_running_loop())
    ipc_client.start_background()
    await event_bus.publish(ServerEvent("status.changed", {"ipc": store.ipc_status.to_json(), "sdk": sdk_status.to_json()}))
    yield
    ipc_client.stop()


app = FastAPI(title="Codex Remote", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:7001", "http://localhost:7001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
def get_status() -> dict[str, Any]:
    return {"ipc": store.ipc_status.to_json(), "sdk": sdk_status.to_json()}


@app.get("/api/threads")
def list_threads() -> dict[str, Any]:
    return {"threads": [summary.to_json() for summary in store.list_threads()]}


@app.get("/api/threads/{conversation_id}")
def get_thread(conversation_id: str) -> dict[str, Any]:
    projection = store.get_projection(conversation_id)
    if projection is None:
        raise HTTPException(status_code=404, detail="thread_not_found")
    return projection.to_detail_json()


@app.get("/api/events")
async def global_events(request: Request, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
    return StreamingResponse(_event_stream(request, last_event_id=last_event_id), media_type="text/event-stream")


@app.get("/api/threads/{conversation_id}/events")
async def thread_events(
    conversation_id: str,
    request: Request,
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
):
    return StreamingResponse(
        _event_stream(request, conversation_id=conversation_id, last_event_id=last_event_id),
        media_type="text/event-stream",
    )


@app.post("/api/threads/{conversation_id}/messages")
async def send_message(conversation_id: str, body: dict[str, Any]) -> dict[str, Any]:
    if os.environ.get("CODEX_REMOTE_ENABLE_CONTROL") != "1":
        raise HTTPException(status_code=403, detail={"error": "control_disabled"})
    projection = store.get_projection(conversation_id)
    if projection is None:
        raise HTTPException(status_code=404, detail={"error": "thread_not_found"})
    summary = projection.summary
    if summary.sandbox_type == "dangerFullAccess" and not body.get("confirmDangerFullAccess"):
        raise HTTPException(status_code=409, detail={"error": "dangerFullAccess_requires_confirmation"})
    if not store.ipc_status.online or not summary.has_live_owner:
        raise HTTPException(status_code=409, detail={"error": "sdk_route_not_implemented"})
    raise HTTPException(status_code=501, detail={"error": "ipc_send_not_implemented"})


async def _event_stream(
    request: Request,
    *,
    conversation_id: str | None = None,
    last_event_id: str | None = None,
):
    parsed_last_id = _parse_event_id(last_event_id)
    queue = await event_bus.subscribe(last_event_id=parsed_last_id)
    try:
        while True:
            if await request.is_disconnected():
                break
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                yield ping_event()
                continue
            if conversation_id is not None and event.conversation_id not in {None, conversation_id}:
                continue
            yield sse_encode(event)
    finally:
        await event_bus.unsubscribe(queue)


def _parse_event_id(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def dumps_debug(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)

