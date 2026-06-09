# Codex IPC Monitor

Standalone Node-based IPC monitor for the local Codex app.

## What it does

- Connects directly to the Codex IPC transport
- Captures raw length-prefixed JSON frames
- Stores both:
  - raw JSON text
  - parsed structured summaries
- Shows a live web UI with:
  - start capture
  - pause capture
  - clear capture
  - export captured packets as JSONL
  - color coding by message type
  - useful parsing for `thread.summary` and `thread-stream-state-changed`

## Start

```bash
cd ipc-monitor
npm start
```

Then open:

[http://127.0.0.1:7011](http://127.0.0.1:7011)

## Export

Use the `Export JSONL` button, or request the endpoint directly:

[http://127.0.0.1:7011/api/packets.jsonl](http://127.0.0.1:7011/api/packets.jsonl)

Each line is one captured packet in chronological order. Packets include `rawText`, `raw`, and `parsed`.

## Environment variables

- `IPC_MONITOR_PORT`: HTTP port, default `7011`
- `IPC_MONITOR_MAX_PACKETS`: in-memory packet buffer size, default `1500`
- `IPC_MONITOR_RECONNECT_MS`: reconnect interval, default `1500`
- `CODEX_IPC_PATH`: override IPC endpoint

## Default IPC endpoint

- Windows: `\\.\pipe\codex-ipc`
- Unix-like: `/tmp/codex-ipc/ipc-<uid>.sock`
