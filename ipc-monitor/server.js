import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { randomUUID } from "node:crypto";
import { fileURLToPath } from "node:url";
import { readFile } from "node:fs/promises";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const publicDir = path.join(__dirname, "public");

const HTTP_PORT = Number.parseInt(process.env.IPC_MONITOR_PORT ?? "7011", 10);
const MAX_PACKETS = Number.parseInt(process.env.IPC_MONITOR_MAX_PACKETS ?? "1500", 10);
const RECONNECT_DELAY_MS = Number.parseInt(process.env.IPC_MONITOR_RECONNECT_MS ?? "1500", 10);

function getDefaultIpcEndpoint() {
  if (process.env.CODEX_IPC_PATH) {
    return process.env.CODEX_IPC_PATH;
  }

  if (process.platform === "win32") {
    return "\\\\.\\pipe\\codex-ipc";
  }

  try {
    const uid = os.userInfo().uid;
    return `/tmp/codex-ipc/ipc-${uid}.sock`;
  } catch {
    return "/tmp/codex-ipc/ipc.sock";
  }
}

const IPC_ENDPOINT = getDefaultIpcEndpoint();

const state = {
  captureEnabled: false,
  ipcConnected: false,
  ipcEndpoint: IPC_ENDPOINT,
  lastConnectedAt: null,
  lastSeenAt: null,
  lastError: null,
  lastPacketAt: null,
  packetCount: 0,
  droppedCount: 0,
  monitorClientId: `ipc-monitor-${randomUUID()}`,
  negotiatedClientId: null,
  packets: [],
};

const sseClients = new Set();
let socket = null;
let reconnectTimer = null;
let frameBuffer = Buffer.alloc(0);
let sequence = 0;
let initializeRequestId = null;

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
};

function nowIso() {
  return new Date().toISOString();
}

function truncate(text, maxLength = 220) {
  if (typeof text !== "string") {
    return "";
  }
  if (text.length <= maxLength) {
    return text;
  }
  return `${text.slice(0, maxLength - 1)}...`;
}

function safeJsonStringify(value, pretty = false) {
  try {
    return JSON.stringify(value, null, pretty ? 2 : 0);
  } catch (error) {
    return JSON.stringify({
      error: "json_stringify_failed",
      message: error instanceof Error ? error.message : String(error),
    });
  }
}

function inferConversationId(message) {
  if (!message || typeof message !== "object") {
    return null;
  }

  return (
    message.conversationId ??
    message.summary?.conversationId ??
    message.params?.conversationId ??
    message.params?.threadId ??
    message.params?.turnStartParams?.threadId ??
    message.response?.conversationId ??
    null
  );
}

function patchPathToString(pathValue) {
  if (!Array.isArray(pathValue)) {
    return String(pathValue ?? "");
  }
  return pathValue.join(".");
}

function extractThreadSettings(value) {
  if (!value || typeof value !== "object") {
    return null;
  }

  const sandboxType =
    value.sandboxPolicy?.type ??
    value.sandboxMode ??
    null;

  return {
    cwd: value.cwd ?? null,
    model: value.model ?? null,
    modelProvider: value.modelProvider ?? null,
    serviceTier: value.serviceTier ?? null,
    effort: value.effort ?? value.reasoning_effort ?? null,
    summary: value.summary ?? null,
    approvalPolicy: value.approvalPolicy ?? null,
    approvalsReviewer: value.approvalsReviewer ?? null,
    sandboxType,
    activePermissionProfile: value.activePermissionProfile ?? null,
    personality: value.personality ?? null,
    collaborationMode: value.collaborationMode?.mode ?? value.collaborationMode ?? null,
  };
}

function summarizeThreadSummary(summary) {
  if (!summary || typeof summary !== "object") {
    return "thread.summary";
  }

  const pieces = [
    "thread.summary",
    summary.latestModel,
    summary.latestReasoningEffort,
    summary.approvalPolicy,
    summary.sandboxMode,
  ].filter(Boolean);

  return pieces.join(" ");
}

