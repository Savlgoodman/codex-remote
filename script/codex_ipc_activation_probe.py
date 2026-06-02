#!/usr/bin/env python
"""Probe codex-ipc events while a thread is opened in Codex App / VSCode."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

from codex_ipc_thread_watch import (  # noqa: E402
    IpcTransport,
    compact_text,
    default_ipc_path,
    find_text,
    format_ipc_error,
    runtime_status,
    summarize_item,
    turn_status,
)


def now_label() -> str:
    return time.strftime("%H:%M:%S")


def summarize_payload(message: dict[str, Any]) -> str:
    message_type = message.get("type")
    method = message.get("method")
    request_id = message.get("requestId")
    prefix = f"{message_type}"
    if method:
        prefix += f" {method}"
    if request_id:
        prefix += f" requestId={compact_text(request_id, 16)}"

    params = message.get("params")
    if isinstance(params, dict):
        conversation_id = params.get("conversationId")
        if conversation_id:
            prefix += f" conversationId={compact_text(conversation_id, 22)}"

    if message_type == "client-discovery-request":
        return f"{prefix} params={compact_text(json.dumps(params, ensure_ascii=False), 260)}"

    if message_type == "client-discovery-response":
        return f"{prefix} response={compact_text(json.dumps(message.get('response'), ensure_ascii=False), 220)}"

    if message_type == "response":
        result = message.get("result")
        error = message.get("error")
        handled_by = message.get("handledByClientId")
        return f"{prefix} resultType={message.get('resultType')} handledBy={handled_by} result={compact_text(json.dumps(result, ensure_ascii=False), 220)} error={error}"

    if message_type == "broadcast" and method == "thread-stream-state-changed" and isinstance(params, dict):
        change = params.get("change") if isinstance(params.get("change"), dict) else {}
        change_type = change.get("type")
        state = change.get("conversationState") if isinstance(change, dict) else None
        if isinstance(state, dict):
            latest = summarize_item(_latest_item(state))
            return (
                f"{prefix} change={change_type} hostId={params.get('hostId')} "
                f"runtime={runtime_status(state)} turn={turn_status(state)} "
                f"title={compact_text(state.get('title') or state.get('preview'), 70)} "
                f"item={compact_text(latest, 140)}"
            )
        patches = change.get("patches") if isinstance(change, dict) else None
        return f"{prefix} change={change_type} patches={len(patches) if isinstance(patches, list) else 0}"

    if message_type == "broadcast" and method == "thread-read-state-changed" and isinstance(params, dict):
        return f"{prefix} params={compact_text(json.dumps(params, ensure_ascii=False), 300)}"

    if message_type == "broadcast" and method == "client-status-changed":
        return f"{prefix} params={compact_text(json.dumps(params, ensure_ascii=False), 220)}"

    if isinstance(params, dict):
        text = find_text(params, 220)
        return f"{prefix} params={compact_text(json.dumps(params, ensure_ascii=False), 260)} text={compact_text(text, 160)}"

    return compact_text(json.dumps(message, ensure_ascii=False), 320)


def _latest_item(state: dict[str, Any]) -> Any:
    turns = state.get("turns")
    if not isinstance(turns, list) or not turns:
        return None
    turn = turns[-1]
    if not isinstance(turn, dict):
        return None
    items = turn.get("items")
    if not isinstance(items, list) or not items:
        return None
    return items[-1]


def write_frame(output: Path, message: dict[str, Any]) -> None:
    with output.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"seenAt": time.time(), "message": message}, ensure_ascii=False))
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Listen to codex-ipc while opening a history-only thread.")
    parser.add_argument("--pipe", default=default_ipc_path())
    parser.add_argument("--seconds", type=float, default=15)
    parser.add_argument("--output", default=None, help="Raw JSONL output path.")
    parser.add_argument("--client-type", default="codex-ipc-activation-probe")
    parser.add_argument("--respond-discovery", action="store_true", help="Respond canHandle=false to discovery requests.")
    args = parser.parse_args()

    output = Path(args.output) if args.output else Path(f"codex-ipc-activation-{int(time.time())}.jsonl")
    if not output.is_absolute():
        output = Path.cwd() / output
    try:
        output.unlink(missing_ok=True)
    except TypeError:
        if output.exists():
            output.unlink()

    transport = IpcTransport(args.pipe)
    try:
        transport.connect()
        request_id = f"activation-probe-{int(time.time() * 1000)}"
        transport.write_message(
            {
                "type": "request",
                "requestId": request_id,
                "sourceClientId": "initializing-client",
                "version": 0,
                "method": "initialize",
                "params": {"clientType": args.client_type},
                "targetClientId": None,
            }
        )
    except Exception as exc:
        print(format_ipc_error(args.pipe, exc, phase="连接"), file=sys.stderr)
        return 1

    print(f"监听 codex-ipc {args.seconds:g}s。现在请在 Codex App / VSCode 里点击一个 history-only 线程。")
    print(f"raw JSONL: {output}")
    print()

    deadline = time.monotonic() + args.seconds
    client_id: str | None = None
    count = 0
    try:
        while time.monotonic() < deadline:
            try:
                message = transport.read_message(timeout=max(0.05, min(0.5, deadline - time.monotonic())))
            except TimeoutError:
                continue
            count += 1
            write_frame(output, message)
            if message.get("type") == "response" and message.get("method") == "initialize":
                result = message.get("result")
                if isinstance(result, dict):
                    client_id = result.get("clientId")
            if args.respond_discovery and message.get("type") == "client-discovery-request":
                transport.write_message(
                    {
                        "type": "client-discovery-response",
                        "requestId": message.get("requestId"),
                        "response": {"canHandle": False},
                    }
                )
            print(f"[{now_label()}] {summarize_payload(message)}")
    except KeyboardInterrupt:
        print("\n收到 Ctrl+C，停止。")
    finally:
        transport.close()

    print()
    print(f"完成。frames={count}, clientId={client_id or '-'}")
    print(f"raw JSONL: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
