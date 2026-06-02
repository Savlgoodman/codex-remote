#!/usr/bin/env python
"""Interactive Codex IPC thread watcher.

This script connects to Codex's local IPC channel and watches thread state
broadcasts from Codex Desktop / VSCode. It is read-only by default: it responds
to discovery requests with canHandle=false, so it will not take over any
conversation. Passing --enable-control exposes explicit follower requests such
as thread-follower-start-turn for controlled experiments.
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import os
import socket
import struct
import sys
import threading
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, BinaryIO


DEFAULT_CLIENT_TYPE = "codex-remote-python-watch"


if os.name == "nt":
    import msvcrt

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel32.PeekNamedPipe.argtypes = [
        ctypes.wintypes.HANDLE,
        ctypes.c_void_p,
        ctypes.wintypes.DWORD,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.wintypes.DWORD),
        ctypes.c_void_p,
    ]
    _kernel32.PeekNamedPipe.restype = ctypes.wintypes.BOOL
else:
    msvcrt = None
    _kernel32 = None


def default_ipc_path() -> str:
    if os.name == "nt":
        return r"\\.\pipe\codex-ipc"
    uid = os.getuid() if hasattr(os, "getuid") else "unknown"
    return os.path.join("/tmp", "codex-ipc", f"ipc-{uid}.sock")


def now_label() -> str:
    return datetime.now().strftime("%H:%M:%S")


def ipc_error_code(exc: BaseException) -> int | None:
    for attr in ("winerror", "errno"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def is_ipc_disconnect_error(exc: BaseException) -> bool:
    if isinstance(exc, (EOFError, ConnectionResetError, BrokenPipeError)):
        return True
    code = ipc_error_code(exc)
    return code in {2, 109, 231, 232, 233}


def format_ipc_error(path: str, exc: BaseException, *, phase: str) -> str:
    code = ipc_error_code(exc)
    code_text = f" Windows error={code}." if code is not None else ""
    if is_ipc_disconnect_error(exc):
        return (
            f"{phase} IPC 通道失败：{path}\n"
            f"  {exc}{code_text}\n"
            "  这通常表示 Codex App 和 VSCode Codex 插件都没有运行，"
            "或者 owner UI 刚关闭导致 named pipe 被系统断开。\n"
            "  监听和控制都需要至少一个 App/VSCode owner 正在运行；"
            "请启动 Codex App 或 VSCode 插件后重试。"
        )
    return f"{phase} IPC 通道失败：{path}\n  {exc}{code_text}"


def compact_text(value: Any, limit: int = 140) -> str:
    text = "" if value is None else str(value)
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "..."


def find_text(value: Any, limit: int = 220) -> str:
    """Best-effort extraction of human text from nested content structures."""
    if value is None:
        return ""
    if isinstance(value, str):
        return compact_text(value, limit)
    if isinstance(value, list):
        parts = [find_text(item, limit) for item in value]
        return compact_text(" ".join(part for part in parts if part), limit)
    if isinstance(value, dict):
        for key in ("text", "message", "input", "content", "value"):
            if key in value:
                text = find_text(value.get(key), limit)
                if text:
                    return compact_text(text, limit)
    return ""


def summarize_item(item: Any) -> str:
    if not isinstance(item, dict):
        return compact_text(item)

    item_type = item.get("type") or item.get("kind") or "item"

    if item_type in {"userMessage", "user-message", "user_message"}:
        text = find_text(item.get("content") or item.get("message") or item.get("input"))
        return f"user: {text}" if text else "user"

    if item_type in {"agentMessage", "agent-message", "assistantMessage"}:
        text = find_text(item.get("text") or item.get("content") or item.get("message"))
        return f"agent: {text}" if text else "agent"

    if item_type in {"steeringUserMessage", "steering-user-message"}:
        text = find_text(item.get("input") or item.get("content"))
        status = item.get("status")
        suffix = f" [{status}]" if status else ""
        return f"steer: {text}{suffix}" if text else f"steer{suffix}"

    if item_type in {"commandExecution", "command-execution"}:
        command = compact_text(item.get("command") or item.get("cmd") or "", 100)
        status = item.get("status") or "unknown"
        return f"command:{status} {command}".strip()

    if item_type in {"mcpToolCall", "mcp-tool-call", "toolCall"}:
        name = item.get("name") or item.get("toolName") or item.get("method") or ""
        status = item.get("status") or ""
        return compact_text(f"{item_type}:{status} {name}".strip())

    if item_type == "reasoning":
        return "reasoning"

    text = find_text(item)
    return compact_text(f"{item_type}: {text}" if text else str(item_type))


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
        return items[-1]
    return None


def thread_title(state: dict[str, Any] | None) -> str:
    if not isinstance(state, dict):
        return ""
    return compact_text(state.get("title") or state.get("preview") or "(untitled)", 70)


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


def short_conversation_id(conversation_id: str) -> str:
    if len(conversation_id) <= 12:
        return conversation_id
    return conversation_id[:8] + "..." + conversation_id[-4:]


def patch_path_label(path: Any) -> str:
    if isinstance(path, list):
        return "/" + "/".join(str(part) for part in path)
    return str(path or "/")


def get_patch_parent(root: Any, path: list[Any]) -> tuple[Any, Any] | None:
    if not path:
        return None
    current = root
    for part in path[:-1]:
        if isinstance(current, list):
            if not isinstance(part, int) or part < 0 or part >= len(current):
                return None
            current = current[part]
            continue
        if isinstance(current, dict):
            if part not in current:
                return None
            current = current[part]
            continue
        return None
    return current, path[-1]


def apply_patch_list(state: dict[str, Any] | None, patches: Any) -> dict[str, Any] | None:
    """Apply the Immer-style patches Codex broadcasts for conversation state."""
    if not isinstance(state, dict) or not isinstance(patches, list):
        return state
    next_state: Any = deepcopy(state)
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        op = patch.get("op")
        path = patch.get("path")
        if not isinstance(path, list):
            continue
        if not path:
            if op in {"add", "replace"}:
                value = patch.get("value")
                if isinstance(value, dict):
                    next_state = deepcopy(value)
            continue
        parent_pair = get_patch_parent(next_state, path)
        if parent_pair is None:
            continue
        parent, key = parent_pair
        if isinstance(parent, list):
            if key == "-":
                index = len(parent)
            elif isinstance(key, int):
                index = key
            else:
                continue
            if op == "add":
                if 0 <= index <= len(parent):
                    parent.insert(index, deepcopy(patch.get("value")))
            elif op == "replace":
                if 0 <= index < len(parent):
                    parent[index] = deepcopy(patch.get("value"))
            elif op == "remove":
                if 0 <= index < len(parent):
                    parent.pop(index)
            continue
        if isinstance(parent, dict):
            if op in {"add", "replace"}:
                parent[key] = deepcopy(patch.get("value"))
            elif op == "remove":
                parent.pop(key, None)
    return next_state if isinstance(next_state, dict) else state


def summarize_patch_value(value: Any) -> str:
    if isinstance(value, dict):
        item_type = value.get("type")
        if item_type:
            return summarize_item(value)
        for key in ("title", "cwd", "status", "message", "text", "input"):
            if key in value:
                text = find_text(value.get(key))
                if text:
                    return f"{key}: {text}"
        return compact_text(json.dumps(value, ensure_ascii=False), 160)
    if isinstance(value, list):
        return compact_text(f"list[{len(value)}]", 80)
    return compact_text(value, 160)


def summarize_patches(patches: Any, limit: int = 6) -> list[str]:
    if not isinstance(patches, list):
        return []
    lines: list[str] = []
    for patch in patches:
        if not isinstance(patch, dict):
            continue
        op = patch.get("op", "?")
        path = patch_path_label(patch.get("path"))
        value = patch.get("value")
        if op == "remove":
            lines.append(f"{op} {path}")
        else:
            lines.append(f"{op} {path} = {summarize_patch_value(value)}")
        if len(lines) >= limit:
            break
    if len(patches) > limit:
        lines.append(f"... {len(patches) - limit} more patch(es)")
    return lines


@dataclass
class ThreadSnapshot:
    conversation_id: str
    host_id: str | None = None
    state: dict[str, Any] | None = None
    change_type: str | None = None
    seen_at: float = field(default_factory=time.time)
    raw_params: dict[str, Any] | None = None
    last_patches: list[dict[str, Any]] | None = None

    def one_line(self) -> str:
        state = self.state or {}
        title = thread_title(state)
        cwd = thread_cwd(state)
        runtime = runtime_status(state)
        latest = summarize_item(latest_item(state))
        return (
            f"{short_conversation_id(self.conversation_id)} | "
            f"{runtime:<10} | turn={turn_status(state):<10} | "
            f"{title} | {compact_text(cwd, 55)} | {compact_text(latest, 100)}"
        )


@dataclass
class PendingIpcRequest:
    method: str
    event: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None


class IpcTransport:
    def __init__(self, path: str):
        self.path = path
        self._file: BinaryIO | None = None
        self._socket: socket.socket | None = None
        self._write_lock = threading.Lock()

    def connect(self) -> None:
        if os.name == "nt":
            self._file = open(self.path, "r+b", buffering=0)
            return

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.path)
        self._socket = sock

    def close(self) -> None:
        try:
            if self._file is not None:
                self._file.close()
        finally:
            self._file = None
        try:
            if self._socket is not None:
                self._socket.close()
        finally:
            self._socket = None

    def _windows_file_handle(self) -> int:
        if self._file is None:
            raise RuntimeError("Windows file transport is not connected")
        if msvcrt is None:
            raise RuntimeError("msvcrt is unavailable")
        handle = msvcrt.get_osfhandle(self._file.fileno())
        if int(handle) == -1:
            raise OSError("Failed to get Windows handle for IPC file")
        return int(handle)

    def bytes_available(self) -> int | None:
        if os.name != "nt" or self._file is None:
            return None
        if _kernel32 is None:
            return None
        available = ctypes.wintypes.DWORD(0)
        ok = _kernel32.PeekNamedPipe(
            ctypes.wintypes.HANDLE(self._windows_file_handle()),
            None,
            0,
            None,
            ctypes.byref(available),
            None,
        )
        if not ok:
            error = ctypes.get_last_error()
            raise OSError(error, "PeekNamedPipe failed")
        return int(available.value)

    def write_message(self, message: dict[str, Any]) -> None:
        payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        frame = struct.pack("<I", len(payload)) + payload
        with self._write_lock:
            if self._file is not None:
                self._file.write(frame)
                try:
                    self._file.flush()
                except Exception:
                    pass
                return
            if self._socket is not None:
                self._socket.sendall(frame)
                return
        raise RuntimeError("IPC transport is not connected")

    def read_exact(self, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining > 0:
            if self._file is not None:
                chunk = self._file.read(remaining)
            elif self._socket is not None:
                chunk = self._socket.recv(remaining)
            else:
                raise EOFError("IPC transport closed")
            if not chunk:
                raise EOFError("IPC channel closed")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def read_message(self, timeout: float | None = None) -> dict[str, Any]:
        if timeout is not None and os.name == "nt" and self._file is not None:
            deadline = time.monotonic() + timeout
            while True:
                available = self.bytes_available()
                if available is not None and available >= 4:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for IPC frame")
                time.sleep(0.05)
        header = self.read_exact(4)
        (length,) = struct.unpack("<I", header)
        if timeout is not None and os.name == "nt" and self._file is not None:
            deadline = time.monotonic() + timeout
            while True:
                available = self.bytes_available()
                if available is not None and available >= length:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError("Timed out waiting for IPC payload")
                time.sleep(0.05)
        payload = self.read_exact(length)
        return json.loads(payload.decode("utf-8"))


class CodexIpcWatcher:
    def __init__(self, path: str, client_type: str, raw: bool = False):
        self.path = path
        self.transport = IpcTransport(path)
        self.client_type = client_type
        self.raw = raw
        self.client_id: str | None = None
        self.threads: dict[str, ThreadSnapshot] = {}
        self.selected_conversation_id: str | None = None
        self.initialized = threading.Event()
        self.stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._print_lock = threading.Lock()
        self._pending_lock = threading.Lock()
        self._pending_requests: dict[str, PendingIpcRequest] = {}

    def start(self) -> None:
        self.transport = IpcTransport(self.path)
        self.client_id = None
        self.initialized.clear()
        self.stopped.clear()
        self.transport.connect()
        init_request_id = str(uuid.uuid4())
        self.transport.write_message(
            {
                "type": "request",
                "requestId": init_request_id,
                "sourceClientId": "initializing-client",
                "version": 0,
                "method": "initialize",
                "params": {"clientType": self.client_type},
                "targetClientId": None,
            }
        )
        deadline = time.monotonic() + 5
        while not self.initialized.is_set():
            remaining = max(0.05, deadline - time.monotonic())
            if time.monotonic() >= deadline:
                raise TimeoutError("Timed out waiting for initialize response")
            first_message = self.transport.read_message(timeout=remaining)
            self.handle_message(first_message)
        self._thread = threading.Thread(target=self._reader_loop, name="codex-ipc-reader", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.stopped.set()
        self.transport.close()

    def close_for_reconnect(self) -> None:
        self.stopped.set()
        self.transport.close()
        if self._thread is not None and self._thread.is_alive() and threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)
        self.client_id = None
        self.initialized.clear()
        with self._pending_lock:
            for pending in self._pending_requests.values():
                pending.response = {
                    "type": "response",
                    "resultType": "error",
                    "error": "ipc-disconnected",
                }
                pending.event.set()
            self._pending_requests.clear()

    def reconnect(self, *, interval: float = 2.0) -> None:
        attempt = 0
        while True:
            attempt += 1
            self.safe_print(f"[{now_label()}] 正在重连 IPC（第 {attempt} 次）...")
            try:
                self.close_for_reconnect()
                self.start()
                self.safe_print(
                    f"[{now_label()}] IPC 已重连。旧线程快照已保留；"
                    "等待 App/VSCode owner 重新广播最新状态。"
                )
                return
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                self.safe_print(format_ipc_error(self.path, exc, phase="重连"))
                time.sleep(max(0.2, interval))

    def _reader_loop(self) -> None:
        while not self.stopped.is_set():
            try:
                message = self.transport.read_message(timeout=0.5)
            except TimeoutError:
                continue
            except Exception as exc:
                if not self.stopped.is_set():
                    self.safe_print("\n" + format_ipc_error(self.path, exc, phase=f"[{now_label()}] 读取"))
                self.stopped.set()
                return
            self.handle_message(message)

    def safe_print(self, text: str) -> None:
        with self._print_lock:
            print(text, flush=True)

    def handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")

        if message_type == "client-discovery-request":
            self.transport.write_message(
                {
                    "type": "client-discovery-response",
                    "requestId": message.get("requestId"),
                    "response": {"canHandle": False},
                }
            )
            return

        if message_type == "request":
            self.transport.write_message(
                {
                    "type": "response",
                    "requestId": message.get("requestId"),
                    "resultType": "error",
                    "error": "codex-python-watcher-is-read-only",
                }
            )
            return

        if message_type == "response" and message.get("method") == "initialize":
            result = message.get("result")
            if isinstance(result, dict):
                self.client_id = result.get("clientId")
            self.initialized.set()
            self.safe_print(f"[{now_label()}] connected as {self.client_id or '(unknown client id)'}")
            return

        if message_type == "response":
            request_id = str(message.get("requestId") or "")
            with self._pending_lock:
                pending = self._pending_requests.get(request_id)
            if pending is not None:
                pending.response = message
                pending.event.set()
                return

        if message_type == "broadcast":
            self.handle_broadcast(message)
            return

        if self.raw:
            self.safe_print(json.dumps(message, ensure_ascii=False, indent=2))

    def handle_broadcast(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        params = message.get("params")

        if method == "thread-stream-state-changed" and isinstance(params, dict):
            conversation_id = str(params.get("conversationId") or "")
            if not conversation_id:
                return
            change = params.get("change") if isinstance(params.get("change"), dict) else {}
            change_type = change.get("type") if isinstance(change, dict) else None
            state = change.get("conversationState") if isinstance(change, dict) else None
            patches = change.get("patches") if isinstance(change, dict) else None
            previous = self.threads.get(conversation_id)
            next_state: dict[str, Any] | None
            if isinstance(state, dict):
                next_state = state
            elif change_type == "patches":
                next_state = apply_patch_list(previous.state if previous else None, patches)
            else:
                next_state = previous.state if previous else None
            snapshot = ThreadSnapshot(
                conversation_id=conversation_id,
                host_id=params.get("hostId") or (previous.host_id if previous else None),
                state=next_state,
                change_type=change_type,
                raw_params=params,
                last_patches=patches if isinstance(patches, list) else None,
            )
            self.threads[conversation_id] = snapshot
            if conversation_id == self.selected_conversation_id:
                self.print_selected_update(snapshot, message)
            return

        if self.raw and method:
            self.safe_print(json.dumps(message, ensure_ascii=False, indent=2))
            return

        selected = self.selected_conversation_id
        if not selected or not isinstance(params, dict):
            return
        if params.get("conversationId") == selected:
            self.safe_print(f"\n[{now_label()}] {method}: {json.dumps(params, ensure_ascii=False)}")

    def print_selected_update(self, snapshot: ThreadSnapshot, raw_message: dict[str, Any]) -> None:
        if self.raw:
            self.safe_print("\n" + json.dumps(raw_message, ensure_ascii=False, indent=2))
            return
        state = snapshot.state or {}
        latest = summarize_item(latest_item(state))
        title = thread_title(state)
        patch_lines = summarize_patches(snapshot.last_patches)
        patch_text = ""
        if patch_lines:
            patch_text = "\n  patches:\n" + "\n".join(f"    - {line}" for line in patch_lines)
        self.safe_print(
            "\n"
            f"[{now_label()}] {snapshot.change_type or 'change'} "
            f"{short_conversation_id(snapshot.conversation_id)} "
            f"runtime={runtime_status(state)} turn={turn_status(state)}\n"
            f"  title: {title}\n"
            f"  cwd:   {thread_cwd(state)}\n"
            f"  item:  {latest}"
            f"{patch_text}"
        )

    def sorted_threads(self) -> list[ThreadSnapshot]:
        return sorted(self.threads.values(), key=lambda item: item.seen_at, reverse=True)

    def request_ipc(self, method: str, params: dict[str, Any], *, version: int = 1, timeout: float = 30) -> dict[str, Any]:
        if not self.client_id:
            raise RuntimeError("IPC client is not initialized")
        request_id = str(uuid.uuid4())
        pending = PendingIpcRequest(method=method)
        with self._pending_lock:
            self._pending_requests[request_id] = pending
        try:
            self.transport.write_message(
                {
                    "type": "request",
                    "requestId": request_id,
                    "sourceClientId": self.client_id,
                    "version": version,
                    "method": method,
                    "params": params,
                    "targetClientId": None,
                }
            )
            if not pending.event.wait(timeout=timeout):
                raise TimeoutError(f"Timed out waiting for IPC response to {method}")
            if pending.response is None:
                raise RuntimeError(f"Missing IPC response to {method}")
            return pending.response
        finally:
            with self._pending_lock:
                self._pending_requests.pop(request_id, None)

    def build_turn_start_params(self, conversation_id: str, text: str) -> dict[str, Any]:
        snapshot = self.threads.get(conversation_id)
        if snapshot is None or not isinstance(snapshot.state, dict):
            raise RuntimeError("Missing conversation snapshot; cannot build turnStartParams")
        state = snapshot.state
        turn = latest_turn(state)
        latest_params = turn.get("params") if isinstance(turn, dict) else None
        params = deepcopy(latest_params) if isinstance(latest_params, dict) else {}
        current_permissions = state.get("currentPermissions") if isinstance(state.get("currentPermissions"), dict) else {}

        params["threadId"] = conversation_id
        params["input"] = [
            {
                "type": "text",
                "text": text if text.endswith("\n") else f"{text}\n",
                "text_elements": [],
            }
        ]
        params["cwd"] = params.get("cwd") or state.get("cwd") or ""
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

    def send_start_turn(self, conversation_id: str, text: str, *, timeout: float = 45) -> dict[str, Any]:
        turn_start_params = self.build_turn_start_params(conversation_id, text)
        return self.request_ipc(
            "thread-follower-start-turn",
            {
                "conversationId": conversation_id,
                "turnStartParams": turn_start_params,
            },
            version=1,
            timeout=timeout,
        )

    def print_thread_list(self) -> None:
        rows = self.sorted_threads()
        if not rows:
            self.safe_print("\n暂时没有收到线程快照。请确认 Codex App 或 VSCode Codex 插件正在打开，并稍后刷新。")
            return
        self.safe_print("\n最近从 codex-ipc 收到的线程：")
        for idx, snapshot in enumerate(rows, start=1):
            self.safe_print(f"{idx:>2}. {snapshot.one_line()}")

    def print_current_snapshot(self, conversation_id: str, max_items: int = 8) -> None:
        snapshot = self.threads.get(conversation_id)
        if snapshot is None:
            self.safe_print(
                f"没有找到线程：{conversation_id}\n"
                "提示：如果刚启动时没有收到该线程 snapshot，可以按 l 查看当前收到的线程，"
                "或用 --collect-seconds=8 延长初始收集时间。"
            )
            return
        state = snapshot.state or {}
        self.safe_print(
            "\n当前线程快照：\n"
            f"  conversationId: {snapshot.conversation_id}\n"
            f"  hostId:         {snapshot.host_id}\n"
            f"  title:          {thread_title(state)}\n"
            f"  cwd:            {thread_cwd(state)}\n"
            f"  runtime:        {runtime_status(state)}\n"
            f"  latest turn:    {turn_status(state)}"
        )

        turn = latest_turn(state)
        items = turn.get("items") if isinstance(turn, dict) else None
        if isinstance(items, list) and items:
            self.safe_print(f"  latest items:   last {min(max_items, len(items))}")
            for item in items[-max_items:]:
                self.safe_print(f"    - {summarize_item(item)}")

    def print_history(self, conversation_id: str) -> None:
        snapshot = self.threads.get(conversation_id)
        if snapshot is None or not isinstance(snapshot.state, dict):
            self.safe_print("当前没有可用的 conversationState，无法打印历史。")
            return
        turns = snapshot.state.get("turns")
        if not isinstance(turns, list) or not turns:
            self.safe_print("当前 snapshot 中没有 turns。")
            return
        self.safe_print(f"\n已加载历史：{len(turns)} turn(s)")
        for index, turn in enumerate(turns, start=1):
            if not isinstance(turn, dict):
                continue
            started = turn.get("turnStartedAtMs")
            status = turn.get("status") or "-"
            self.safe_print(f"\nTurn {index} | {status} | {turn.get('turnId') or '-'} | startedAtMs={started}")
            params = turn.get("params") if isinstance(turn.get("params"), dict) else {}
            input_text = find_text(params.get("input")) if isinstance(params, dict) else ""
            if input_text:
                self.safe_print(f"  params.input: {input_text}")
            items = turn.get("items")
            if isinstance(items, list):
                for item in items:
                    self.safe_print(f"  - {summarize_item(item)}")

    def dump_current_raw(self, conversation_id: str) -> None:
        snapshot = self.threads.get(conversation_id)
        if snapshot is None:
            self.safe_print(f"没有找到线程：{conversation_id}")
            return
        self.safe_print(json.dumps(snapshot.raw_params, ensure_ascii=False, indent=2))


def choose_thread(watcher: CodexIpcWatcher) -> str | None:
    while not watcher.stopped.is_set():
        watcher.print_thread_list()
        choice = input("\n选择线程序号，或输入 r 刷新 / q 退出：").strip().lower()
        if watcher.stopped.is_set():
            return None
        if choice in {"q", "quit", "exit"}:
            return None
        if choice in {"r", "refresh", ""}:
            time.sleep(2)
            continue
        rows = watcher.sorted_threads()
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(rows):
                return rows[idx - 1].conversation_id
        for snapshot in rows:
            if choice == snapshot.conversation_id.lower() or choice == short_conversation_id(snapshot.conversation_id).lower():
                return snapshot.conversation_id
        print("输入无效。")
    return None


def listen_loop(
    watcher: CodexIpcWatcher,
    conversation_id: str,
    *,
    enable_control: bool = False,
    control_timeout: float = 45,
) -> str:
    watcher.selected_conversation_id = conversation_id
    watcher.print_current_snapshot(conversation_id)
    commands = "s=显示快照，h=历史，raw=打印原始 payload，l=列出线程，b=返回选择，q=退出"
    if enable_control:
        commands += "，send <文本>=通过 IPC 发消息"
    print(
        "\n进入监听模式。现在去 Codex App 或 VSCode 插件里对这个线程发消息，"
        "这里会实时打印 IPC 同步过来的状态。\n"
        f"命令：{commands}。"
    )
    while not watcher.stopped.is_set():
        raw_command = input("\nwatch> ").strip()
        if watcher.stopped.is_set():
            return "disconnected"
        command = raw_command.lower()
        if command in {"q", "quit", "exit"}:
            watcher.stop()
            return "quit"
        if command in {"b", "back"}:
            watcher.selected_conversation_id = None
            return "back"
        if command in {"s", "show", ""}:
            watcher.print_current_snapshot(conversation_id)
            continue
        if command in {"raw", "dump"}:
            watcher.dump_current_raw(conversation_id)
            continue
        if command in {"h", "history"}:
            watcher.print_history(conversation_id)
            continue
        if command in {"l", "list"}:
            watcher.print_thread_list()
            continue
        if command.startswith("send "):
            text = raw_command[5:].strip()
            if not enable_control:
                print("控制发送未开启。请用 --enable-control 重新启动脚本后再试。")
                continue
            if not text:
                print("send 后面需要跟要发送的文本。")
                continue
            try:
                turn_start_params = watcher.build_turn_start_params(conversation_id, text)
            except Exception as exc:
                print(f"无法构造 turnStartParams：{exc}")
                continue
            print("\n即将通过 IPC 发送 thread-follower-start-turn：")
            print(f"  conversationId: {conversation_id}")
            print(f"  text:           {compact_text(text, 180)}")
            print(f"  cwd:            {turn_start_params.get('cwd')}")
            print(f"  approvalPolicy: {turn_start_params.get('approvalPolicy')}")
            print(f"  sandboxPolicy:  {json.dumps(turn_start_params.get('sandboxPolicy'), ensure_ascii=False)}")
            confirm = input("确认发送请输入 yes：").strip()
            if confirm != "yes":
                print("已取消。")
                continue
            try:
                response = watcher.send_start_turn(conversation_id, text, timeout=control_timeout)
                print("IPC response:")
                print(json.dumps(response, ensure_ascii=False, indent=2))
            except Exception as exc:
                print(f"发送失败：{exc}")
            continue
        print("未知命令。可用：s / raw / l / b / q")
    return "disconnected"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Watch Codex App/VSCode thread updates over codex-ipc.")
    parser.add_argument("--pipe", default=default_ipc_path(), help="IPC path. Default: %(default)s")
    parser.add_argument("--client-type", default=DEFAULT_CLIENT_TYPE, help="IPC client type label.")
    parser.add_argument("--collect-seconds", type=float, default=5.0, help="Seconds to collect snapshots before listing.")
    parser.add_argument("--conversation-id", help="Skip selection and watch this conversation id.")
    parser.add_argument("--raw", action="store_true", help="Print raw selected broadcasts instead of summaries.")
    parser.add_argument("--list-once", action="store_true", help="Collect, list threads, and exit.")
    parser.add_argument("--enable-control", action="store_true", help="Enable interactive send commands.")
    parser.add_argument("--control-timeout", type=float, default=45, help="Seconds to wait for control IPC responses.")
    parser.add_argument("--reconnect", action="store_true", help="Keep retrying when codex-ipc disappears.")
    parser.add_argument("--reconnect-interval", type=float, default=2.0, help="Seconds between reconnect attempts.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    watcher = CodexIpcWatcher(args.pipe, args.client_type, raw=args.raw)
    try:
        watcher.start()
    except Exception as exc:
        print(format_ipc_error(args.pipe, exc, phase="连接"), file=sys.stderr)
        if not args.reconnect:
            print("提示：如果你想让脚本等待 Codex App/VSCode 重新启动，可以加 --reconnect。", file=sys.stderr)
            return 1
        try:
            watcher.reconnect(interval=args.reconnect_interval)
        except KeyboardInterrupt:
            print("\n收到 Ctrl+C，退出。")
            watcher.stop()
            return 1

    if not watcher.initialized.wait(timeout=5):
        print("等待 IPC initialize 响应超时。", file=sys.stderr)
        watcher.stop()
        return 1

    if args.collect_seconds > 0:
        print(f"收集线程快照 {args.collect_seconds:g}s ...")
        time.sleep(args.collect_seconds)

    if args.list_once:
        watcher.print_thread_list()
        watcher.stop()
        return 0

    if args.conversation_id:
        while True:
            result = listen_loop(
                watcher,
                args.conversation_id,
                enable_control=args.enable_control,
                control_timeout=args.control_timeout,
            )
            if result != "disconnected" or not args.reconnect:
                break
            watcher.reconnect(interval=args.reconnect_interval)
        return 0

    try:
        while True:
            if watcher.stopped.is_set():
                if not args.reconnect:
                    break
                watcher.reconnect(interval=args.reconnect_interval)
            conversation_id = choose_thread(watcher)
            if conversation_id is None:
                if watcher.stopped.is_set() and args.reconnect:
                    continue
                break
            result = listen_loop(
                watcher,
                conversation_id,
                enable_control=args.enable_control,
                control_timeout=args.control_timeout,
            )
            if result == "disconnected" and args.reconnect:
                watcher.reconnect(interval=args.reconnect_interval)
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，退出。")
    finally:
        watcher.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