function parseBroadcastThreadState(message) {
  const change = message?.params?.change;
  if (!change || typeof change !== "object") {
    return {
      kind: "thread-stream-state-changed",
      summary: "thread-stream-state-changed",
      changeType: null,
      revision: null,
      baseRevision: null,
      patchPaths: [],
      threadSettings: null,
      highlights: {},
    };
  }

  const conversationState =
    change.conversationState && typeof change.conversationState === "object"
      ? change.conversationState
      : null;

  const patchPaths = Array.isArray(change.patches)
    ? change.patches.map((patch) => patchPathToString(patch?.path))
    : [];

  let threadSettings = extractThreadSettings(conversationState?.latestThreadSettings);
  let collaborationMode =
    conversationState?.latestCollaborationMode?.mode ??
    conversationState?.latestCollaborationMode ??
    null;
  let latestReasoningEffort =
    conversationState?.latestReasoningEffort ??
    threadSettings?.effort ??
    null;
  let latestModel =
    conversationState?.latestModel ??
    threadSettings?.model ??
    null;

  for (const patch of change.patches ?? []) {
    const patchPath = patchPathToString(patch?.path);
    if (patchPath === "latestThreadSettings") {
      threadSettings = extractThreadSettings(patch.value);
      latestModel = threadSettings?.model ?? latestModel;
      latestReasoningEffort = threadSettings?.effort ?? latestReasoningEffort;
    } else if (patchPath === "latestReasoningEffort") {
      latestReasoningEffort = patch.value ?? latestReasoningEffort;
    } else if (patchPath === "latestCollaborationMode") {
      collaborationMode = patch.value?.mode ?? patch.value ?? null;
    }
  }

  const highlights = {
    model: latestModel,
    reasoningEffort: latestReasoningEffort,
    approvalPolicy: threadSettings?.approvalPolicy ?? null,
    sandboxType: threadSettings?.sandboxType ?? null,
    collaborationMode,
  };

  const pathSummary = patchPaths.length > 0 ? patchPaths.join(", ") : "no paths";
  const revisionText =
    typeof change.baseRevision === "number" && typeof change.revision === "number"
      ? `r${change.baseRevision}->${change.revision}`
      : "revision ?";

  const turnCount = Array.isArray(conversationState?.turns) ? conversationState.turns.length : null;
  const changeLabel =
    change.type === "snapshot"
      ? `snapshot ${revisionText}`
      : `patches ${revisionText} ${pathSummary}`;

  return {
    kind: "thread-stream-state-changed",
    summary: changeLabel,
    changeType: change.type ?? null,
    revision: change.revision ?? null,
    baseRevision: change.baseRevision ?? null,
    patchPaths,
    threadSettings,
    collaborationMode,
    highlights,
    title: conversationState?.title ?? conversationState?.preview ?? null,
    cwd: conversationState?.cwd ?? threadSettings?.cwd ?? null,
    turnCount,
  };
}

