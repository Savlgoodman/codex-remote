#!/usr/bin/env node
"use strict";

const crypto = require("node:crypto");
const net = require("node:net");
const os = require("node:os");
const path = require("node:path");

const args = new Set(process.argv.slice(2));
const rawMode = args.has("--raw");
const once = args.has("--once");
const durationArg = process.argv.find((arg) => arg.startsWith("--duration-ms="));
const durationMs = durationArg ? Number(durationArg.split("=")[1]) : null;
const clientTypeArg = process.argv.find((arg) => arg.startsWith("--client-type="));
const clientType = clientTypeArg?.split("=")[1] || "codex-remote-observer";

function ipcPath() {
  if (process.platform === "win32") {
    return "\\\\.\\pipe\\codex-ipc";
  }
  const root = path.join(os.tmpdir(), "codex-ipc");
  return path.join(root, typeof process.getuid === "function" ? `ipc-${process.getuid()}.sock` : "ipc.sock");
}

function frame(message) {
  const payload = Buffer.from(JSON.stringify(message), "utf8");
  const out = Buffer.allocUnsafe(4 + payload.length);
  out.writeUInt32LE(payload.length, 0);
  payload.copy(out, 4);
  return out;
}

function write(socket, message) {
  if (closing || socket.destroyed || !socket.writable) return;
  socket.write(frame(message));
}

function truncate(text, limit = 120) {
  const value = String(text ?? "").replace(/\s+/g, " ").trim();
  return value.length <= limit ? value : `${value.slice(0, limit - 1)}...`;
}

function summarizeItem(item) {
  if (!item || typeof item !== "object") return null;
  switch (item.type) {
    case "userMessage": {
      const parts = Array.isArray(item.content) ? item.content : [];
      const text = parts.map((part) => part?.text).filter(Boolean).join(" ");
      return `user: ${truncate(text)}`;
    }
    case "agentMessage":
      return `agent: ${truncate(item.text)}`;
    case "commandExecution":
      return `command:${item.status ?? "unknown"} ${truncate(item.command, 80)}`;
    case "reasoning":
      return "reasoning";
    default:
      return item.type || "item";
  }
}

function summarizeThreadState(params) {
  const change = params?.change;
  const state = change?.conversationState;
  const latestTurn = Array.isArray(state?.turns) ? state.turns[state.turns.length - 1] : null;
  const latestItem = Array.isArray(latestTurn?.items) ? latestTurn.items[latestTurn.items.length - 1] : null;
  return {
    event: "thread-stream-state-changed",
    hostId: params?.hostId,
    conversationId: params?.conversationId,
    changeType: change?.type,
    title: truncate(state?.title || state?.preview || ""),
    cwd: state?.cwd,
    runtime: state?.threadRuntimeStatus?.type,
    turns: state?.turns?.length ?? null,
    latestTurnStatus: latestTurn?.status ?? null,
    latestItem: summarizeItem(latestItem),
  };
}

function summarize(message) {
  if (message.type === "response") {
    return {
      event: "response",
      method: message.method,
      resultType: message.resultType,
      handledByClientId: message.handledByClientId,
      clientId: message.result?.clientId,
      error: message.error,
    };
  }
  if (message.type === "broadcast" && message.method === "thread-stream-state-changed") {
    return summarizeThreadState(message.params);
  }
  if (message.type === "broadcast" && message.method === "client-status-changed") {
    return {
      event: "client-status-changed",
      clientId: message.params?.clientId,
      clientType: message.params?.clientType,
      status: message.params?.status,
    };
  }
  if (message.type === "broadcast") {
    return {
      event: message.method,
      sourceClientId: message.sourceClientId,
      params: message.params,
    };
  }
  return { event: message.type, method: message.method ?? null };
}

const socketPath = ipcPath();
const socket = net.createConnection(socketPath);
let buffer = Buffer.alloc(0);
let neededBytes = null;
let initialized = false;
let closing = false;

function closeSoon() {
  if (closing) return;
  closing = true;
  socket.end();
  setTimeout(() => {
    socket.destroy();
  }, 100).unref?.();
}

socket.on("connect", () => {
  const init = {
    type: "request",
    requestId: crypto.randomUUID(),
    sourceClientId: "initializing-client",
    version: 0,
    method: "initialize",
    params: { clientType },
    targetClientId: null,
  };
  write(socket, init);
});

socket.on("data", (chunk) => {
  buffer = Buffer.concat([buffer, chunk]);
  for (;;) {
    if (neededBytes == null) {
      if (buffer.length < 4) return;
      neededBytes = buffer.readUInt32LE(0);
      buffer = buffer.subarray(4);
    }
    if (buffer.length < neededBytes) return;
    const payload = buffer.subarray(0, neededBytes);
    buffer = buffer.subarray(neededBytes);
    neededBytes = null;
    handleMessage(JSON.parse(payload.toString("utf8")));
  }
});

socket.on("error", (error) => {
  if (closing && error?.code === "EPIPE") return;
  console.error(`Failed to connect to ${socketPath}: ${error.message}`);
  process.exitCode = 1;
});

socket.on("close", () => {
  closing = true;
});

function handleMessage(message) {
  if (closing) return;
  if (message.type === "client-discovery-request") {
    write(socket, {
      type: "client-discovery-response",
      requestId: message.requestId,
      response: { canHandle: false },
    });
    return;
  }
  if (message.type === "request") {
    write(socket, {
      type: "response",
      requestId: message.requestId,
      resultType: "error",
      error: "no-handler-for-request",
    });
    return;
  }

  if (!initialized && message.type === "response" && message.method === "initialize") {
    initialized = true;
  }

  console.log(JSON.stringify(rawMode ? message : summarize(message), null, 2));
  if (once && initialized) {
    closeSoon();
  }
}

if (Number.isFinite(durationMs) && durationMs > 0) {
  setTimeout(closeSoon, durationMs);
}
