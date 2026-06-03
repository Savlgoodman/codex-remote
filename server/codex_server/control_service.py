from __future__ import annotations

from copy import deepcopy
from typing import Any

from .ipc_client import IpcClient
from .models import MessageOptions, MessageRouteDecision, SendMessageCommand, ThreadSummary
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

    async def send_message(self, command: SendMessageCommand) -> dict[str, Any]:
        decision = self.decide_send_route(command.conversation_id)
        if decision.mode == "ipc-owner":
            summary = self.store.get_summary(command.conversation_id)
            if summary is None:
                raise ControlError("thread_not_found", "Thread summary disappeared before IPC send.")
            return await self._send_message_via_ipc(command, summary, decision)
        return await self._send_message_via_sdk(command, decision)

    def decide_send_route(self, conversation_id: str) -> MessageRouteDecision:
        summary = self.store.get_summary(conversation_id)
        if not self.ipc.status.online:
            return MessageRouteDecision("sdk-background", "ipc_offline")
        if summary is None:
            return MessageRouteDecision("sdk-background", "missing_summary")
        if summary.source != "live" or not summary.has_live_owner:
            return MessageRouteDecision("sdk-background", "thread_not_live")
        snapshot = self.store.get_snapshot(conversation_id)
        if snapshot is None or not isinstance(snapshot.state, dict):
            return MessageRouteDecision("sdk-background", "missing_live_snapshot")
        return MessageRouteDecision("ipc-owner", "live_owner")

    async def _send_message_via_ipc(
        self,
        command: SendMessageCommand,
        summary: ThreadSummary,
        decision: MessageRouteDecision,
    ) -> dict[str, Any]:
        if summary.runtime_status not in {"idle", "unknown"} and summary.latest_turn_status not in {"completed", "-", "failed"}:
            raise ControlError("thread_busy", "The target thread is currently running; start-turn is not available.")

        params = self.build_turn_start_params(command.conversation_id, command.text, summary)
        self.apply_turn_options(params, command.options)
        await self.sync_model_and_reasoning_via_ipc(command.conversation_id, params, command.options)

        sandbox = params.get("sandboxPolicy")
        if isinstance(sandbox, dict) and sandbox.get("type") == "dangerFullAccess" and not command.confirm_danger_full_access:
            raise ControlError(
                "dangerFullAccess_requires_confirmation",
                "This thread uses dangerFullAccess and requires confirmation before sending.",
            )

        response = await self.ipc.request_async(
            "thread-follower-start-turn",
            {"conversationId": command.conversation_id, "turnStartParams": params},
            version=1,
            timeout=45,
        )
        return {"ok": True, **decision.to_json(), "route": decision.to_json(), "ipcResponse": response}

    async def _send_message_via_sdk(self, command: SendMessageCommand, decision: MessageRouteDecision) -> dict[str, Any]:
        if not self.sdk.available:
            raise ControlError("sdk_unavailable", f"openai-codex SDK is unavailable: {self.sdk.last_error or 'unknown error'}")
        sandbox_mode = command.options.sandbox_mode
        summary = self.store.get_summary(command.conversation_id)
        if sandbox_mode is None and summary is not None:
            sandbox_mode = summary.sandbox_mode
        if sandbox_mode in {"danger-full-access", "full-access"} and not command.confirm_danger_full_access:
            raise ControlError(
                "dangerFullAccess_requires_confirmation",
                "This send will use dangerFullAccess and requires confirmation before sending.",
            )

        def publish_update(detail: Any) -> None:
            self._publish_sdk_detail(command.conversation_id, detail)

        detail = await self.sdk.send_message(command.conversation_id, command.text, on_update=publish_update, options=command.options)
        if detail is None:
            raise ControlError("sdk_send_failed", self.sdk.last_error or "SDK resume send failed.")
        self._publish_sdk_detail(command.conversation_id, detail)
        return {"ok": True, **decision.to_json(), "route": decision.to_json()}

    def _publish_sdk_detail(self, conversation_id: str, detail: Any) -> None:
        self.store.upsert_detail(detail)
        cached = self.store.get_detail(conversation_id)
        if cached is None:
            cached = detail
        self.store.publish(
            {
                "type": "thread.snapshot",
                "version": 1,
                "reason": "sdk_stream",
                "conversationId": conversation_id,
                "summary": cached.summary.to_json(),
                "messages": [message.to_json() for message in cached.messages],
            }
        )
        self.store.publish({"type": "thread.summary", "version": 1, "conversationId": conversation_id, "summary": cached.summary.to_json()})

    def build_turn_start_params(self, conversation_id: str, text: str, summary: ThreadSummary) -> dict[str, Any]:
        snapshot = self.store.get_snapshot(conversation_id)
        if snapshot is None or not isinstance(snapshot.state, dict):
            raise ControlError("missing_snapshot", "No live snapshot is available to build turnStartParams.")
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

    def apply_turn_options(self, params: dict[str, Any], options: MessageOptions) -> None:
        if options.model:
            params["model"] = options.model
        if options.reasoning_effort:
            params["effort"] = options.reasoning_effort
            params["reasoningEffort"] = options.reasoning_effort
        if options.model or options.reasoning_effort:
            collaboration_mode = params.get("collaborationMode")
            if isinstance(collaboration_mode, dict):
                agents = collaboration_mode.get("agents")
                if isinstance(agents, dict):
                    for agent in agents.values():
                        if isinstance(agent, dict):
                            if options.model:
                                agent["model"] = options.model
                            if options.reasoning_effort:
                                agent["reasoning_effort"] = options.reasoning_effort
                                agent["reasoningEffort"] = options.reasoning_effort
        if options.approval_policy:
            params["approvalPolicy"] = options.approval_policy
        if options.sandbox_mode:
            params["sandboxPolicy"] = _sandbox_policy_from_mode(options.sandbox_mode)

    async def sync_model_and_reasoning_via_ipc(
        self,
        conversation_id: str,
        params: dict[str, Any],
        options: MessageOptions,
    ) -> None:
        if not options.model and not options.reasoning_effort:
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