function parseMessage(message) {
  const type = message?.type ?? "unknown";
  const method = message?.method ?? message?.params?.type ?? null;
  const conversationId = inferConversationId(message);
  const base = {
    type,
    method,
    requestId: message?.requestId ?? null,
    conversationId,
    sourceClientId: message?.sourceClientId ?? null,
    targetClientId: message?.targetClientId ?? null,
    summary: "",
    highlights: {},
    details: {},
  };

  if (type === "thread.summary") {
    const summary = message.summary ?? {};
    return {
      ...base,
      summary: summarizeThreadSummary(summary),
      highlights: {
        model: summary.latestModel ?? null,
        reasoningEffort: summary.latestReasoningEffort ?? null,
        approvalPolicy: summary.approvalPolicy ?? null,
        sandboxMode: summary.sandboxMode ?? null,
        runtimeStatus: summary.runtimeStatus ?? null,
      },
      details: {
        title: summary.title ?? null,
        cwd: summary.cwd ?? null,
        source: summary.source ?? null,
        runtimeStatus: summary.runtimeStatus ?? null,
        latestTurnStatus: summary.latestTurnStatus ?? null,
        latestItemPreview: summary.latestItemPreview ?? null,
        activeAt: summary.activeAt ?? null,
        updatedAt: summary.updatedAt ?? null,
        hasLiveOwner: summary.hasLiveOwner ?? null,
        latestModel: summary.latestModel ?? null,
        latestReasoningEffort: summary.latestReasoningEffort ?? null,
        approvalPolicy: summary.approvalPolicy ?? null,
        sandboxMode: summary.sandboxMode ?? null,
      },
    };
  }

  if (type === "broadcast" && message.method === "thread-stream-state-changed") {
    const parsed = parseBroadcastThreadState(message);
    return {
      ...base,
      summary: parsed.summary,
      highlights: parsed.highlights,
      details: parsed,
    };
  }

  if (type === "request") {
    return {
      ...base,
      summary: `request ${message.method ?? "unknown"}`,
      highlights: {
        method: message.method ?? null,
      },
      details: {
        paramsType: message.params?.type ?? null,
        paramsKeys: message.params && typeof message.params === "object" ? Object.keys(message.params) : [],
      },
    };
  }

  if (type === "response") {
    const resultType = message.resultType ?? message.response?.type ?? null;
    const errorCode =
      (message.error && typeof message.error === "object" ? message.error.code : null) ??
      (typeof message.error === "string" ? message.error : null);
    return {
      ...base,
      summary: `response ${resultType ?? errorCode ?? "ok"}`,
      highlights: {
        resultType,
        errorCode,
      },
      details: {
        hasResponse: Object.prototype.hasOwnProperty.call(message ?? {}, "response"),
        hasResult: Object.prototype.hasOwnProperty.call(message ?? {}, "result"),
        hasError: Object.prototype.hasOwnProperty.call(message ?? {}, "error"),
        resultKeys:
          message.response && typeof message.response === "object"
            ? Object.keys(message.response)
            : message.result && typeof message.result === "object"
              ? Object.keys(message.result)
              : [],
      },
    };
  }

  if (type === "client-discovery-request") {
    return {
      ...base,
      summary: "client discovery request",
      details: {
        targetClientId: message.targetClientId ?? null,
      },
    };
  }

  if (type === "client-discovery-response") {
    return {
      ...base,
      summary: `client discovery response ${message.response?.canHandle ? "canHandle" : "ignore"}`,
      details: {
        canHandle: message.response?.canHandle ?? null,
      },
    };
  }

  return {
    ...base,
    summary: type,
    details: {
      topLevelKeys: message && typeof message === "object" ? Object.keys(message) : [],
    },
  };
}

function buildPacket({ direction, rawText, message, malformed = false, errorMessage = null }) {
  const parsed = malformed
    ? {
        type: "invalid-json",
        method: null,
        requestId: null,
        conversationId: null,
        summary: "invalid json frame",
        highlights: {},
        details: {
          error: errorMessage,
        },
      }
    : parseMessage(message);

  return {
    id: ++sequence,
    observedAt: nowIso(),
    direction,
    size: Buffer.byteLength(rawText, "utf8"),
    ipcType: malformed ? "invalid-json" : message?.type ?? "unknown",
    method: malformed ? null : message?.method ?? null,
    requestId: malformed ? null : message?.requestId ?? null,
    conversationId: parsed.conversationId ?? null,
    summary: parsed.summary,
    payloadPreview: truncate(rawText, 240),
    rawText,
    raw: malformed ? null : message,
    parsed,
    malformed,
  };
}

function emitSse(event, payload) {
  const serialized = `event: ${event}\ndata: ${safeJsonStringify(payload)}\n\n`;
  for (const client of sseClients) {
    client.write(serialized);
  }
}

function getStatus() {
  return {
    captureEnabled: state.captureEnabled,
    ipcConnected: state.ipcConnected,
    ipcEndpoint: state.ipcEndpoint,
    packetCount: state.packetCount,
    bufferedPackets: state.packets.length,
    droppedCount: state.droppedCount,
    lastConnectedAt: state.lastConnectedAt,
    lastSeenAt: state.lastSeenAt,
    lastPacketAt: state.lastPacketAt,
    lastError: state.lastError,
    monitorClientId: state.monitorClientId,
    negotiatedClientId: state.negotiatedClientId,
    httpPort: HTTP_PORT,
  };
}

