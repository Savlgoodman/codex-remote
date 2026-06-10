import { useEffect, useReducer, useState } from "react";
import { getStatus, getThread, listThreads } from "./api/client";
import { openEvents } from "./api/sse";
import { Sidebar } from "./components/Sidebar";
import { StatusBar } from "./components/StatusBar";
import { ThreadPane } from "./components/ThreadPane";
import { initialState, reducer } from "./store/eventReducer";

export function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [globalSse, setGlobalSse] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    getStatus().then((status) => dispatch({ type: "status.loaded", status })).catch(() => undefined);
    listThreads().then((threads) => dispatch({ type: "threads.loaded", threads })).catch(() => undefined);
    const events = openEvents("/api/events", (event) => dispatch({ type: "event", event }), setGlobalSse);
    return () => events.close();
  }, []);

  useEffect(() => {
    if (!selectedId && state.threads.length > 0) {
      setSelectedId(state.threads[0].conversationId);
    }
  }, [selectedId, state.threads]);

  useEffect(() => {
    if (!selectedId) return;
    let disposed = false;
    setLoadingDetail(true);
    setDetailError(null);
    getThread(selectedId)
      .then((detail) => {
        if (!disposed) dispatch({ type: "thread.loaded", detail });
      })
      .catch((error) => {
        if (!disposed) setDetailError(error instanceof Error ? error.message : "thread_load_failed");
      })
      .finally(() => {
        if (!disposed) setLoadingDetail(false);
      });
    const events = openEvents(
      `/api/threads/${encodeURIComponent(selectedId)}/events`,
      (event) => dispatch({ type: "event", event }),
      (connected) => dispatch({ type: "detail.sse", conversationId: selectedId, connected }),
    );
    return () => {
      disposed = true;
      events.close();
    };
  }, [selectedId]);

  const detail = selectedId ? state.details[selectedId] ?? null : null;
  return (
    <div className="app-shell">
      <Sidebar threads={state.threads} selectedId={selectedId} onSelect={setSelectedId} />
      <div className="work-area">
        <div className="window-menu">
          <span>文件</span>
          <span>编辑</span>
          <span>查看</span>
          <span>窗口</span>
          <span>帮助</span>
        </div>
        <StatusBar status={state.status} sseConnected={globalSse} />
        <ThreadPane detail={detail} loading={loadingDetail} error={detailError} />
      </div>
    </div>
  );
}
