from __future__ import annotations

import asyncio
import ctypes
import ctypes.wintypes
import json
import os
import socket
import struct
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, BinaryIO, Callable

from .models import IpcStatus


DEFAULT_CLIENT_TYPE = "codex-webui-server"

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


def ipc_error_code(exc: BaseException) -> int | None:
    for attr in ("winerror", "errno"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    return None


def is_ipc_disconnect_error(exc: BaseException) -> bool:
    if isinstance(exc, (EOFError, ConnectionResetError, BrokenPipeError)):
        return True
    return ipc_error_code(exc) in {2, 109, 231, 232, 233}


def format_ipc_error(path: str, exc: BaseException, phase: str) -> str:
    code = ipc_error_code(exc)
    code_text = f" Windows error={code}." if code is not None else ""
    if is_ipc_disconnect_error(exc):
        return (
            f"{phase} IPC failed: {path}. {exc}{code_text} "
            "Codex App or VSCode Codex extension is probably not running."
        )
    return f"{phase} IPC failed: {path}. {exc}{code_text}"


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
        if os.name != "nt" or self._file is None or _kernel32 is None:
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
            raise OSError(ctypes.get_last_error(), "PeekNamedPipe failed")
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


@dataclass
class PendingRequest:
    method: str
    event: threading.Event = field(default_factory=threading.Event)
    response: dict[str, Any] | None = None


class IpcClient:
    def __init__(
        self,
        *,
        path: str | None = None,
        client_type: str = DEFAULT_CLIENT_TYPE,
        reconnect_interval: float = 2.0,
        on_message: Callable[[dict[str, Any]], None] | None = None,
        on_status: Callable[[IpcStatus], None] | None = None,
        on_raw_message: Callable[[str, dict[str, Any]], None] | None = None,
    ):
        self.path = path or default_ipc_path()
        self.client_type = client_type
        self.reconnect_interval = reconnect_interval
        self.on_message = on_message
        self.on_status = on_status
        self.on_raw_message = on_raw_message
        self.status = IpcStatus()
        self.client_id: str | None = None
        self._transport = IpcTransport(self.path)
        self._pending: dict[str, PendingRequest] = {}
        self._pending_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start_background(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run_loop, name="codex-webui-ipc", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._transport.close()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.connect()
                self._read_loop()
            except Exception as exc:
                self._mark_offline(format_ipc_error(self.path, exc, "connect/read"))
                time.sleep(self.reconnect_interval)

    def connect(self) -> None:
        self._transport = IpcTransport(self.path)
        self._transport.connect()
        request_id = str(uuid.uuid4())
        self._write_message(
            {
                "type": "request",
                "requestId": request_id,
                "sourceClientId": "initializing-client",
                "version": 0,
                "method": "initialize",
                "params": {"clientType": self.client_type},
                "targetClientId": None,
            }
        )
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            message = self._transport.read_message(timeout=max(0.05, deadline - time.monotonic()))
            self._emit_raw_message("in", message)
            if message.get("type") == "response" and message.get("method") == "initialize":
                result = message.get("result")
                self.client_id = result.get("clientId") if isinstance(result, dict) else None
                self.status = IpcStatus(
                    online=True,
                    client_id=self.client_id,
                    connected_at=time.time(),
                    last_error=None,
                    last_seen_at=time.time(),
                )
                self._emit_status()
                return
            self._handle_message(message)
        raise TimeoutError("Timed out waiting for IPC initialize response")

    def _read_loop(self) -> None:
        while not self._stop.is_set():
            try:
                message = self._transport.read_message(timeout=0.5)
            except TimeoutError:
                continue
            self.status.last_seen_at = time.time()
            self._emit_raw_message("in", message)
            self._handle_message(message)

    def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "client-discovery-request":
            self._write_message(
                {
                    "type": "client-discovery-response",
                    "requestId": message.get("requestId"),
                    "response": {"canHandle": False},
                }
            )
            return
        if message_type == "request":
            self._write_message(
                {
                    "type": "response",
                    "requestId": message.get("requestId"),
                    "resultType": "error",
                    "error": "codex-webui-server-is-not-owner",
                }
            )
            return
        if message_type == "response":
            request_id = str(message.get("requestId") or "")
            with self._pending_lock:
                pending = self._pending.get(request_id)
            if pending is not None:
                pending.response = message
                pending.event.set()
                return
        if self.on_message is not None:
            self.on_message(message)

    def request(self, method: str, params: dict[str, Any], *, version: int = 1, timeout: float = 45) -> dict[str, Any]:
        if not self.status.online or not self.client_id:
            raise RuntimeError("ipc_offline")
        request_id = str(uuid.uuid4())
        pending = PendingRequest(method=method)
        with self._pending_lock:
            self._pending[request_id] = pending
        try:
            self._write_message(
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
                self._pending.pop(request_id, None)

    async def request_async(self, method: str, params: dict[str, Any], *, version: int = 1, timeout: float = 45) -> dict[str, Any]:
        return await asyncio.to_thread(self.request, method, params, version=version, timeout=timeout)

    def broadcast(self, method: str, params: dict[str, Any], *, version: int = 1) -> None:
        if not self.status.online or not self.client_id:
            raise RuntimeError("ipc_offline")
        self._write_message(
            {
                "type": "broadcast",
                "sourceClientId": self.client_id,
                "version": version,
                "method": method,
                "params": params,
            }
        )

    async def broadcast_async(self, method: str, params: dict[str, Any], *, version: int = 1) -> None:
        await asyncio.to_thread(self.broadcast, method, params, version=version)

    def send_event(self, event: dict[str, Any]) -> None:
        if not self.status.online or not self.client_id:
            raise RuntimeError("ipc_offline")
        self._write_message(event)

    async def send_event_async(self, event: dict[str, Any]) -> None:
        await asyncio.to_thread(self.send_event, event)

    def _write_message(self, message: dict[str, Any]) -> None:
        self._transport.write_message(message)
        self._emit_raw_message("out", message)

    def _emit_raw_message(self, direction: str, message: dict[str, Any]) -> None:
        if self.on_raw_message is None:
            return
        try:
            self.on_raw_message(direction, message)
        except Exception:
            return

    def _mark_offline(self, error: str) -> None:
        self._transport.close()
        self.client_id = None
        self.status.online = False
        self.status.client_id = None
        self.status.last_error = error
        self.status.last_seen_at = time.time()
        with self._pending_lock:
            for pending in self._pending.values():
                pending.response = {"type": "response", "resultType": "error", "error": "ipc_offline"}
                pending.event.set()
            self._pending.clear()
        self._emit_status()

    def _emit_status(self) -> None:
        if self.on_status is not None:
            self.on_status(self.status)

