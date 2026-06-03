from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Any

from .models import Message, ThreadDetail, ThreadSummary


TOOL_ITEM_TYPES = {
    "webSearch",
    "web_search_call",
    "tool_search_call",
    "tool_search_output",
    "custom_tool_call",
    "custom_tool_call_output",
    "mcpToolCall",
    "mcp-tool-call",
    "toolCall",
    "dynamicToolCall",
    "dynamic-tool-call",
    "collabAgentToolCall",
    "collab-agent-tool-call",
    "fileChange",
    "imageGeneration",
}


def compact_text(value: Any, limit: int = 180) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."


def find_text(value: Any, limit: int = 10000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(part for part in (find_text(item, limit) for item in value) if part)
    if isinstance(value, dict):
        root = value.get("root")
        if isinstance(root, dict):
            text = find_text(root, limit)
            if text:
                return text
        for key in ("text", "message", "input", "content", "value", "aggregatedOutput"):
            if key in value:
                text = find_text(value.get(key), limit)
                if text:
                    return text[:limit]
    return ""


def latest_turn(state: dict[str, Any] | None) -> dict[str, Any] | None:
    turns = state.get("turns") if isinstance(state, dict) else None
    if isinstance(turns, list) and turns:
        turn = turns[-1]
        return turn if isinstance(turn, dict) else None
    return None


def latest_item(state: dict[str, Any] | None) -> Any:
    turn = latest_turn(state)
    items = turn.get("items") if isinstance(turn, dict) else None
    if isinstance(items, list) and items:
        item = items[-1]
        return item.get("root", item) if isinstance(item, dict) else item
    return None


def thread_title(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return "(untitled)"
    return compact_text(state.get("title") or state.get("preview") or "(untitled)", 90)


def runtime_status(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return "unknown"
    runtime = state.get("threadRuntimeStatus")
    if isinstance(runtime, dict):
        return str(runtime.get("type") or runtime.get("status") or "unknown")
    return str(runtime or "unknown")


def thread_cwd(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return ""
    return str(state.get("cwd") or "")


def turn_status(state: dict[str, Any] | None) -> str:
    turn = latest_turn(state)
    if not isinstance(turn, dict):
        return "-"
    return str(turn.get("status") or "-")


def summarize_item(item: Any, limit: int = 180) -> str:
    if not isinstance(item, dict):
        return compact_text(item, limit)
    root = item.get("root")
    if isinstance(root, dict):
        item = root
    item_type = item.get("type") or item.get("kind") or "item"
    if item_type in {"userMessage", "user-message", "user_message"}:
        text = find_text(item.get("content") or item.get("message") or item.get("input"))
        return compact_text(f"user: {text}" if text else "user", limit)
    if item_type in {"agentMessage", "agent-message", "assistantMessage"}:
        text = find_text(item.get("text") or item.get("content") or item.get("message"))
        return compact_text(f"agent: {text}" if text else "agent", limit)
    if item_type in {"steeringUserMessage", "steering-user-message"}:
        text = find_text(item.get("input") or item.get("content"))
        status = item.get("status")
        suffix = f" [{status}]" if status else ""
        return compact_text(f"steer: {text}{suffix}" if text else f"steer{suffix}", limit)
    if item_type in {"commandExecution", "command-execution"}:
        command = compact_text(item.get("command") or item.get("cmd") or "", 110)
        status = item.get("status") or "unknown"
        return compact_text(f"command:{status} {command}".strip(), limit)
    if item_type == "fileChange":
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        paths = [str(change.get("path")) for change in changes if isinstance(change, dict) and change.get("path")]
        status = item.get("status") or "unknown"
        return compact_text(f"file change:{status} {', '.join(paths)}".strip(), limit)
    if item_type in {"webSearch", "web_search_call"}:
        query = item.get("query") or _tool_action_text(item.get("action"))
        status = item.get("status") or ""
        return compact_text(f"web search:{status} {query}".strip(), limit)
    if item_type in {"tool_search_call", "tool_search_output", "custom_tool_call", "custom_tool_call_output"}:
        name = item.get("name") or item.get("execution") or item.get("call_id") or item.get("id") or item_type
        status = item.get("status") or ""
        return compact_text(f"tool:{status} {name}".strip(), limit)
    if item_type in {"mcpToolCall", "mcp-tool-call", "toolCall"}:
        server = item.get("server")
        tool = item.get("tool") or item.get("name") or item.get("toolName") or item.get("method") or ""
        name = f"{server}.{tool}" if server and tool else str(tool)
        status = item.get("status") or ""
        return compact_text(f"{item_type}:{status} {name}".strip(), limit)
    if item_type in {"dynamicToolCall", "dynamic-tool-call"}:
        namespace = item.get("namespace")
        tool = item.get("tool") or item.get("name") or "tool"
        status = item.get("status") or ""
        label = f"{namespace}.{tool}" if namespace else str(tool)
        return compact_text(f"tool:{status} {label}".strip(), limit)
    if item_type == "reasoning":
        return "reasoning"
    text = find_text(item)
    return compact_text(f"{item_type}: {text}" if text else str(item_type), limit)


def get_patch_parent(root: Any, path: list[Any]) -> tuple[Any, Any] | None:
    if not path:
        return None
    current = root
    for part in path[:-1]:
        if isinstance(current, list):
            index = _list_index(part)
            if index is None or index < 0 or index >= len(current):
                return None
            current = current[index]
            continue
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
            continue
        return None
    return current, path[-1]


def apply_patch_list(state: dict[str, Any] | None, patches: Any) -> dict[str, Any] | None:
    if not isinstance(state, dict) or not isinstance(patches, list):
        return state
    next_state: Any = state
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        op = patch.get("op")
        path = _patch_path_parts(patch.get("path"))
        if path is None:
            continue
        if not path:
            if op in {"add", "replace"} and isinstance(patch.get("value"), dict):
                next_state = deepcopy(patch["value"])
            continue
        parent_pair = get_patch_parent(next_state, path)
        if parent_pair is None:
            continue
        parent, key = parent_pair
        if isinstance(parent, list):
            index = len(parent) if key == "-" else _list_index(key)
            if index is None:
                continue
            if op == "add" and 0 <= index <= len(parent):
                parent.insert(index, deepcopy(patch.get("value")))
            elif op == "replace" and 0 <= index < len(parent):
                parent[index] = deepcopy(patch.get("value"))
            elif op == "remove" and 0 <= index < len(parent):
                parent.pop(index)
            continue
        if isinstance(parent, dict):
            if op in {"add", "replace"}:
                parent[key] = deepcopy(patch.get("value"))
            elif op == "remove":
                parent.pop(key, None)
    return next_state if isinstance(next_state, dict) else state


def _patch_path_parts(path: Any) -> list[Any] | None:
    if isinstance(path, list):
        return path
    if not isinstance(path, str):
        return None
    if path == "":
        return []
    if not path.startswith("/"):
        return None
    parts: list[Any] = []
    for part in path[1:].split("/"):
        unescaped = part.replace("~1", "/").replace("~0", "~")
        parts.append(int(unescaped) if unescaped.isdecimal() else unescaped)
    return parts


def _list_index(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return None


def active_time_from_state(state: dict[str, Any] | None, fallback: float | None = None) -> float | None:
    turn = latest_turn(state)
    if isinstance(turn, dict):
        for key in ("completedAtMs", "turnCompletedAtMs", "turnStartedAtMs", "startedAtMs"):
            value = turn.get(key)
            if isinstance(value, (int, float)):
                return float(value) / 1000 if value > 10_000_000_000 else float(value)
    return fallback


def summary_from_ipc_state(conversation_id: str, state: dict[str, Any] | None, *, seen_at: float | None = None) -> ThreadSummary:
    seen_at = seen_at or time.time()
    latest_model, latest_reasoning_effort = model_settings_from_state(state)
    approval_policy, sandbox_mode = permission_settings_from_state(state)
    return ThreadSummary(
        conversation_id=conversation_id,
        title=thread_title(state),
        cwd=thread_cwd(state),
        source="live",
        runtime_status=runtime_status(state),
        latest_turn_status=turn_status(state),
        latest_item_preview=summarize_item(latest_item(state)),
        active_at=active_time_from_state(state, seen_at),
        updated_at=seen_at,
        has_live_owner=True,
        latest_model=latest_model,
        latest_reasoning_effort=latest_reasoning_effort,
        approval_policy=approval_policy,
        sandbox_mode=sandbox_mode,
    )


def message_from_item(item: dict[str, Any], message_id: str, created_at: float | None = None) -> Message | None:
    root = item.get("root")
    if isinstance(root, dict):
        item = root
    item_type = item.get("type") or item.get("kind")
    status = item.get("status") or item.get("phase")
    if item_type in {"userMessage", "user-message", "user_message"}:
        return Message(message_id, "user", find_text(item.get("content") or item.get("message") or item.get("input")), status, created_at, item)
    if item_type in {"agentMessage", "agent-message", "assistantMessage"}:
        return Message(message_id, "assistant", find_text(item.get("text") or item.get("content") or item.get("message")), status, created_at, item)
    if item_type in {"commandExecution", "command-execution"}:
        command = item.get("command") or item.get("cmd") or ""
        output = item.get("aggregatedOutput") or item.get("output") or ""
        text = command if not output else f"{command}\n\n{output}"
        return Message(message_id, "command", str(text), status, created_at, item)
    if item_type in TOOL_ITEM_TYPES:
        return Message(message_id, "tool", format_tool_item(item), status, created_at, item)
    if item_type == "reasoning":
        text = find_text(item.get("summary") or item.get("content") or item)
        if not text:
            return None
        return Message(message_id, "reasoning", text, status, created_at, item)
    text = find_text(item)
    if text:
        return Message(message_id, "system", text, status, created_at, item)
    return None


def detail_from_turns(summary: ThreadSummary, turns: list[Any], pagination: dict[str, Any] | None = None) -> ThreadDetail:
    messages: list[Message] = []
    for turn_index, turn in enumerate(turns):
        if not isinstance(turn, dict):
            continue
        turn_id = str(turn.get("turnId") or turn.get("id") or turn_index)
        created_at = None
        started = turn.get("turnStartedAtMs") or turn.get("startedAtMs")
        if isinstance(started, (int, float)):
            created_at = float(started) / 1000 if started > 10_000_000_000 else float(started)
        params = turn.get("params") if isinstance(turn.get("params"), dict) else {}
        items = turn.get("items") or []
        has_user_item = False
        if isinstance(items, list):
            for item in items:
                root = item.get("root", item) if isinstance(item, dict) else item
                if isinstance(root, dict) and root.get("type") in {"userMessage", "user-message", "user_message"}:
                    has_user_item = True
                    break
        input_text = find_text(params.get("input")) if isinstance(params, dict) else ""
        if input_text and not has_user_item:
            messages.append(Message(f"{turn_id}-params-input", "user", input_text, turn.get("status"), created_at, params))
        if isinstance(items, list):
            for item_index, item in enumerate(items):
                if not isinstance(item, dict):
                    continue
                message = message_from_item(item, f"{turn_id}-item-{item_index}", created_at)
                if message is not None:
                    messages.append(message)
    return ThreadDetail(summary=summary, messages=messages, raw_turns=turns, pagination=pagination)


def sdk_model_to_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value


def format_tool_item(item: dict[str, Any]) -> str:
    item_type = item.get("type") or item.get("kind") or "tool"
    if item_type in {"webSearch", "web_search_call"}:
        action = item.get("action")
        action_type = ""
        if isinstance(action, dict):
            root = action.get("root") if isinstance(action.get("root"), dict) else action
            if isinstance(root, dict):
                action_type = str(root.get("type") or "")
        query = item.get("query") or _tool_action_text(action)
        return compact_text(f"web search {action_type}: {query}".strip(), 1000)

    if item_type in {"tool_search_call", "custom_tool_call"}:
        name = item.get("name") or item.get("execution") or "tool"
        args = _compact_json_field(item.get("arguments") or item.get("input"), 520)
        return "\n".join(part for part in (str(name), f"args: {args}" if args else "") if part)

    if item_type in {"tool_search_output", "custom_tool_call_output"}:
        name = item.get("name") or item.get("execution") or "tool output"
        output = _compact_json_field(item.get("tools") or item.get("output") or item.get("content"), 700)
        return "\n".join(part for part in (str(name), f"output: {output}" if output else "") if part)

    if item_type == "fileChange":
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        if not changes:
            return "file changes"
        lines = ["file changes"]
        for change in changes[:12]:
            if not isinstance(change, dict):
                continue
            kind = change.get("kind") or "update"
            path = change.get("path") or "-"
            diff = str(change.get("diff") or "")
            summary = _diff_summary(diff)
            suffix = f" ({summary})" if summary else ""
            lines.append(f"{kind}: {path}{suffix}")
        if len(changes) > 12:
            lines.append(f"... {len(changes) - 12} more")
        return "\n".join(lines)

    if item_type in {"mcpToolCall", "mcp-tool-call", "toolCall"}:
        server = item.get("server")
        tool = item.get("tool") or item.get("name") or item.get("toolName") or item.get("method") or "tool"
        title = f"{server}.{tool}" if server else str(tool)
        args = _compact_json_field(item.get("arguments"), 420)
        result = _compact_json_field(item.get("result"), 600)
        error = find_text(item.get("error"))
        parts = [title]
        if args:
            parts.append(f"args: {args}")
        if result:
            parts.append(f"result: {result}")
        if error:
            parts.append(f"error: {error}")
        return "\n".join(parts)

    if item_type in {"dynamicToolCall", "dynamic-tool-call"}:
        namespace = item.get("namespace")
        tool = item.get("tool") or item.get("name") or "tool"
        title = f"{namespace}.{tool}" if namespace else str(tool)
        args = _compact_json_field(item.get("arguments"), 420)
        content = _compact_json_field(item.get("contentItems") or item.get("content_items"), 600)
        parts = [title]
        if args:
            parts.append(f"args: {args}")
        if content:
            parts.append(f"output: {content}")
        return "\n".join(parts)

    if item_type == "collabAgentToolCall":
        tool = item.get("tool") or "collab"
        prompt = compact_text(item.get("prompt") or "", 600)
        receivers = item.get("receiverThreadIds") or item.get("receiver_thread_ids") or []
        return "\n".join(part for part in (str(tool), f"prompt: {prompt}" if prompt else "", f"threads: {', '.join(map(str, receivers))}" if receivers else "") if part)

    if item_type == "imageGeneration":
        prompt = compact_text(item.get("revisedPrompt") or item.get("revised_prompt") or "", 600)
        saved = item.get("savedPath") or item.get("saved_path")
        result = item.get("result")
        return "\n".join(part for part in ("image generation", f"prompt: {prompt}" if prompt else "", f"saved: {saved}" if saved else "", f"result: {compact_text(result, 600)}" if result else "") if part)

    return summarize_item(item, 1000)


def _compact_json_field(value: Any, limit: int) -> str:
    if value is None:
        return ""
    text = find_text(value, limit)
    if not text:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    return compact_text(text, limit)


def _tool_action_text(value: Any) -> str:
    action = value
    if isinstance(action, dict) and isinstance(action.get("root"), dict):
        action = action["root"]
    if isinstance(action, dict):
        for key in ("query", "queries", "url", "pattern"):
            text = find_text(action.get(key))
            if text:
                return text
    return find_text(value)


def _diff_summary(diff: str) -> str:
    if not diff:
        return ""
    added = 0
    removed = 0
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
        elif line.startswith("-"):
            removed += 1
    parts = []
    if added:
        parts.append(f"+{added}")
    if removed:
        parts.append(f"-{removed}")
    return " ".join(parts)


def summary_from_sdk_thread(thread: dict[str, Any]) -> ThreadSummary:
    updated = thread.get("updatedAt")
    active_at = None
    if isinstance(updated, (int, float)):
        active_at = float(updated)
    status = thread.get("status")
    if isinstance(status, dict):
        runtime = str(status.get("type") or status.get("status") or status.get("root") or "unknown")
    else:
        runtime = str(status or "unknown")
    latest_model = thread.get("latestModel") or thread.get("latest_model")
    latest_reasoning_effort = thread.get("latestReasoningEffort") or thread.get("latest_reasoning_effort")
    turns = thread.get("turns")
    state_like = {
        "turns": turns if isinstance(turns, list) else [],
        "latestModel": latest_model,
        "latestReasoningEffort": latest_reasoning_effort,
        "latestCollaborationMode": thread.get("latestCollaborationMode") or thread.get("latest_collaboration_mode"),
        "currentPermissions": thread.get("currentPermissions") or thread.get("current_permissions"),
    }
    latest_model, latest_reasoning_effort = model_settings_from_state(state_like)
    approval_policy, sandbox_mode = permission_settings_from_state(state_like)
    return ThreadSummary(
        conversation_id=str(thread.get("id") or ""),
        title=compact_text(thread.get("name") or thread.get("title") or thread.get("preview") or "(untitled)", 90),
        cwd=str(thread.get("cwd") or ""),
        source="history-only",
        runtime_status=runtime,
        latest_turn_status="-",
        latest_item_preview=compact_text(thread.get("preview") or "", 180),
        active_at=active_at,
        updated_at=active_at,
        has_live_owner=False,
        latest_model=_string_or_none(latest_model),
        latest_reasoning_effort=_string_or_none(latest_reasoning_effort),
        approval_policy=approval_policy,
        sandbox_mode=sandbox_mode,
    )


def safe_json_dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def model_settings_from_state(state: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if not isinstance(state, dict):
        return None, None
    model = state.get("latestModel") or state.get("latest_model")
    effort = state.get("latestReasoningEffort") or state.get("latest_reasoning_effort")
    collaboration_mode = state.get("latestCollaborationMode") or state.get("latest_collaboration_mode")
    settings = collaboration_mode.get("settings") if isinstance(collaboration_mode, dict) else None
    if isinstance(settings, dict):
        model = model or settings.get("model")
        effort = effort or settings.get("reasoning_effort") or settings.get("reasoningEffort")
    turn = latest_turn(state)
    params = turn.get("params") if isinstance(turn, dict) and isinstance(turn.get("params"), dict) else None
    if isinstance(params, dict):
        model = model or params.get("model")
        effort = effort or params.get("reasoningEffort") or params.get("reasoning_effort") or params.get("effort")
    return _string_or_none(model), _string_or_none(effort)


def permission_settings_from_state(state: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if not isinstance(state, dict):
        return None, None
    current = state.get("currentPermissions") or state.get("current_permissions")
    current_permissions = current if isinstance(current, dict) else {}
    approval_policy = current_permissions.get("approvalPolicy") or current_permissions.get("approval_policy")
    sandbox_policy = current_permissions.get("sandboxPolicy") or current_permissions.get("sandbox_policy")
    turn = latest_turn(state)
    params = turn.get("params") if isinstance(turn, dict) and isinstance(turn.get("params"), dict) else None
    if isinstance(params, dict):
        approval_policy = approval_policy or params.get("approvalPolicy") or params.get("approval_policy")
        sandbox_policy = sandbox_policy or params.get("sandboxPolicy") or params.get("sandbox_policy")
    return _string_or_none(approval_policy), sandbox_mode_from_policy(sandbox_policy)


def sandbox_mode_from_policy(policy: Any) -> str | None:
    if isinstance(policy, str):
        return policy
    if not isinstance(policy, dict):
        return None
    policy_type = policy.get("type")
    if policy_type in {"readOnly", "read-only", "read_only"}:
        return "read-only"
    if policy_type in {"workspaceWrite", "workspace-write", "workspace_write"}:
        return "workspace-write-network" if policy.get("networkAccess") or policy.get("network_access") else "workspace-write"
    if policy_type in {"dangerFullAccess", "danger-full-access", "danger_full_access"}:
        return "danger-full-access"
    return _string_or_none(policy_type)


def _string_or_none(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value or None
