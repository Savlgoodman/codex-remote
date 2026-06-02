#!/usr/bin/env python
"""Probe whether codex-ipc can wake a history-only thread.

This is intentionally experimental. It can send either:

* a raw codex-ipc broadcast that looks like a follower start-turn event; or
* a normal codex-ipc request for thread-follower-start-turn.

The goal is to test whether an App/VSCode IPC owner will pick up a
history-only conversation that it has not loaded. By default the script only
lists candidates and prints the payload. Use --send --yes to actually write to
the IPC pipe.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from copy import deepcopy
from dataclasses import dataclass
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

try:
    from openai_codex import Codex, CodexConfig
except ModuleNotFoundError as exc:
    if exc.name == "openai_codex":
        print("Missing dependency: openai-codex", file=sys.stderr)
        raise SystemExit(1) from exc
    raise


Json = dict[str, Any]


@dataclass
class SdkThread:
    index: int
    id: str
    title: str
    cwd: str
    status: str
    updated_at: float | None
    preview: str
    raw: Json
    live_seen: bool = False


def model_to_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value


def status_type(status: Any) -> str:
    data = model_to_json(status)
    if isinstance(data, dict):
        if "type" in data:
            return str(data["type"])
        root = data.get("root")
        if isinstance(root, dict):
            return status_type(root)
    return str(data or "unknown")


def list_sdk_threads(limit: int, cwd: str | None = None) -> list[SdkThread]:
    config = CodexConfig(cwd=cwd) if cwd else CodexConfig()
    with Codex(config=config) as codex:
        response = codex.thread_list(limit=limit)
    data = model_to_json(response)
    rows: list[SdkThread] = []
    for index, thread in enumerate(data.get("data", []) if isinstance(data, dict) else [], start=1):
        if not isinstance(thread, dict):
            continue
        thread_id = str(thread.get("id") or "")
        if not thread_id:
            continue
        rows.append(
            SdkThread(
                index=index,
                id=thread_id,
                title=compact_text(thread.get("name") or thread.get("title") or "(untitled)", 64),
                cwd=str(thread.get("cwd") or ""),
                status=status_type(thread.get("status")),
                updated_at=float(thread["updatedAt"]) if isinstance(thread.get("updatedAt"), (int, float)) else None,
                preview=compact_text(thread.get("preview") or "", 100),
                raw=thread,
            )
        )
    return rows


def read_sdk_thread(thread_id: str, cwd: str | None = None) -> Json:
    config = CodexConfig(cwd=cwd) if cwd else CodexConfig()
    with Codex(config=config) as codex:
        thread = codex.thread_resume(thread_id)
        response = thread.read(include_turns=True)
    data = model_to_json(response)
    if isinstance(data, dict):
        thread_data = data.get("thread")
        if isinstance(thread_data, dict):
            return thread_data
        return data
    return {}


def latest_turn(thread_data: Json) -> Json | None:
    turns = thread_data.get("turns")
    if isinstance(turns, list) and turns:
        turn = turns[-1]
        return turn if isinstance(turn, dict) else None
    return None


def build_turn_start_params(thread_id: str, text: str, thread_data: Json) -> Json:
    turn = latest_turn(thread_data)
    latest_params = turn.get("params") if isinstance(turn, dict) else None
    params = deepcopy(latest_params) if isinstance(latest_params, dict) else {}
    current_permissions = thread_data.get("currentPermissions")
    if not isinstance(current_permissions, dict):
        current_permissions = {}

    params["threadId"] = thread_id
    params["input"] = [
        {
            "type": "text",
            "text": text if text.endswith("\n") else f"{text}\n",
            "text_elements": [],
        }
    ]
    params["cwd"] = params.get("cwd") or thread_data.get("cwd") or ""
    params["attachments"] = []
    params["commentAttachments"] = []
    params["approvalPolicy"] = (
        params.get("approvalPolicy")
        or current_permissions.get("approvalPolicy")
        or "on-request"
    )
    params["approvalsReviewer"] = (
        params.get("approvalsReviewer")
        or current_permissions.get("approvalsReviewer")
        or "user"
    )
    params["sandboxPolicy"] = (
        params.get("sandboxPolicy")
        or current_permissions.get("sandboxPolicy")
        or {"type": "readOnly", "networkAccess": False}
    )
    params["collaborationMode"] = params.get("collaborationMode") or thread_data.get("latestCollaborationMode")
    params["model"] = params.get("model", None)
    params["effort"] = params.get("effort", None)
    params["serviceTier"] = params.get("serviceTier", None)
    params["summary"] = params.get("summary") or "none"
    params["personality"] = params.get("personality", None)
    params["outputSchema"] = params.get("outputSchema", None)
    return params


def connect_and_initialize(path: str, client_type: str) -> tuple[IpcTransport, str]:
    transport = IpcTransport(path)
    transport.connect()
    request_id = str(uuid.uuid4())
    transport.write_message(
        {
            "type": "request",
            "requestId": request_id,
            "sourceClientId": "initializing-client",
            "version": 0,
            "method": "initialize",
            "params": {"clientType": client_type},
            "targetClientId": None,
        }
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        message = transport.read_message(timeout=max(0.05, deadline - time.monotonic()))
        if message.get("type") == "response" and message.get("method") == "initialize":
            result = message.get("result")
            client_id = result.get("clientId") if isinstance(result, dict) else None
            if not client_id:
                raise RuntimeError(f"initialize did not return clientId: {message}")
            return transport, str(client_id)
        respond_to_discovery(transport, message)
    raise TimeoutError("Timed out waiting for IPC initialize response")


def collect_live_thread_ids(transport: IpcTransport, seconds: float) -> set[str]:
    deadline = time.monotonic() + max(0, seconds)
    live_ids: set[str] = set()
    while time.monotonic() < deadline:
        try:
            message = transport.read_message(timeout=max(0.05, min(0.5, deadline - time.monotonic())))
        except TimeoutError:
            continue
        respond_to_discovery(transport, message)
        if message.get("type") != "broadcast" or message.get("method") != "thread-stream-state-changed":
            continue
        params = message.get("params")
        if not isinstance(params, dict):
            continue
        conversation_id = str(params.get("conversationId") or "")
        if conversation_id:
            live_ids.add(conversation_id)
    return live_ids


def respond_to_discovery(transport: IpcTransport, message: Json) -> None:
    if message.get("type") == "client-discovery-request":
        transport.write_message(
            {
                "type": "client-discovery-response",
                "requestId": message.get("requestId"),
                "response": {"canHandle": False},
            }
        )
    elif message.get("type") == "request":
        transport.write_message(
            {
                "type": "response",
                "requestId": message.get("requestId"),
                "resultType": "error",
                "error": "history-broadcast-probe-is-not-owner",
            }
        )


def build_ipc_message(
    *,
    mode: str,
    client_id: str,
    conversation_id: str,
    turn_start_params: Json,
    broadcast_method: str,
) -> Json:
    params = {"conversationId": conversation_id, "turnStartParams": turn_start_params}
    if mode == "broadcast":
        return {
            "type": "broadcast",
            "sourceClientId": client_id,
            "version": 1,
            "method": broadcast_method,
            "params": params,
        }
    return {
        "type": "request",
        "requestId": str(uuid.uuid4()),
        "sourceClientId": client_id,
        "version": 1,
        "method": "thread-follower-start-turn",
        "params": params,
        "targetClientId": None,
    }


def summarize_ipc_message(message: Json, target_id: str) -> str | None:
    message_type = message.get("type")
    method = message.get("method")
    if message_type == "response":
        return (
            f"response method={method} resultType={message.get('resultType')} "
            f"handledBy={message.get('handledByClientId')} error={message.get('error')} "
            f"result={compact_text(json.dumps(message.get('result'), ensure_ascii=False), 220)}"
        )
    if message_type == "client-discovery-request":
        params = message.get("params")
        return f"client-discovery-request params={compact_text(json.dumps(params, ensure_ascii=False), 260)}"
    if message_type == "broadcast" and method == "thread-stream-state-changed":
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        conversation_id = str(params.get("conversationId") or "")
        if target_id and conversation_id != target_id:
            return None
        change = params.get("change") if isinstance(params.get("change"), dict) else {}
        state = change.get("conversationState") if isinstance(change, dict) else None
        if isinstance(state, dict):
            return (
                f"thread-stream-state-changed change={change.get('type')} "
                f"runtime={runtime_status(state)} turn={turn_status(state)} "
                f"title={compact_text(state.get('title') or state.get('preview'), 80)} "
                f"item={compact_text(summarize_item(_latest_item(state)), 120)}"
            )
        patches = change.get("patches") if isinstance(change, dict) else None
        return f"thread-stream-state-changed change={change.get('type')} patches={len(patches) if isinstance(patches, list) else 0}"
    if message_type == "broadcast":
        params = message.get("params")
        if isinstance(params, dict) and target_id and params.get("conversationId") != target_id:
            return None
        return f"broadcast {method} params={compact_text(json.dumps(params, ensure_ascii=False), 260)}"
    return f"{message_type} {method or ''}".strip()


def _latest_item(state: Json) -> Any:
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


def listen_after_send(transport: IpcTransport, target_id: str, seconds: float, output: Path | None) -> list[Json]:
    deadline = time.monotonic() + seconds
    seen: list[Json] = []
    while time.monotonic() < deadline:
        try:
            message = transport.read_message(timeout=max(0.05, min(0.5, deadline - time.monotonic())))
        except TimeoutError:
            continue
        respond_to_discovery(transport, message)
        seen.append(message)
        if output is not None:
            with output.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"seenAt": time.time(), "message": message}, ensure_ascii=False))
                handle.write("\n")
        summary = summarize_ipc_message(message, target_id)
        if summary:
            print(f"[ipc] {summary}")
    return seen


def print_candidates(rows: list[SdkThread]) -> None:
    print("SDK recent threads:")
    for row in rows:
        source = "live-seen" if row.live_seen else "history-only"
        print(
            f"{row.index:>2}. {source:<12} {compact_text(row.id, 28):<28} "
            f"{row.status:<12} {compact_text(row.title, 42):<42} "
            f"{compact_text(row.cwd, 46)}"
        )


def choose_candidate(rows: list[SdkThread], explicit_id: str | None, *, pick_first_history_only: bool) -> SdkThread:
    if explicit_id:
        for row in rows:
            if row.id == explicit_id:
                return row
        return SdkThread(0, explicit_id, "(explicit)", "", "unknown", None, "", {})
    if not rows:
        raise RuntimeError("No SDK threads found.")
    print_candidates(rows)
    if pick_first_history_only:
        for row in rows:
            if not row.live_seen:
                print(f"\nAuto-selected first history-only candidate: {row.index}. {row.id}")
                return row
        raise RuntimeError("No history-only SDK thread found after filtering live IPC snapshots.")
    while True:
        choice = input("Select a history-only candidate number, or q to quit: ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            raise SystemExit(0)
        if choice.isdigit():
            index = int(choice)
            for row in rows:
                if row.index == index:
                    return row
        print("Invalid selection.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe history-only wakeup over codex-ipc.")
    parser.add_argument("--conversation-id", help="Thread/conversation id to target.")
    parser.add_argument("--message", default="IPC history-only wakeup probe. Please respond with one short sentence.")
    parser.add_argument("--mode", choices=["broadcast", "request"], default="broadcast")
    parser.add_argument(
        "--broadcast-method",
        default="thread-follower-start-turn",
        help="Broadcast method to send in --mode broadcast.",
    )
    parser.add_argument("--limit", type=int, default=20, help="SDK thread list limit.")
    parser.add_argument("--cwd", help="Optional cwd for the SDK app-server.")
    parser.add_argument("--pipe", default=default_ipc_path())
    parser.add_argument("--client-type", default="codex-history-broadcast-probe")
    parser.add_argument("--listen-seconds", type=float, default=45)
    parser.add_argument("--collect-seconds", type=float, default=3, help="Seconds to collect IPC live ids before selecting.")
    parser.add_argument("--output", help="Optional JSONL capture path.")
    parser.add_argument("--list-only", action="store_true", help="List SDK threads marked by IPC live status, then exit.")
    parser.add_argument("--pick-first-history-only", action="store_true", help="Non-interactively select the first SDK thread not seen live over IPC.")
    parser.add_argument("--send", action="store_true", help="Actually write the probe IPC message.")
    parser.add_argument("--yes", action="store_true", help="Skip confirmation when --send is set.")
    parser.add_argument("--print-raw-payload", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    rows = list_sdk_threads(args.limit, cwd=args.cwd)

    transport: IpcTransport | None = None
    try:
        transport, client_id = connect_and_initialize(args.pipe, args.client_type)
    except Exception as exc:
        print(format_ipc_error(args.pipe, exc, phase="connect"), file=sys.stderr)
        return 1

    if args.collect_seconds > 0:
        print(f"Collecting IPC live thread ids for {args.collect_seconds:g}s ...")
        live_ids = collect_live_thread_ids(transport, args.collect_seconds)
        for row in rows:
            row.live_seen = row.id in live_ids
        print(f"IPC live ids seen: {len(live_ids)}")

    if args.list_only:
        print_candidates(rows)
        transport.close()
        return 0

    candidate = choose_candidate(rows, args.conversation_id, pick_first_history_only=args.pick_first_history_only)
    print()
    print(f"Target: {candidate.id}")
    print(f"Title:  {candidate.title}")
    print(f"Status: {candidate.status}")
    print(f"Cwd:    {candidate.cwd or '-'}")

    print("\nReading thread through SDK for latest turn params...")
    thread_data = read_sdk_thread(candidate.id, cwd=args.cwd)
    turns = thread_data.get("turns") if isinstance(thread_data.get("turns"), list) else []
    print(f"Loaded turns: {len(turns)}")
    latest = latest_turn(thread_data)
    if isinstance(latest, dict):
        print(f"Latest turn status: {latest.get('status') or '-'}")
        print(f"Latest turn input:  {compact_text(find_text((latest.get('params') or {}).get('input')), 160)}")

    turn_start_params = build_turn_start_params(candidate.id, args.message, thread_data)
    print("\nTurnStartParams summary:")
    print(f"  input:          {compact_text(args.message, 180)}")
    print(f"  cwd:            {turn_start_params.get('cwd') or '-'}")
    print(f"  approvalPolicy: {turn_start_params.get('approvalPolicy')}")
    print(f"  sandboxPolicy:  {json.dumps(turn_start_params.get('sandboxPolicy'), ensure_ascii=False)}")

    output = Path(args.output) if args.output else None
    if output is not None and not output.is_absolute():
        output = Path.cwd() / output

    message = build_ipc_message(
        mode=args.mode,
        client_id=client_id,
        conversation_id=candidate.id,
        turn_start_params=turn_start_params,
        broadcast_method=args.broadcast_method,
    )

    print()
    print(f"IPC clientId: {client_id}")
    print(f"Probe mode:   {args.mode}")
    if args.print_raw_payload:
        print(json.dumps(message, ensure_ascii=False, indent=2))

    if not args.send:
        print("\nDry run only. Add --send --yes to write this IPC message.")
        transport.close()
        return 0

    if not args.yes:
        confirm = input("This will write to codex-ipc. Type yes to continue: ").strip()
        if confirm != "yes":
            print("Cancelled.")
            transport.close()
            return 0

    print("\nWriting IPC probe message...")
    transport.write_message(message)
    if output is not None:
        with output.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"sentAt": time.time(), "message": message}, ensure_ascii=False))
            handle.write("\n")

    print(f"Listening {args.listen_seconds:g}s for responses/broadcasts...")
    seen = listen_after_send(transport, candidate.id, args.listen_seconds, output)
    transport.close()

    relevant = [item for item in seen if summarize_ipc_message(item, candidate.id)]
    print()
    print(f"Done. Frames seen: {len(seen)}, relevant frames printed: {len(relevant)}")
    if output is not None:
        print(f"Raw capture: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
