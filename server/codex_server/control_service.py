from __future__ import annotations

from copy import deepcopy
from typing import Any

from .ipc_client import IpcClient
from .models import ThreadSummary
from .normalizer import latest_turn
from .sdk_reader import SdkReader
from .state_store import StateStore


class ControlError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class ControlService:
    def __init__(self, store: StateStore, ipc: IpcClient, sdk: SdkReader):
        self.store = store
        self.ipc = ipc
        self.sdk = sdk

    async def send_message(
        self,
        conversation_id: str,
        text: str,
        *,
        confirm_danger_full_access: bool = False,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        summary = self.store.get_summary(conversation_id)
        if self.ipc.status.online and summary is not None and summary.has_live_owner:
            return await self.send_message_via_ipc(
                conversation_id,
                text,
                summary,
                confirm_danger_full_access=confirm_danger_full_access,
                options=options,
            )
        return await self.send_message_via_sdk(
            conversation_id,
            text,
            options=options,
            confirm_danger_full_access=confirm_danger_full_access,
        )

    async def send_message_via_ipc(
        self,
        conversation_id: str,
        text: str,
        summary: ThreadSummary,
        *,
        confirm_danger_full_access: bool = False,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if summary.runtime_status not in {"idle", "unknown"} and summary.latest_turn_status not in {"completed", "-", "failed"}:
            raise ControlError("thread_busy", "当前线程正在运行，普通 start-turn 暂不可用。")
        params = self.build_turn_start_params(conversation_id, text, summary)
        self.apply_turn_options(params, options)
        await self.sync_model_and_reasoning_via_ipc(conversation_id, params, options)
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

    async def send_message_via_sdk(
        self,
        conversation_id: str,
        text: str,
        *,
        options: dict[str, Any] | None = None,
        confirm_danger_full_access: bool = False,
    ) -> dict[str, Any]:
        if not self.sdk.available:
            raise ControlError("sdk_unavailable", f"openai-codex SDK 不可用：{self.sdk.last_error or 'unknown error'}")
        if _clean_option((options or {}).get("sandboxMode")) in {"danger-full-access", "full-access"} and not confirm_danger_full_access:
            raise ControlError("dangerFullAccess_requires_confirmation", "该线程将使用 dangerFullAccess，需要二次确认后发送。")

        def publish_update(detail: Any) -> None:
            self._publish_sdk_detail(conversation_id, detail)

        detail = await self.sdk.send_message(conversation_id, text, on_update=publish_update, options=options)
        if detail is None:
            raise ControlError("sdk_send_failed", self.sdk.last_error or "SDK resume 发送失败。")
        self._publish_sdk_detail(conversation_id, detail)
        return {"ok": True, "mode": "sdk-background"}

    def _publish_sdk_detail(self, conversation_id: str, detail: Any) -> None:
        self.store.upsert_detail(detail)
        cached = self.store.get_detail(conversation_id)
        if cached is None:
            cached = detail
        self.store.publish(
            {
                "type": "thread.snapshot",
                "conversationId": conversation_id,
                "summary": cached.summary.to_json(),
                "messages": [message.to_json() for message in cached.messages],
            }
        )
        self.store.publish({"type": "threads.changed", "threads": [item.to_json() for item in self.store.list_threads()]})

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

    def apply_turn_options(self, params: dict[str, Any], options: dict[str, Any] | None) -> None:
        if not options:
            return
        model = _clean_option(options.get("model"))
        reasoning_effort = _clean_option(options.get("reasoningEffort"))
        approval_policy = _clean_option(options.get("approvalPolicy"))
        sandbox_mode = _clean_option(options.get("sandboxMode"))

        if model:
            params["model"] = model
        if reasoning_effort:
            params["effort"] = reasoning_effort
            params["reasoningEffort"] = reasoning_effort
        if model or reasoning_effort:
            collaboration_mode = params.get("collaborationMode")
            if isinstance(collaboration_mode, dict):
                agents = collaboration_mode.get("agents")
                if isinstance(agents, dict):
                    for agent in agents.values():
                        if isinstance(agent, dict):
                            if model:
                                agent["model"] = model
                            if reasoning_effort:
                                agent["reasoning_effort"] = reasoning_effort
                                agent["reasoningEffort"] = reasoning_effort
        if approval_policy:
            params["approvalPolicy"] = approval_policy
        if sandbox_mode:
            params["sandboxPolicy"] = _sandbox_policy_from_mode(sandbox_mode)

    async def sync_model_and_reasoning_via_ipc(self, conversation_id: str, params: dict[str, Any], options: dict[str, Any] | None) -> None:
        if not options:
            return
        if not _clean_option(options.get("model")) and not _clean_option(options.get("reasoningEffort")):
            return
        request_params = {"conversationId": conversation_id}
        model = params.get("model")
        effort = params.get("reasoningEffort") or params.get("effort")
        if model is not None:
            request_params["model"] = model
        if effort is not None:
            request_params["reasoningEffort"] = effort
        try:
            await self.ipc.request_async("thread-follower-set-model-and-reasoning", request_params, version=1, timeout=15)
        except Exception:
            # The start-turn payload also carries these settings; don't fail the send if owner sync is unavailable.
            return


def _clean_option(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or value == "inherit":
        return None
    return value


def _sandbox_policy_from_mode(mode: str) -> dict[str, Any]:
    if mode == "read-only":
        return {"type": "readOnly", "networkAccess": False}
    if mode == "workspace-write":
        return {"type": "workspaceWrite", "networkAccess": False}
    if mode == "workspace-write-network":
        return {"type": "workspaceWrite", "networkAccess": True}
    if mode in {"danger-full-access", "full-access"}:
        return {"type": "dangerFullAccess"}
    return {"type": "readOnly", "networkAccess": False}

