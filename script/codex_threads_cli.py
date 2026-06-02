#!/usr/bin/env python
"""Interactive probe for controlling local Codex SDK threads.

Install dependency:
    python -m pip install openai-codex

This script talks to the local Codex app-server over the Python SDK. It can
list recent local threads, read completed threads, start new turns, and control
turns that this CLI starts.
"""

from __future__ import annotations

import argparse
import json
import queue
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any

try:
    from openai_codex import Codex, CodexConfig, Sandbox
    from openai_codex.generated.v2_all import (
        AgentMessageDeltaNotification,
        CommandExecutionThreadItem,
        ItemCompletedNotification,
        ItemStartedNotification,
        ThreadTokenUsageUpdatedNotification,
        TurnCompletedNotification,
        TurnPlanUpdatedNotification,
    )
except ModuleNotFoundError as exc:
    if exc.name == "openai_codex":
        print("Missing dependency: openai-codex")
        print()
        print("Install it with:")
        print("  python -m pip install openai-codex")
        print()
        print("Or use the probe venv created earlier:")
        print(r"  %TEMP%\codex-sdk-probe\venv\Scripts\python.exe scripts\codex_threads_cli.py")
        raise SystemExit(1) from exc
    raise


Json = dict[str, Any]


@dataclass
class ThreadRow:
    index: int
    id: str
    name: str
    cwd: str
    preview: str
    status: str
    updated_at: str
    source: str
    raw: Json


@dataclass
class ManagedTurn:
    thread_id: str
    turn_id: str
    handle: Any
    done: threading.Event
    events: "queue.Queue[Any]"
    error: BaseException | None = None


managed_turns: dict[str, ManagedTurn] = {}


