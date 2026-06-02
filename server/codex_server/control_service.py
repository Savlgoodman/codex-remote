from __future__ import annotations

from copy import deepcopy
from typing import Any

from .ipc_client import IpcClient
from .models import ThreadSummary
from .normalizer import latest_turn
from .state_store import StateStore


class ControlError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ControlService:
    def __init__(self, store: StateStore, ipc: IpcClient):
        self.store = store
        self.ipc = ipc

    async def send_message(self, conversation_id: str, text: str, *, confirm_danger_full_access: bool = False) -> dict[str, Any]:
        summary = self.store.get_summary(conversation_id)
        if not self.ipc.status.online:
            raise ControlError("ipc_offline", "Codex App / VSCode 插件未运行，无法通过 UI owner 发送。")
        if summary is None or not summary.has_live_owner:
            raise ControlError("owner_not_found", "当前线程没有可用的 App/VSCode owner。")
        if summary.runtime_status not in {"idle", "unknown"} and summary.latest_turn_status not in {"completed", "-", "failed"}:
            raise ControlError("thread_busy", "当前线程正在运行，普通 start-turn 暂不可用。")
        params = self.build_turn_start_params(conversation_id, text, summary)
        sandbox = params.get("sandboxPolicy")
        if isinstance(sandbox, dict) and sandbox.get("type") == "dangerFullAccess" and not confirm_danger_full_access:
            raise ControlError("dangerFullAccess_requires_confirmation", "该线程当前是 dangerFullAccess，需要二次确认后发送。")
        response = await self.ipc.request_async(
            "thread-follower-start-turn",
            {"conversationId": conversation_id, "turnStartParams": params},
            version=1,
            timeout=45,
        )
        return {"ok": True, "mode": "ipc-owner", "ipcResponse": response}

    def build_turn_start_params(self, conversation_id: str, text: str, summary: ThreadSummary) -> dict[str, Any]:
        snapshot = self.store.get_snapshot(conversation_id)
        if snapshot is None or not isinstance(snapshot.state, dict):
            raise ControlError("missing_snapshot", "没有 live snapshot，无法安全构造 turnStartParams。")
        state = snapshot.state
        turn = latest_turn(state)
        latest_params = turn.get("params") if isinstance(turn, dict) else None
        params = deepcopy(latest_params) if isinstance(latest_params, dict) else {}
        current_permissions = state.get("currentPermissions") if isinstance(state.get("currentPermissions"), dict) else {}
        params["threadId"] = conversation_id
        params["input"] = [{"type": "text", "text": text if text.endswith("\n") else f"{text}\n", "text_elements": []}]
        params["cwd"] = params.get("cwd") or state.get("cwd") or summary.cwd or ""
        params["attachments"] = []
        params["commentAttachments"] = []
        params["approvalPolicy"] = params.get("approvalPolicy") or current_permissions.get("approvalPolicy") or "on-request"
        params["approvalsReviewer"] = params.get("approvalsReviewer") or current_permissions.get("approvalsReviewer") or "user"
        params["sandboxPolicy"] = params.get("sandboxPolicy") or current_permissions.get("sandboxPolicy") or {"type": "readOnly", "networkAccess": False}
        params["collaborationMode"] = params.get("collaborationMode") or state.get("latestCollaborationMode")
        params["model"] = params.get("model", None)
        params["effort"] = params.get("effort", None)
        params["serviceTier"] = params.get("serviceTier", None)
        params["summary"] = params.get("summary") or "none"
        params["personality"] = params.get("personality", None)
        params["outputSchema"] = params.get("outputSchema", None)
        return params

