import { Bot, Folder, MessageSquare, Plus, Search, Settings, SlidersHorizontal, Zap } from "lucide-react";
import type { ThreadSummary } from "../types";

type Props = {
  threads: ThreadSummary[];
  selectedId: string | null;
  onSelect: (conversationId: string) => void;
};

export function Sidebar({ threads, selectedId, onSelect }: Props) {
  const grouped = groupByCwd(threads);
  return (
    <aside className="sidebar">
      <nav className="primary-nav">
        <button className="nav-row" title="新对话">
          <Plus size={16} />
          <span>新对话</span>
        </button>
        <button className="nav-row" title="搜索">
          <Search size={16} />
          <span>搜索</span>
        </button>
        <button className="nav-row" title="插件">
          <Bot size={16} />
          <span>插件</span>
        </button>
        <button className="nav-row" title="自动化">
          <Zap size={16} />
          <span>自动化</span>
        </button>
      </nav>
      <div className="sidebar-section-title">项目</div>
      <div className="thread-groups">
        {grouped.map((group) => (
          <section className="thread-group" key={group.cwd}>
            <div className="project-row">
              <Folder size={16} />
              <span>{projectName(group.cwd)}</span>
            </div>
            {group.threads.map((thread) => (
              <button
                className={`thread-row ${thread.conversationId === selectedId ? "active" : ""}`}
                key={thread.conversationId}
                onClick={() => onSelect(thread.conversationId)}
                title={thread.title ?? thread.conversationId}
              >
                <MessageSquare size={14} />
                <span className="thread-title">{thread.title || "(untitled)"}</span>
                <span className="thread-time">{relativeTime(thread.activeAt ?? thread.updatedAt)}</span>
              </button>
            ))}
          </section>
        ))}
      </div>
      <div className="sidebar-footer">
        <button className="nav-row" title="设置">
          <Settings size={16} />
          <span>设置</span>
        </button>
        <button className="icon-button" title="筛选">
          <SlidersHorizontal size={16} />
        </button>
      </div>
    </aside>
  );
}

function groupByCwd(threads: ThreadSummary[]) {
  const map = new Map<string, ThreadSummary[]>();
  for (const thread of threads) {
    const cwd = thread.cwd || "无项目";
    map.set(cwd, [...(map.get(cwd) ?? []), thread]);
  }
  return [...map.entries()].map(([cwd, rows]) => ({ cwd, threads: rows }));
}

function projectName(cwd: string) {
  if (cwd === "无项目") return cwd;
  const parts = cwd.split(/[\\/]/).filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : cwd;
}

function relativeTime(value: number | null | undefined) {
  if (!value) return "";
  const seconds = Math.max(0, Date.now() / 1000 - value);
  if (seconds < 60) return "刚刚";
  if (seconds < 3600) return `${Math.floor(seconds / 60)} 分`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} 小时`;
  return `${Math.floor(seconds / 86400)} 天`;
}