def model_to_json(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return value


def one_line(text: str | None, limit: int = 80) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "..."


def status_type(status: Any) -> str:
    data = model_to_json(status)
    if isinstance(data, dict):
        if "type" in data:
            flags = data.get("activeFlags")
            if flags:
                return f"{data['type']}({','.join(map(str, flags))})"
            return str(data["type"])
        root = data.get("root")
        if isinstance(root, dict):
            return status_type(root)
    return str(data)


def status_label(status: str) -> str:
    if status.startswith("active"):
        return "运行中"
    if status == "idle":
        return "已结束/空闲"
    if status == "notLoaded":
        return "未加载/历史"
    if status == "systemError":
        return "错误"
    return status


def is_active(status: str) -> bool:
    return status.startswith("active")


def fmt_timestamp(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return datetime.fromtimestamp(int(value)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def make_rows(resp: Any) -> list[ThreadRow]:
    data = model_to_json(resp)
    rows: list[ThreadRow] = []
    for idx, thread in enumerate(data.get("data", []), start=1):
        status = status_type(thread.get("status"))
        name = thread.get("name") or "(unnamed)"
        rows.append(
            ThreadRow(
                index=idx,
                id=thread.get("id", ""),
                name=one_line(name, 36),
                cwd=one_line(thread.get("cwd"), 42),
                preview=one_line(thread.get("preview"), 72),
                status=status,
                updated_at=fmt_timestamp(thread.get("updatedAt")),
                source=str(thread.get("source") or "-"),
                raw=thread,
            )
        )
    return rows


def print_threads(rows: list[ThreadRow]) -> None:
    print()
    print("Recent Codex threads")
    print("=" * 120)
    print(f"{'#':>2}  {'state':<12} {'updated':<19} {'name':<36} {'cwd':<42} preview")
    print("-" * 120)
    for row in rows:
        print(
            f"{row.index:>2}  {status_label(row.status):<12} "
            f"{row.updated_at:<19} {row.name:<36} {row.cwd:<42} {row.preview}"
        )
    print("-" * 120)


def choose_row(rows: list[ThreadRow]) -> ThreadRow | None:
    while True:
        choice = input("Select thread number, r=refresh, q=quit: ").strip().lower()
        if choice in {"q", "quit", "exit"}:
            raise KeyboardInterrupt
        if choice in {"r", "refresh", ""}:
            return None
        if choice.isdigit():
            index = int(choice)
            for row in rows:
                if row.index == index:
                    return row
        print("Invalid selection.")


def show_thread_details(row: ThreadRow) -> None:
    print()
    print(f"Thread: {row.name}")
    print(f"  id:      {row.id}")
    print(f"  state:   {status_label(row.status)} ({row.status})")
    print(f"  source:  {row.source}")
    print(f"  updated: {row.updated_at}")
    print(f"  cwd:     {row.raw.get('cwd')}")
    print(f"  path:    {row.raw.get('path')}")
    print(f"  preview: {one_line(row.raw.get('preview'), 240)}")


def print_recent_items(thread: Any, limit: int = 12) -> None:
    resp = thread.read(include_turns=True)
    data = model_to_json(resp)
    turns = data.get("thread", {}).get("turns") or []
    if not turns:
        print("No turns loaded for this thread.")
        return

    print()
    print(f"Loaded {len(turns)} turn(s). Showing recent items:")
    print("-" * 100)
    items: list[Json] = []
    for turn in turns:
        for item in turn.get("items") or []:
            item = item.get("root", item) if isinstance(item, dict) else item
            if isinstance(item, dict):
                items.append(item)
    for item in items[-limit:]:
        print(format_thread_item(item))
    print("-" * 100)


def collect_thread_items(thread: Any) -> tuple[Json, list[Json]]:
    resp = thread.read(include_turns=True)
    data = model_to_json(resp)
    turns = data.get("thread", {}).get("turns") or []
    items: list[Json] = []
    for turn in turns:
        for item in turn.get("items") or []:
            item = item.get("root", item) if isinstance(item, dict) else item
            if isinstance(item, dict):
                items.append(item)
    return data, items


def format_thread_item(item: Json) -> str:
    item_type = item.get("type", "item")
    if item_type == "userMessage":
        text = extract_content_text(item.get("content"))
        return f"[user] {one_line(text, 180)}"
    if item_type == "agentMessage":
        phase = item.get("phase") or "agent"
        return f"[agent:{phase}] {one_line(item.get('text'), 180)}"
    if item_type == "commandExecution":
        command = one_line(item.get("command"), 100)
        status = item.get("status")
        code = item.get("exitCode")
        output = one_line(item.get("aggregatedOutput"), 120)
        suffix = f" output={output}" if output else ""
        return f"[command:{status}] exit={code} {command}{suffix}"
    if item_type == "reasoning":
        summary = " ".join(item.get("summary") or item.get("content") or [])
        return f"[reasoning] {one_line(summary, 180)}"
    return f"[{item_type}] {one_line(json.dumps(item, ensure_ascii=False), 180)}"


def extract_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for part in content:
        if isinstance(part, dict):
            if part.get("type") == "text":
                parts.append(str(part.get("text") or ""))
            elif "text" in part:
                parts.append(str(part["text"]))
    return " ".join(parts)


def prompt_multiline() -> str:
    print("Enter message. Finish with an empty line:")
    lines: list[str] = []
    while True:
        line = input("> ")
        if not line:
            break
        lines.append(line)
    return "\n".join(lines).strip()


def start_turn(thread: Any, text: str) -> ManagedTurn | None:
    if not text:
        print("No message entered.")
        return None
    handle = thread.turn(text, sandbox=Sandbox.workspace_write)
    turn = ManagedTurn(
        thread_id=thread.id,
        turn_id=handle.id,
        handle=handle,
        done=threading.Event(),
        events=queue.Queue(),
    )
    managed_turns[turn.turn_id] = turn
    worker = threading.Thread(target=stream_turn_worker, args=(turn,), daemon=True)
    worker.start()
    print(f"Started turn {turn.turn_id}.")
    return turn


def stream_turn_worker(turn: ManagedTurn) -> None:
    try:
        for event in turn.handle.stream():
            turn.events.put(event)
    except BaseException as exc:
        turn.error = exc
        turn.events.put(exc)
    finally:
        turn.done.set()


def watch_turn(turn: ManagedTurn) -> None:
    print()
    print(f"Watching turn {turn.turn_id}")
    print("Commands while watching: i=interrupt, s <text>=steer, q=stop watching")
    print("-" * 100)

    input_thread = start_input_thread()
    while True:
        drain_events(turn)
        if turn.done.is_set():
            drain_events(turn)
            if turn.error:
                print(f"[stream error] {type(turn.error).__name__}: {turn.error}")
            print("[turn completed]")
            return

        try:
            cmd = input_thread.get_nowait()
        except queue.Empty:
            time.sleep(0.15)
            continue

        cmd = cmd.strip()
        if cmd in {"q", "quit", "exit"}:
            print("[stopped watching; turn continues if still active]")
            return
        if cmd in {"i", "interrupt"}:
            interrupt_turn(turn)
            continue
        if cmd.startswith("s "):
            text = cmd[2:].strip()
            if text:
                try:
                    turn.handle.steer(text)
                    print("[steered]")
                except Exception as exc:
                    print(f"[steer failed] {type(exc).__name__}: {exc}")
            continue
        print("Unknown command. Use i, s <text>, or q.")


def start_input_thread() -> "queue.Queue[str]":
    commands: "queue.Queue[str]" = queue.Queue()

    def read_loop() -> None:
        while True:
            try:
                commands.put(input())
            except EOFError:
                commands.put("q")
                return

    threading.Thread(target=read_loop, daemon=True).start()
    return commands


def drain_events(turn: ManagedTurn) -> None:
    while True:
        try:
            event = turn.events.get_nowait()
        except queue.Empty:
            return
        if isinstance(event, BaseException):
            print(f"[stream error] {type(event).__name__}: {event}")
            continue
        print(format_event(event))


def format_event(event: Any) -> str:
    payload = getattr(event, "payload", None)
    method = getattr(event, "method", None) or type(payload).__name__

    if isinstance(payload, AgentMessageDeltaNotification):
        return payload.delta or ""
    if isinstance(payload, ItemStartedNotification):
        item = model_to_json(payload.item)
        item = item.get("root", item) if isinstance(item, dict) else item
        item_type = item.get("type", "item") if isinstance(item, dict) else "item"
        item_id = item.get("id", "-") if isinstance(item, dict) else "-"
        return f"\n[item started] {item_type}:{item_id}"
    if isinstance(payload, ItemCompletedNotification):
        item = model_to_json(payload.item)
        item = item.get("root", item) if isinstance(item, dict) else item
        if isinstance(item, dict):
            return "\n" + format_thread_item(item)
    if isinstance(payload, TurnPlanUpdatedNotification):
        plan = model_to_json(payload).get("plan") or []
        parts = [f"{p.get('status')}:{p.get('step')}" for p in plan if isinstance(p, dict)]
        return "\n[plan] " + " | ".join(parts)
    if isinstance(payload, ThreadTokenUsageUpdatedNotification):
        usage = model_to_json(payload.token_usage)
        return f"\n[usage] {usage}"
    if isinstance(payload, TurnCompletedNotification):
        turn = model_to_json(payload.turn)
        return f"\n[turn completed] status={turn.get('status')} durationMs={turn.get('durationMs')}"

    data = model_to_json(payload)
    return "\n[" + str(method) + "] " + one_line(json.dumps(data, ensure_ascii=False), 220)


def interrupt_turn(turn: ManagedTurn) -> None:
    try:
        resp = turn.handle.interrupt()
        print(f"[interrupt requested] {json.dumps(model_to_json(resp), ensure_ascii=False)}")
    except Exception as exc:
        print(f"[interrupt failed] {type(exc).__name__}: {exc}")


def try_interrupt_external(codex: Codex, row: ThreadRow) -> None:
    thread = codex.thread_resume(row.id)
    data = model_to_json(thread.read(include_turns=True))
    turns = data.get("thread", {}).get("turns") or []
    active_turns = [turn for turn in turns if turn.get("status") in {"inProgress", "running"}]
    if not active_turns and turns:
        last = turns[-1]
        if last.get("completedAt") is None and last.get("id"):
            active_turns = [last]

    if not active_turns:
        print("No active turn id was visible in thread history. Cannot interrupt from this SDK connection.")
        return

    turn_id = active_turns[-1]["id"]
    print(f"Attempting interrupt for visible turn {turn_id}...")
    try:
        resp = codex._client.turn_interrupt(row.id, turn_id)  # Uses SDK raw client; no public handle exists.
        print(f"Interrupt response: {json.dumps(model_to_json(resp), ensure_ascii=False)}")
    except Exception as exc:
        print(f"Interrupt failed: {type(exc).__name__}: {exc}")


def watch_thread_by_polling(codex: Codex, row: ThreadRow) -> None:
    thread = codex.thread_resume(row.id)
    print()
    print(f"Polling thread {row.id}")
    print("Commands while polling: i=interrupt visible active turn, q=stop watching")
    print("-" * 100)

    seen: set[str] = set()
    _, initial_items = collect_thread_items(thread)
    for item in initial_items[-8:]:
        item_id = str(item.get("id") or json.dumps(item, ensure_ascii=False)[:80])
        seen.add(item_id)
        print(format_thread_item(item))

    commands = start_input_thread()
    while True:
        try:
            data, items = collect_thread_items(thread)
        except Exception as exc:
            print(f"[poll failed] {type(exc).__name__}: {exc}")
            return

        for item in items:
            item_id = str(item.get("id") or json.dumps(item, ensure_ascii=False)[:80])
            if item_id in seen:
                continue
            seen.add(item_id)
            print(format_thread_item(item))

        state = status_type(data.get("thread", {}).get("status"))
        if not is_active(state):
            print(f"[thread state] {status_label(state)} ({state})")
            return

        try:
            cmd = commands.get_nowait().strip().lower()
        except queue.Empty:
            time.sleep(2)
            continue

        if cmd in {"q", "quit", "exit"}:
            print("[stopped polling]")
            return
        if cmd in {"i", "interrupt"}:
            try_interrupt_external(codex, row)
            continue
        if cmd:
            print("Unknown command. Use i or q.")


def thread_menu(codex: Codex, row: ThreadRow) -> None:
    thread = codex.thread_resume(row.id)
    while True:
        show_thread_details(row)
        print()
        print("Actions:")
        print("  1. Read recent messages/items")
        print("  2. Send a message and watch live")
        if is_active(row.status):
            print("  3. Watch latest activity by polling")
            print("  4. Try interrupt active visible turn")
        print("  b. Back")
        choice = input("Choose action: ").strip().lower()
        if choice in {"b", "back", ""}:
            return
        if choice == "1":
            print_recent_items(thread)
            input("Press Enter to continue...")
            continue
        if choice == "2":
            text = prompt_multiline()
            turn = start_turn(thread, text)
            if turn is not None:
                watch_turn(turn)
                input("Press Enter to continue...")
            continue
        if choice == "3" and is_active(row.status):
            watch_thread_by_polling(codex, row)
            input("Press Enter to continue...")
            continue
        if choice == "4" and is_active(row.status):
            try_interrupt_external(codex, row)
            input("Press Enter to continue...")
            continue
        print("Invalid action.")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Interactive Codex SDK thread controller")
    parser.add_argument("--limit", type=int, default=10, help="number of recent threads to show")
    parser.add_argument(
        "--cwd",
        default=None,
        help="optional working directory used when launching the SDK app-server",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    print("Starting local Codex app-server through Python SDK...")
    config = CodexConfig(cwd=args.cwd) if args.cwd else None
    with Codex(config=config) as codex:
        while True:
            resp = codex.thread_list(limit=args.limit)
            rows = make_rows(resp)
            if not rows:
                print("No Codex threads found.")
                return 0
            print_threads(rows)
            try:
                row = choose_row(rows)
            except KeyboardInterrupt:
                print()
                print("Bye.")
                return 0
            if row is None:
                continue
            thread_menu(codex, row)


if __name__ == "__main__":
    raise SystemExit(main())
