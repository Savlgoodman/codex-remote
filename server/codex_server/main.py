from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .control_service import ControlError, ControlService
from .ipc_client import IpcClient
from .rollout_reader import RolloutReader
from .sdk_reader import SdkReader
from .state_store import StateStore


store = StateStore()
sdk_reader = SdkReader()
rollout_reader = RolloutReader()
ipc_client = IpcClient(on_message=store.handle_ipc_message, on_status=store.set_ipc_status)
control_service = ControlService(store, ipc_client)
CONTROL_ENABLED = os.environ.get("CODEX_WEBUI_ENABLE_CONTROL", "1") not in {"0", "false", "False"}


class SendMessageRequest(BaseModel):
    text: str
    confirmDangerFullAccess: bool = False


async def refresh_sdk_threads(limit: int = 100) -> None:
    rows = await sdk_reader.list_threads(limit=limit)
    for summary in rows:
        store.upsert_history_summary(summary)
    store.publish({"type": "threads.changed", "threads": [item.to_json() for item in store.list_threads()]})


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.bind_loop(asyncio.get_running_loop())
    ipc_client.start_background()
    await refresh_sdk_threads()
    yield
    ipc_client.stop()


app = FastAPI(title="codex-server", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CODEX_WEBUI_CORS", "http://localhost:5173,http://127.0.0.1:5173").split(","),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/status")
async def api_status() -> dict[str, Any]:
    return {
        "ipc": store.ipc_status.to_json(),
        "sdk": {"available": sdk_reader.available, "lastError": sdk_reader.last_error},
        "control": {"enabled": CONTROL_ENABLED},
        "time": time.time(),
    }


@app.post("/api/refresh")
async def api_refresh() -> dict[str, Any]:
    await refresh_sdk_threads()
    return {"ok": True, "threads": len(store.list_threads())}


@app.get("/api/threads")
async def api_threads(limit: int = 100) -> dict[str, Any]:
    if not store.list_threads():
        await refresh_sdk_threads(limit=limit)
    return {"threads": [item.to_json() for item in store.list_threads()[:limit]]}


@app.get("/api/threads/{conversation_id}")
async def api_thread_detail(conversation_id: str, raw: bool = False) -> dict[str, Any]:
    detail = await sdk_reader.read_thread(conversation_id)
    if detail is None:
        snapshot = store.get_snapshot(conversation_id)
        rollout_path = None
        if snapshot is not None and isinstance(snapshot.state, dict):
            rollout_path = snapshot.state.get("rolloutPath")
        detail = await rollout_reader.read_thread(conversation_id, rollout_path)
    if detail is not None:
        store.upsert_detail(detail)
    cached = store.get_detail(conversation_id)
    if cached is None:
        summary = store.get_summary(conversation_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="thread_not_found")
        return {"summary": summary.to_json(), "messages": [], "pagination": None}
    return cached.to_json(include_raw=raw)


@app.post("/api/threads/{conversation_id}/messages")
async def api_send_message(conversation_id: str, request: SendMessageRequest) -> JSONResponse:
    if not CONTROL_ENABLED:
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": "control_disabled", "message": "codex-server control API is disabled."},
        )
    if not request.text.strip():
        raise HTTPException(status_code=400, detail="empty_text")
    try:
        result = await control_service.send_message(
            conversation_id,
            request.text,
            confirm_danger_full_access=request.confirmDangerFullAccess,
        )
    except ControlError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": exc.code, "message": exc.message})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": "send_failed", "message": str(exc)})
    return JSONResponse(content=result)


@app.websocket("/api/events")
async def websocket_events(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = store.subscribe()
    try:
        await websocket.send_json({"type": "ipc.status", **store.ipc_status.to_json()})
        await websocket.send_json({"type": "threads.changed", "threads": [item.to_json() for item in store.list_threads()]})
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        store.unsubscribe(queue)
