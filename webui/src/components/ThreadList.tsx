import type { ThreadSummary } from "../types";

interface Props {
  threads: ThreadSummary[];
  selectedId: string | null;
  onSelect: (conversationId: string) => void;
}

export function ThreadList({ threads, selectedId, onSelect }: Props) {
  const groups = groupByCwd(threads);
  return (
    <div className="thread-list">
      {groups.map((group) => (
        <section className="thread-group" key={group.cwd}>
          <div className="group-title">{group.cwd || "No cwd"}</div>
          {group.threads.map((thread) => (
            <button
              className={`thread-row ${thread.conversationId === selectedId ? "selected" : ""}`}
              key={thread.conversationId}
              onClick={() => onSelect(thread.conversationId)}
            >
              <span className={`live-dot source-${thread.source}`} />
              <span className="thread-main">
                <span className="thread-title">{thread.title}</span>
                <span className="thread-preview">{thread.latestItemPreview || thread.runtimeStatus}</span>
              </span>
              <span className="thread-time">{formatTime(thread.activeAt ?? thread.updatedAt)}</span>
            </button>
          ))}
        </section>
      ))}
    </div>
  );
}

function groupByCwd(threads: ThreadSummary[]) {
  const map = new Map<string, ThreadSummary[]>();
  for (const thread of threads) {
    const key = thread.cwd || "";
    map.set(key, [...(map.get(key) ?? []), thread]);
  }
  return [...map.entries()].map(([cwd, rows]) => ({ cwd, threads: rows }));
}

function formatTime(value: number | null | undefined) {
  if (!value) return "";
  return new Date(value * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