function pushPacket(packet) {
  state.packetCount += 1;
  state.lastPacketAt = packet.observedAt;

  if (!state.captureEnabled) {
    emitSse("status", getStatus());
    return;
  }

  state.packets.unshift(packet);
  if (state.packets.length > MAX_PACKETS) {
    state.packets.length = MAX_PACKETS;
    state.droppedCount += 1;
  }

  emitSse("packet", packet);
  emitSse("status", getStatus());
}

function setLastError(error) {
  state.lastError = error
    ? {
        at: nowIso(),
        message: error instanceof Error ? error.message : String(error),
      }
    : null;
}

function clearPackets() {
  state.packets = [];
  emitSse("packets-cleared", { at: nowIso() });
  emitSse("status", getStatus());
}

function encodeFrame(text) {
  const payload = Buffer.from(text, "utf8");
  const header = Buffer.allocUnsafe(4);
  header.writeUInt32LE(payload.length, 0);
  return Buffer.concat([header, payload]);
}

function sendIpcMessage(message) {
  if (!socket || socket.destroyed) {
    return false;
  }

  const rawText = safeJsonStringify(message, false);
  socket.write(encodeFrame(rawText));
  pushPacket(buildPacket({ direction: "out", rawText, message }));
  return true;
}

function sendInitialize() {
  initializeRequestId = randomUUID();
  sendIpcMessage({
    type: "request",
    requestId: initializeRequestId,
    sourceClientId: "initializing-client",
    targetClientId: null,
    version: 0,
    method: "initialize",
    params: {
      clientType: "codex-ipc-monitor",
    },
  });
}

function maybeTrackNegotiatedClientId(message) {
  if (message?.type !== "response" || message.requestId !== initializeRequestId) {
    return;
  }

  const negotiatedClientId =
    message.result?.clientId ??
    message.result?.client?.clientId ??
    message.response?.clientId ??
    message.response?.client?.clientId ??
    message.clientId ??
    null;

  if (negotiatedClientId) {
    state.negotiatedClientId = negotiatedClientId;
    emitSse("status", getStatus());
  }
}

function maybeAutoReply(message) {
  if (!message || typeof message !== "object") {
    return;
  }

  if (message.type === "client-discovery-request") {
    sendIpcMessage({
      type: "client-discovery-response",
      requestId: message.requestId ?? randomUUID(),
      response: {
        canHandle: false,
      },
    });
    return;
  }

  if (message.type === "request" && message.method !== "initialize") {
    sendIpcMessage({
      type: "response",
      requestId: message.requestId ?? randomUUID(),
      resultType: "error",
      error: "codex-ipc-monitor-is-not-owner",
    });
  }
}

function handleFrame(buffer) {
  const rawText = buffer.toString("utf8");
  state.lastSeenAt = nowIso();

  try {
    const message = JSON.parse(rawText);
    const packet = buildPacket({ direction: "in", rawText, message });
    pushPacket(packet);
    maybeTrackNegotiatedClientId(message);
    maybeAutoReply(message);
  } catch (error) {
    const packet = buildPacket({
      direction: "in",
      rawText,
      malformed: true,
      errorMessage: error instanceof Error ? error.message : String(error),
    });
    pushPacket(packet);
  }
}

function processSocketData(chunk) {
  frameBuffer = Buffer.concat([frameBuffer, chunk]);

  while (frameBuffer.length >= 4) {
    const frameLength = frameBuffer.readUInt32LE(0);
    if (frameBuffer.length < frameLength + 4) {
      return;
    }

    const body = frameBuffer.subarray(4, frameLength + 4);
    frameBuffer = frameBuffer.subarray(frameLength + 4);
    handleFrame(body);
  }
}

function scheduleReconnect() {
  if (reconnectTimer) {
    return;
  }

  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectIpc();
  }, RECONNECT_DELAY_MS);
}

function handleDisconnect(error = null) {
  state.ipcConnected = false;
  if (error) {
    setLastError(error);
  }
  emitSse("status", getStatus());
  scheduleReconnect();
}

function connectIpc() {
  if (socket && !socket.destroyed) {
    return;
  }

  frameBuffer = Buffer.alloc(0);
  const client = net.createConnection(IPC_ENDPOINT);
  socket = client;

  client.on("connect", () => {
    state.ipcConnected = true;
    state.lastConnectedAt = nowIso();
    setLastError(null);
    emitSse("status", getStatus());
    sendInitialize();
  });

  client.on("data", processSocketData);

  client.on("error", (error) => {
    setLastError(error);
    emitSse("status", getStatus());
  });

  client.on("close", () => {
    socket = null;
    handleDisconnect();
  });

  client.on("end", () => {
    socket = null;
    handleDisconnect();
  });
}

