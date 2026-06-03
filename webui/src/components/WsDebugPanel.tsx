import { PointerEvent, useEffect, useRef, useState } from "react";
import { Bug, Check, ChevronDown, ChevronUp, Copy, LocateFixed, Trash2 } from "lucide-react";
import type { WsLogEntry } from "../types";

interface Props {
  logs: WsLogEntry[];
  wsState: "connecting" | "open" | "closed";
  onClear: () => void;
}

interface PanelPosition {
  x: number;
  y: number;
}

const POSITION_KEY = "codex-webui.wsDebugPosition";

export function WsDebugPanel({ logs, wsState, onClear }: Props) {
  const panelRef = useRef<HTMLDetailsElement | null>(null);
  const dragRef = useRef<{ pointerId: number; offsetX: number; offsetY: number } | null>(null);
  const [position, setPosition] = useState<PanelPosition | null>(loadPosition);
  const [expandedId, setExpandedId] = useState<number | null>(null);
  const [copiedId, setCopiedId] = useState<number | null>(null);
  const latest = logs[0];

  useEffect(() => {
    if (position === null) {
      window.localStorage.removeItem(POSITION_KEY);
      return;
    }
    window.localStorage.setItem(POSITION_KEY, JSON.stringify(position));
  }, [position]);

  useEffect(() => {
    function clampCurrentPosition() {
      if (position === null) return;
      const panel = panelRef.current;
      const width = panel?.offsetWidth ?? 420;
      const height = panel?.offsetHeight ?? 42;
      const next = clampPosition(position, width, height);
      if (next.x !== position.x || next.y !== position.y) {
        setPosition(next);
      }
    }

    clampCurrentPosition();
    window.addEventListener("resize", clampCurrentPosition);
    return () => window.removeEventListener("resize", clampCurrentPosition);
  }, [position]);

  async function copyPayload(log: WsLogEntry) {
    await window.navigator.clipboard.writeText(log.payloadPreview ?? "");
    setCopiedId(log.id);
    window.setTimeout(() => setCopiedId((current) => (current === log.id ? null : current)), 1200);
  }

  function startDrag(event: PointerEvent<HTMLElement>) {
    if ((event.target as HTMLElement).closest("button")) return;
    const panel = panelRef.current;
    if (!panel) return;
    const rect = panel.getBoundingClientRect();
    dragRef.current = {
      pointerId: event.pointerId,
      offsetX: event.clientX - rect.left,
      offsetY: event.clientY - rect.top,
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setPosition({ x: rect.left, y: rect.top });
  }

  function drag(event: PointerEvent<HTMLElement>) {
    const dragState = dragRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;
    const panel = panelRef.current;
    const width = panel?.offsetWidth ?? 420;
    const height = panel?.offsetHeight ?? 42;
    setPosition(clampPosition({ x: event.clientX - dragState.offsetX, y: event.clientY - dragState.offsetY }, width, height));
  }

  function stopDrag(event: PointerEvent<HTMLElement>) {
    const dragState = dragRef.current;
    if (!dragState || dragState.pointerId !== event.pointerId) return;
    dragRef.current = null;
    event.currentTarget.releasePointerCapture(event.pointerId);
  }

  return (
    <details
      className="ws-debug"
      ref={panelRef}
      style={position ? { left: position.x, top: position.y, right: "auto", bottom: "auto" } : undefined}
    >
      <summary onPointerDown={startDrag} onPointerMove={drag} onPointerUp={stopDrag} onPointerCancel={stopDrag}>
        <span>
          <Bug size={14} />
          WS debug
        </span>
        <span className={`ws-debug-state ws-${wsState}`}>{wsState}</span>
        <span className="ws-debug-latest">{latest ? latest.eventType : "no events"}</span>
        <ChevronUp className="ws-open-icon" size={14} />
        <ChevronDown className="ws-closed-icon" size={14} />
      </summary>
      <div className="ws-debug-body">
        <div className="ws-debug-actions">
          <span>{logs.length} saved events</span>
          <span className="ws-debug-buttons">
            <button type="button" onClick={() => setPosition(null)} title="Reset websocket panel position">
              <LocateFixed size={14} />
            </button>
            <button type="button" onClick={onClear} title="Clear websocket logs">
              <Trash2 size={14} />
            </button>
          </span>
        </div>
        <div className="ws-debug-list">
          {logs.length === 0 ? <div className="ws-debug-empty">No websocket events yet.</div> : null}
          {logs.map((log) => {
            const expanded = expandedId === log.id;
            return (
              <div className={`ws-debug-entry ${expanded ? "expanded" : ""}`} key={log.id}>
                <button type="button" className="ws-debug-row" onClick={() => setExpandedId(expanded ? null : log.id)}>
                  <span>{formatTime(log.timestamp)}</span>
                  <strong>{log.eventType}</strong>
                  <span>{log.size === null ? "-" : `${formatBytes(log.size)}`}</span>
                  <em>{log.summary}</em>
                </button>
                {expanded ? (
                  <div className="ws-debug-payload">
                    <div className="ws-debug-payload-meta">
                      <span>{log.conversationId ? `conversation ${log.conversationId}` : log.direction}</span>
                      {log.payloadTruncated ? <span>truncated</span> : null}
                      <button type="button" onClick={() => void copyPayload(log)} title="Copy packet content">
                        {copiedId === log.id ? <Check size={13} /> : <Copy size={13} />}
                      </button>
                    </div>
                    <pre>{log.payloadPreview || "(empty packet)"}</pre>
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>
    </details>
  );
}

function loadPosition(): PanelPosition | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(POSITION_KEY) ?? "null");
    if (!value || typeof value.x !== "number" || typeof value.y !== "number") return null;
    return { x: value.x, y: value.y };
  } catch {
    return null;
  }
}

function formatTime(value: number) {
  return new Date(value).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}

function clamp(value: number, min: number, max: number) {
  return Math.min(max, Math.max(min, value));
}

function clampPosition(position: PanelPosition, width: number, height: number): PanelPosition {
  const maxX = Math.max(8, window.innerWidth - width - 8);
  const maxY = Math.max(8, window.innerHeight - height - 8);
  return {
    x: clamp(position.x, 8, maxX),
    y: clamp(position.y, 8, maxY),
  };
}
