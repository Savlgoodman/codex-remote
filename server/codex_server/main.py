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
from .message_service import MessageService
from .models import MessageOptions, ReadMessagesQuery, SendMessageCommand
from .rollout_reader import RolloutReader
from .sdk_reader import SdkReader
from .state_store import StateStore


store = StateStore()
sdk_reader = SdkReader()
rollout_reader = RolloutReader()
ipc_client = IpcClient(
    on_message=store.handle_ipc_message,
    on_status=store.set_ipc_status,
    on_raw_message=store.handle_ipc_raw_event,
)
control_service = ControlService(store, ipc_client, sdk_reader)
message_service = MessageService(store, sdk_reader, rollout_reader, control_service)
CONTROL_ENABLED = os.environ.get("CODEX_WEBUI_ENABLE_CONTROL", "1") not in {"0", "false", "False"}


class SendMessageRequest(BaseModel):
    text: str
    confirmDangerFullAccess: bool = False
    model: str | None = None
    reasoningEffort: str | None = None
    approvalPolicy: str | None = None
    sandboxMode: str | None = None


class ThreadSettingsRequest(BaseModel):
    confirmDangerFullAccess: bool = False
    model: str | None = None
    reasoningEffort: str | None = None
    approvalPolicy: str | None = None
    sandboxMode: str | None = None


async def refresh_sdk_threads(limit: int = 100) -> None:
    rows = await sdk_reader.list_threads(limit=limit)
    for summary in rows:
        store.upsert_history_summary(summary)
    store.publish({"type": "threads.snapshot", "version": 1, "reason": "refresh", "threads": [item.to_json() for item in store.list_threads()]})


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
    allow_origins=os.environ.get("CODEX_WEBUI_CORS", "http://localhost:7001,http://127.0.0.1:7001").split(","),
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


@app.post("/api/ipc-monitor/start")
async def api_ipc_monitor_start() -> dict[str, Any]:
    return {"ok": True, **store.set_ipc_monitor_capturing(True)}


@app.post("/api/ipc-monitor/pause")
async def api_ipc_monitor_pause() -> dict[str, Any]:
    return {"ok": True, **store.set_ipc_monitor_capturing(False)}


@app.get("/api/ipc-monitor")
async def api_ipc_monitor_status() -> dict[str, Any]:
    return store.ipc_monitor_status()


@app.get("/api/threads")
async def api_threads(limit: int = 100) -> dict[str, Any]:
    if not store.list_threads():
        await refresh_sdk_threads(limit=limit)
    return {"threads": [item.to_json() for item in store.list_threads()[:limit]]}


@app.get("/api/threads/{conversation_id}")
async def api_thread_detail(conversation_id: str, raw: bool = False) -> dict[str, Any]:
    detail = await message_service.read_messages(ReadMessagesQuery(conversation_id=conversation_id, include_raw=raw))
    if detail is None:
        summary = store.get_summary(conversation_id)
        if summary is None:
            raise HTTPException(status_code=404, detail="thread_not_found")
        return {"summary": summary.to_json(), "messages": [], "pagination": None}
    return detail.to_json(include_raw=raw)


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
        result = await message_service.send_message(
            SendMessageCommand(
                conversation_id=conversation_id,
                text=request.text,
                confirm_danger_full_access=request.confirmDangerFullAccess,
                options=MessageOptions.from_mapping(
                    {
                        "model": request.model,
                        "reasoningEffort": request.reasoningEffort,
                        "approvalPolicy": request.approvalPolicy,
                        "sandboxMode": request.sandboxMode,
                    }
                ),
            )
        )
    except ControlError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": exc.code, "message": exc.message})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": "send_failed", "message": str(exc)})
    return JSONResponse(content=result)


@app.post("/api/threads/{conversation_id}/settings")
async def api_update_thread_settings(conversation_id: str, request: ThreadSettingsRequest) -> JSONResponse:
    if not CONTROL_ENABLED:
        return JSONResponse(
            status_code=403,
            content={"ok": False, "error": "control_disabled", "message": "codex-server control API is disabled."},
        )
    updates = _settings_updates_from_request(request)
    try:
        result = await control_service.update_thread_settings(
            conversation_id,
            updates,
            confirm_danger_full_access=request.confirmDangerFullAccess,
        )
    except ControlError as exc:
        return JSONResponse(status_code=409, content={"ok": False, "error": exc.code, "message": exc.message})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": "settings_update_failed", "message": str(exc)})
    return JSONResponse(content=result)


@app.websocket("/api/events")
async def websocket_events(websocket: WebSocket) -> None:
    await websocket.accept()
    queue = store.subscribe()
    try:
        await websocket.send_json({"type": "ipc.status", "version": 1, **store.ipc_status.to_json()})
        await websocket.send_json(store.ipc_monitor_status())
        await websocket.send_json({"type": "threads.snapshot", "version": 1, "reason": "subscribe", "threads": [item.to_json() for item in store.list_threads()]})
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        store.unsubscribe(queue)


def _settings_updates_from_request(request: ThreadSettingsRequest) -> dict[str, str | None]:
    raw = request.model_dump(exclude_unset=True)
    raw.pop("confirmDangerFullAccess", None)
    updates: dict[str, str | None] = {}
    key_map = {
        "model": "model",
        "reasoningEffort": "reasoningEffort",
        "approvalPolicy": "approvalPolicy",
        "sandboxMode": "sandboxMode",
    }
    for request_key, update_key in key_map.items():
        if request_key in raw:
            updates[update_key] = _clean_setting(raw.get(request_key))
    return updates


def _clean_setting(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value == "inherit":
        return None
    return value
