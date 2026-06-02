import { useEffect, useMemo, useState } from "react";
import { RefreshCw } from "lucide-react";
import { getStatus, getThreadDetail, getThreads, refreshThreads, sendMessage, websocketUrl } from "./api";
import { Composer } from "./components/Composer";
import { MessageView } from "./components/MessageView";
import { StatusBar } from "./components/StatusBar";
import { ThreadList } from "./components/ThreadList";
import type { IpcStatus, Message, ServerEvent, ServerStatus, ThreadDetail, ThreadSummary } from "./types";

const initialIpc: IpcStatus = {
  online: false,
  clientId: null,
  connectedAt: null,
  lastError: null,
  lastSeenAt: null,
};

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
      socket = new WebSocket(websocketUrl());
      socket.onopen = () => setWsState("open");
      socket.onclose = () => {
        setWsState("closed");
        if (!stopped) {
          reconnectTimer = window.setTimeout(connect, 1500);
        }
      };
      socket.onerror = () => setWsState("closed");
      socket.onmessage = (event) => {
        const data = JSON.parse(event.data) as ServerEvent;
        if (data.type === "ipc.status") {
          setStatus((prev) => ({ ...prev, ipc: data }));
        } else if (data.type === "threads.changed") {
          setThreads(data.threads);
        } else if (data.type === "thread.snapshot") {
          setThreads((prev) => upsertThread(prev, data.summary));
          setDetail((prev) => {
            if (!prev || prev.summary.conversationId !== data.conversationId) return prev;
            return { ...prev, summary: data.summary, messages: data.messages };
          });
        } else if (data.type === "thread.patch") {
          setThreads((prev) => upsertThread(prev, data.summary));
          setDetail((prev) => {
            if (!prev || prev.summary.conversationId !== data.conversationId) return prev;
            return { ...prev, summary: data.summary };
          });
        }
      };
    }

    connect();
    return () => {
      stopped = true;
      if (reconnectTimer !== null) window.clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    setLoadingDetail(true);
    setError(null);
    getThreadDetail(selectedId)
      .then((next) => {
        if (!cancelled) setDetail(next);
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
    () => threads.find((thread) => thread.conversationId === selectedId) ?? detail?.summary ?? null,
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

  async function handleSend(text: string, confirmDangerFullAccess = false) {
    if (!selectedId) return;
    setError(null);
    try {
      await sendMessage(selectedId, text, confirmDangerFullAccess);
      const next = await getThreadDetail(selectedId);
      setDetail(next);
    } catch (exc) {
      const err = exc as Error & { data?: { error?: string; message?: string } };
      if (err.data?.error === "dangerFullAccess_requires_confirmation") {
        const confirmed = window.confirm(err.data.message ?? "该线程需要 dangerFullAccess 确认，继续发送？");
        if (confirmed) {
          await sendMessage(selectedId, text, true);
          const next = await getThreadDetail(selectedId);
          setDetail(next);
          return;
        }
      }
      setError(err.data?.message ?? err.message ?? String(exc));
    }
  }

  const messages: Message[] = detail?.messages ?? [];

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
                </div>
                <div className={`source-pill source-${selectedSummary.source}`}>{selectedSummary.source}</div>
              </header>
              {error ? <div className="error-bar">{error}</div> : null}
              <div className="message-scroll">
                {loadingDetail && messages.length === 0 ? <div className="empty-state">Loading thread...</div> : null}
                {messages.length === 0 && !loadingDetail ? <div className="empty-state">No messages loaded yet.</div> : null}
                {messages.map((message) => (
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
    </div>
  );
}

function upsertThread(rows: ThreadSummary[], incoming: ThreadSummary): ThreadSummary[] {
  const next = rows.filter((item) => item.conversationId !== incoming.conversationId);
  next.push(incoming);
  return next.sort((a, b) => (b.activeAt ?? b.updatedAt ?? 0) - (a.activeAt ?? a.updatedAt ?? 0));
}
