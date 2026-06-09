import { Check, Copy, Pause, Play, Trash2 } from "lucide-react";
import { useState } from "react";
import type { IpcRawEvent } from "../types";

interface Props {
  capturing: boolean;
  events: IpcRawEvent[];
  onStart: () => Promise<void>;
  onPause: () => Promise<void>;
  onClear: () => void;
}

export function IpcMonitorPanel({ capturing, events, onStart, onPause, onClear }: Props) {
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);

  async function copyPayload(id: string, text: string) {
    await window.navigator.clipboard.writeText(text);
    setCopiedId(id);
    window.setTimeout(() => setCopiedId((current) => (current === id ? null : current)), 1200);
  }

  return (
    <section className="ipc-monitor">
      <header className="ipc-monitor-head">
        <div>
          <strong>IPC monitor</strong>
          <span>{capturing ? "capturing" : "paused"}</span>
        </div>
        <div className="ipc-monitor-actions">
          {capturing ? (
            <button type="button" onClick={() => void onPause()} title="Pause IPC capture">
              <Pause size={14} />
            </button>
          ) : (
            <button type="button" onClick={() => void onStart()} title="Start IPC capture">
              <Play size={14} />
            </button>
          )}
          <button type="button" onClick={onClear} title="Clear IPC capture">
            <Trash2 size={14} />
          </button>
        </div>
      </header>
      <div className="ipc-monitor-list">
        {events.length === 0 ? <div className="ipc-monitor-empty">No IPC packets captured.</div> : null}
        {events.map((event) => {
          const id = eventId(event);
          const expanded = expandedId === id;
          return (
            <article className={`ipc-entry ipc-${typeClass(event)}`} key={id}>
              <button type="button" className="ipc-entry-row" onClick={() => setExpandedId(expanded ? null : id)}>
                <span className={`ipc-direction ipc-${event.direction}`}>{event.direction}</span>
                <span>{formatTime(event.timestamp)}</span>
                <strong>{event.ipcType}</strong>
                <span>{event.method ?? "-"}</span>
                <em>{event.summary}</em>
                <span>{formatBytes(event.size)}</span>
              </button>
              {expanded ? (
                <div className="ipc-entry-payload">
                  <div className="ipc-entry-meta">
                    <span>{event.conversationId ? `conversation ${event.conversationId}` : event.requestId ?? event.direction}</span>
                    {event.payloadTruncated ? <span>preview truncated</span> : null}
                    <button type="button" onClick={() => void copyPayload(id, event.payloadPreview)} title="Copy IPC payload">
                      {copiedId === id ? <Check size={13} /> : <Copy size={13} />}
                    </button>
                  </div>
                  <pre>{formatPayload(event)}</pre>
                </div>
              ) : null}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function eventId(event: IpcRawEvent) {
  return `${event.timestamp}-${event.direction}-${event.requestId ?? event.method ?? event.ipcType}`;
}

function typeClass(event: IpcRawEvent) {
  if (event.ipcType === "thread.summary") return "thread-summary";
  if (event.ipcType === "broadcast") return "broadcast";
  if (event.ipcType === "request") return "request";
  if (event.ipcType === "response") return "response";
  if (event.ipcType.includes("discovery")) return "discovery";
  return "other";
}

function formatPayload(event: IpcRawEvent) {
  try {
    return JSON.stringify(event.payload, null, 2);
  } catch {
    return event.payloadPreview;
  }
}

function formatTime(value: number) {
  return new Date(value * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function formatBytes(value: number) {
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
