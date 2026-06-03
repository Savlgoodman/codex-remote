import { useEffect, useMemo, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";
import { getStatus, getThreadDetail, getThreads, refreshThreads, sendMessage, websocketUrl } from "./api";
import { Composer } from "./components/Composer";
import { MessageView } from "./components/MessageView";
import { StatusBar } from "./components/StatusBar";
import { ThreadList } from "./components/ThreadList";
import { WsDebugPanel } from "./components/WsDebugPanel";
import type { IpcStatus, Message, SendOptions, ServerEvent, ServerStatus, ThreadDetail, ThreadSummary, WsLogEntry } from "./types";

const initialIpc: IpcStatus = {
  online: false,
  clientId: null,
  connectedAt: null,
  lastError: null,
  lastSeenAt: null,
};
const WS_LOG_KEY = "codex-webui.wsLogs";
const MAX_WS_LOGS = 160;
const MAX_RENDERED_MESSAGES = 180;
const WS_PAYLOAD_PREVIEW_LIMIT = 20_000;

export function App() {
  const [status, setStatus] = useState<ServerStatus>({
    ipc: initialIpc,
    sdk: { available: false, lastError: null },
    control: { enabled: true },
    time: Date.now() / 1000,
  });
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<ThreadDetail | null>(null);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [wsState, setWsState] = useState<"connecting" | "open" | "closed">("connecting");
  const [wsLogs, setWsLogs] = useState<WsLogEntry[]>(loadWsLogs);
  const messageScrollRef = useRef<HTMLDivElement | null>(null);
  const pendingEventsRef = useRef<ServerEvent[]>([]);
  const pendingLogsRef = useRef<WsLogEntry[]>([]);
  const flushTimerRef = useRef<number | null>(null);
  const logFlushTimerRef = useRef<number | null>(null);

  async function loadInitial() {
    try {
      const [nextStatus, nextThreads] = await Promise.all([getStatus(), getThreads()]);
      setStatus(nextStatus);
      setThreads(nextThreads);
      if (!selectedId && nextThreads[0]) {
        setSelectedId(nextThreads[0].conversationId);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  useEffect(() => {
    void loadInitial();
  }, []);

  useEffect(() => {
    let stopped = false;
    let socket: WebSocket | null = null;
    let reconnectTimer: number | null = null;

    function connect() {
      if (stopped) return;
      setWsState("connecting");
      enqueueWsLog(systemWsLog("connecting", "Opening websocket connection."));
      socket = new WebSocket(websocketUrl());
      socket.onopen = () => {
        setWsState("open");
        enqueueWsLog(systemWsLog("open", "Websocket connected."));
      };
      socket.onclose = () => {
        setWsState("closed");
        enqueueWsLog(systemWsLog("closed", "Websocket closed; reconnecting soon."));
        if (!stopped) {
          reconnectTimer = window.setTimeout(connect, 1500);
        }
      };
      socket.onerror = () => {
        setWsState("closed");
        enqueueWsLog(systemWsLog("error", "Websocket error."));
      };
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as ServerEvent;
          enqueueWsLog(eventWsLog(data, event.data.length));
          enqueueServerEvent(data);
        } catch (exc) {
          enqueueWsLog(systemWsLog("parse_error", exc instanceof Error ? exc.message : String(exc)));
        }
      };
    }

    function enqueueWsLog(entry: WsLogEntry) {
      pendingLogsRef.current.push(entry);
      if (logFlushTimerRef.current !== null) return;
      logFlushTimerRef.current = window.requestAnimationFrame(() => {
        logFlushTimerRef.current = null;
        const logs = pendingLogsRef.current.splice(0);
        appendWsLogs(setWsLogs, logs);
      });
    }

    function enqueueServerEvent(data: ServerEvent) {
      pendingEventsRef.current.push(data);
      if (flushTimerRef.current !== null) return;
      flushTimerRef.current = window.requestAnimationFrame(() => {
        flushTimerRef.current = null;
        const events = pendingEventsRef.current.splice(0);
        applyServerEvents(events);
      });
    }

    function applyServerEvents(events: ServerEvent[]) {
      if (events.length === 0) return;
      let latestIpc: IpcStatus | null = null;
      let latestThreads: ThreadSummary[] | null = null;
      const latestSummaries = new Map<string, ThreadSummary>();
      const latestSnapshots = new Map<string, Extract<ServerEvent, { type: "thread.snapshot" }>>();
      const latestMessages = new Map<string, Message[]>();
      for (const event of events) {
        if (event.type === "ipc.status") {
          latestIpc = event;
        } else if (event.type === "threads.snapshot") {
          latestThreads = event.threads;
        } else if (event.type === "thread.summary") {
          latestSummaries.set(event.conversationId, event.summary);
        } else if (event.type === "thread.snapshot") {
          latestSnapshots.set(event.conversationId, event);
          latestSummaries.set(event.conversationId, event.summary);
        } else if (event.type === "thread.message.upsert") {
          latestMessages.set(event.conversationId, [...(latestMessages.get(event.conversationId) ?? []), event.message]);
        }
      }
      if (latestIpc) {
        setStatus((prev) => ({ ...prev, ipc: latestIpc }));
      }
      if (latestThreads) {
        setThreads(latestThreads);
      }
      for (const summary of latestSummaries.values()) {
        setThreads((prev) => upsertThread(prev, summary));
        setDetail((prev) => {
          if (!prev || prev.summary.conversationId !== summary.conversationId) return prev;
          return { ...prev, summary };
        });
      }
      for (const event of latestSnapshots.values()) {
        setDetail((prev) => {
          if (!prev || prev.summary.conversationId !== event.conversationId) return prev;
          return { ...prev, summary: event.summary, messages: event.messages };
        });
      }
      for (const [conversationId, messages] of latestMessages) {
        setDetail((prev) => {
          if (!prev || prev.summary.conversationId !== conversationId) return prev;
          return { ...prev, messages: upsertMessages(prev.messages, messages) };
        });
      }
    }

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      if (flushTimerRef.current !== null) {
        window.cancelAnimationFrame(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      if (logFlushTimerRef.current !== null) {
        window.cancelAnimationFrame(logFlushTimerRef.current);
        logFlushTimerRef.current = null;
      }
      const remainingLogs = pendingLogsRef.current.splice(0);
      appendWsLogs(setWsLogs, remainingLogs);
      socket?.close();
    };
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    setLoadingDetail(true);
    setError(null);
    getThreadDetail({ conversationId: selectedId })
      .then((next) => {
        if (!cancelled) {
          setDetail(next);
          setThreads((prev) => upsertThread(prev, next.summary));
        }
      })
      .catch((exc) => {
        if (!cancelled) setError(exc instanceof Error ? exc.message : String(exc));
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  const selectedSummary = useMemo(
    () => {
      if (detail?.summary.conversationId === selectedId) return detail.summary;
      return threads.find((thread) => thread.conversationId === selectedId) ?? null;
    },
    [threads, selectedId, detail],
  );

  async function handleRefresh() {
    setError(null);
    try {
      await refreshThreads();
      const nextThreads = await getThreads();
      setThreads(nextThreads);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : String(exc));
    }
  }

  async function handleSend(text: string, options: SendOptions = {}, confirmDangerFullAccess = false) {
    if (!selectedId) return;
    setError(null);
    try {
      await sendMessage({ conversationId: selectedId, text, confirmDangerFullAccess, options });
      const next = await getThreadDetail({ conversationId: selectedId });
      setDetail(next);
      setThreads((prev) => upsertThread(prev, next.summary));
    } catch (exc) {
      const err = exc as Error & { data?: { error?: string; message?: string } };
      if (err.data?.error === "dangerFullAccess_requires_confirmation") {
        const confirmed = window.confirm(err.data.message ?? "该线程需要 dangerFullAccess 确认，继续发送？");
        if (confirmed) {
          await sendMessage({ conversationId: selectedId, text, confirmDangerFullAccess: true, options });
          const next = await getThreadDetail({ conversationId: selectedId });
          setDetail(next);
          setThreads((prev) => upsertThread(prev, next.summary));
          return;
        }
      }
      setError(err.data?.message ?? err.message ?? String(exc));
    }
  }

  const messages: Message[] = detail?.messages ?? [];
  const visibleMessages = messages.length > MAX_RENDERED_MESSAGES ? messages.slice(-MAX_RENDERED_MESSAGES) : messages;

  useEffect(() => {
    const node = messageScrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
  }, [selectedId, messages]);

  return (
    <div className="app-shell">
      <StatusBar status={status} wsState={wsState} />
      <main className="workspace">
        <aside className="sidebar">
          <div className="sidebar-head">
            <div>
              <h1>codex-webui</h1>
              <span>{threads.length} threads</span>
            </div>
            <button className="icon-button" onClick={handleRefresh} title="刷新线程">
              <RefreshCw size={17} />
            </button>
          </div>
          <ThreadList threads={threads} selectedId={selectedId} onSelect={setSelectedId} />
        </aside>
        <section className="detail-pane">
          {selectedSummary ? (
            <>
              <header className="thread-header">
                <div>
                  <h2>{selectedSummary.title}</h2>
                  <p>{selectedSummary.cwd || "No working directory"}</p>
                  <div className="thread-settings-line">
                    <span>{selectedSummary.latestModel ?? "model inherit"}</span>
                    <span>{selectedSummary.latestReasoningEffort ?? "reasoning inherit"}</span>
                    <span>{selectedSummary.approvalPolicy ?? "approval inherit"}</span>
                    <span>{selectedSummary.sandboxMode ?? "sandbox inherit"}</span>
                  </div>
                </div>
                <div className={`source-pill source-${selectedSummary.source}`}>{selectedSummary.source}</div>
              </header>
              {error ? <div className="error-bar">{error}</div> : null}
              <div className="message-scroll" ref={messageScrollRef}>
                {loadingDetail && messages.length === 0 ? <div className="empty-state">Loading thread...</div> : null}
                {messages.length === 0 && !loadingDetail ? <div className="empty-state">No messages loaded yet.</div> : null}
                {messages.length > visibleMessages.length ? (
                  <div className="message-window-note">
                    Showing latest {visibleMessages.length} of {messages.length} messages.
                  </div>
                ) : null}
                {visibleMessages.map((message) => (
                  <MessageView key={message.id} message={message} />
                ))}
              </div>
              <Composer summary={selectedSummary} ipcOnline={status.ipc.online} controlEnabled={status.control?.enabled !== false} onSend={handleSend} />
            </>
          ) : (
            <div className="empty-detail">Select a thread to inspect Codex activity.</div>
          )}
        </section>
      </main>
      <WsDebugPanel
        logs={wsLogs}
        wsState={wsState}
        onClear={() => {
          window.localStorage.removeItem(WS_LOG_KEY);
          setWsLogs([]);
        }}
      />
    </div>
  );
}

function upsertThread(rows: ThreadSummary[], incoming: ThreadSummary): ThreadSummary[] {
  const next = rows.filter((item) => item.conversationId !== incoming.conversationId);
  next.push(incoming);
  return next.sort((a, b) => (b.activeAt ?? b.updatedAt ?? 0) - (a.activeAt ?? a.updatedAt ?? 0));
}

function upsertMessages(rows: Message[], incoming: Message[]) {
  const byId = new Map(rows.map((message) => [message.id, message]));
  for (const message of incoming) {
    byId.set(message.id, message);
  }
  return [...byId.values()];
}

function loadWsLogs(): WsLogEntry[] {
  try {
    const raw = window.localStorage.getItem(WS_LOG_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter(isWsLogEntry).slice(0, MAX_WS_LOGS) : [];
  } catch {
    return [];
  }
}

function appendWsLogs(setLogs: (updater: (logs: WsLogEntry[]) => WsLogEntry[]) => void, entries: WsLogEntry[]) {
  if (entries.length === 0) return;
  setLogs((logs) => {
    const next = [...entries.reverse(), ...logs].slice(0, MAX_WS_LOGS);
    try {
      window.localStorage.setItem(WS_LOG_KEY, JSON.stringify(next));
    } catch {
      // Keep UI logging non-critical; storage quota/privacy settings should not affect websocket handling.
    }
    return next;
  });
}

function systemWsLog(eventType: string, summary: string): WsLogEntry {
  return {
    id: Date.now() + Math.floor(Math.random() * 1000),
    timestamp: Date.now(),
    direction: "system",
    eventType,
    size: null,
    summary,
    payloadPreview: summary,
  };
}

function eventWsLog(event: ServerEvent, size: number): WsLogEntry {
  const payload = payloadPreview(event);
  return {
    id: Date.now() + Math.floor(Math.random() * 1000),
    timestamp: Date.now(),
    direction: "in",
    eventType: event.type,
    size,
    conversationId: "conversationId" in event ? event.conversationId : undefined,
    summary: eventSummary(event),
    payloadPreview: payload.preview,
    payloadTruncated: payload.truncated,
  };
}

function eventSummary(event: ServerEvent) {
  if (event.type === "ipc.status") return event.online ? "ipc online" : `ipc offline ${event.lastError ?? ""}`.trim();
  if (event.type === "threads.snapshot") return `full list: ${event.threads.length} threads`;
  if (event.type === "thread.summary") return event.summary.latestItemPreview || event.summary.runtimeStatus;
  if (event.type === "thread.snapshot") return `${event.messages.length} messages`;
  if (event.type === "thread.message.upsert") return `${event.message.role}: ${event.message.text.slice(-120)}`;
  return "event";
}

function isWsLogEntry(value: unknown): value is WsLogEntry {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return false;
  const row = value as Partial<WsLogEntry>;
  return typeof row.id === "number" && typeof row.timestamp === "number" && typeof row.eventType === "string";
}

function payloadPreview(event: ServerEvent) {
  const text = JSON.stringify(event, null, 2);
  if (text.length <= WS_PAYLOAD_PREVIEW_LIMIT) return { preview: text, truncated: false };
  return { preview: `${text.slice(0, WS_PAYLOAD_PREVIEW_LIMIT)}\n... truncated ${text.length - WS_PAYLOAD_PREVIEW_LIMIT} chars`, truncated: true };
}