async function serveStaticFile(response, filePath) {
  try {
    const content = await readFile(filePath);
    const extension = path.extname(filePath);
    response.writeHead(200, {
      "content-type": MIME_TYPES[extension] ?? "application/octet-stream",
      "cache-control": "no-store",
    });
    response.end(content);
  } catch {
    response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    response.end("Not found");
  }
}

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  response.end(safeJsonStringify(payload, false));
}

function sendJsonlExport(response) {
  const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const filename = `codex-ipc-${timestamp}.jsonl`;
  const lines = [...state.packets]
    .reverse()
    .map((packet) => safeJsonStringify(packet, false))
    .join("\n");
  const body = lines ? `${lines}\n` : "";
  const bodySize = Buffer.byteLength(body, "utf8");

  response.writeHead(200, {
    "content-type": "application/jsonl; charset=utf-8",
    "content-disposition": `attachment; filename="${filename}"`,
    "content-length": bodySize,
    "cache-control": "no-store",
  });
  response.end(body);
}

function setCaptureEnabled(enabled) {
  state.captureEnabled = enabled;
  emitSse("status", getStatus());
}

function handleApiRequest(request, response, pathname) {
  if (request.method === "GET" && pathname === "/api/status") {
    sendJson(response, 200, getStatus());
    return true;
  }

  if (request.method === "GET" && pathname === "/api/packets") {
    sendJson(response, 200, {
      packets: state.packets,
    });
    return true;
  }

  if (request.method === "GET" && pathname === "/api/packets.jsonl") {
    sendJsonlExport(response);
    return true;
  }

  if (request.method === "POST" && pathname === "/api/capture/start") {
    setCaptureEnabled(true);
    sendJson(response, 200, getStatus());
    return true;
  }

  if (request.method === "POST" && pathname === "/api/capture/pause") {
    setCaptureEnabled(false);
    sendJson(response, 200, getStatus());
    return true;
  }

  if (request.method === "POST" && pathname === "/api/capture/clear") {
    clearPackets();
    sendJson(response, 200, getStatus());
    return true;
  }

  if (request.method === "GET" && pathname === "/events") {
    response.writeHead(200, {
      "content-type": "text/event-stream; charset=utf-8",
      "cache-control": "no-store",
      connection: "keep-alive",
    });
    response.write("retry: 1500\n\n");
    sseClients.add(response);

    const snapshot = {
      status: getStatus(),
      packets: state.packets,
    };
    response.write(`event: snapshot\ndata: ${safeJsonStringify(snapshot)}\n\n`);

    request.on("close", () => {
      sseClients.delete(response);
      response.end();
    });

    return true;
  }

  return false;
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://${request.headers.host ?? "localhost"}`);
  const pathname = url.pathname;

  if (handleApiRequest(request, response, pathname)) {
    return;
  }

  if (request.method !== "GET") {
    response.writeHead(405, { "content-type": "text/plain; charset=utf-8" });
    response.end("Method not allowed");
    return;
  }

  if (pathname === "/") {
    await serveStaticFile(response, path.join(publicDir, "index.html"));
    return;
  }

  const normalizedPath = path.normalize(path.join(publicDir, pathname.replace(/^\/+/, "")));
  if (!normalizedPath.startsWith(publicDir)) {
    response.writeHead(403, { "content-type": "text/plain; charset=utf-8" });
    response.end("Forbidden");
    return;
  }

  await serveStaticFile(response, normalizedPath);
});

server.listen(HTTP_PORT, () => {
  console.log(`[ipc-monitor] HTTP server listening on http://127.0.0.1:${HTTP_PORT}`);
  console.log(`[ipc-monitor] IPC endpoint: ${IPC_ENDPOINT}`);
});

connectIpc();

process.on("SIGINT", () => {
  if (socket && !socket.destroyed) {
    socket.destroy();
  }
  server.close(() => {
    process.exit(0);
  });
});
