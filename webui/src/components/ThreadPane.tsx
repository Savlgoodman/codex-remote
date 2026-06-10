import { ArrowUp, CheckCircle2, Copy, Loader2, Paperclip, RefreshCcw, ShieldAlert, Square } from "lucide-react";
import { FormEvent, useState } from "react";
import type { DetailState } from "../store/eventReducer";
import { sendMessage } from "../api/client";

type Props = {
  detail: DetailState | null;
  loading: boolean;
  error: string | null;
};

export function ThreadPane({ detail, loading, error }: Props) {
  const [text, setText] = useState("");
  const [sendError, setSendError] = useState<string | null>(null);
  const busy = detail?.summary.runtimeStatus === "active" || detail?.summary.latestTurnStatus === "inProgress";
  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!detail || !text.trim()) return;
    setSendError(null);
    try {
      await sendMessage(detail.summary.conversationId, text, detail.summary.sandboxType === "dangerFullAccess");
      setText("");
    } catch (err) {
      setSendError(err instanceof Error ? err.message : "send_failed");
    }
  }
  return (
    <main className="main-pane">
      <header className="thread-header">
        <div className="thread-heading">
          <strong>{detail?.summary.title ?? "Codex Remote"}</strong>
          {detail?.summary.source && <span className={`source-pill ${detail.summary.source}`}>{detail.summary.source}</span>}
        </div>
        <div className="header-actions">
          {detail?.sseConnected ? <CheckCircle2 size={15} /> : <Loader2 size={15} />}
          <button className="icon-button" title="刷新">
            <RefreshCcw size={15} />
          </button>
        </div>
      </header>
      <section className="message-viewport">
        {loading && <div className="empty-state">加载中...</div>}
        {error && <div className="empty-state error">{error}</div>}
        {!loading && !detail && !error && <div className="empty-state">暂无会话，等待 IPC 同步。</div>}
        {detail && (
          <div className="message-column">
            {detail.messages.map((message) => (
              <article className={`message ${message.role}`} key={message.id}>
                <div className="message-body">{message.text || (message.role === "reasoning" ? "思考中..." : "")}</div>
                {message.role === "assistant" && (
                  <div className="message-tools">
                    <button title="复制">
                      <Copy size={14} />
                    </button>
                  </div>
                )}
              </article>
            ))}
          </div>
        )}
      </section>
      <form className="composer" onSubmit={onSubmit}>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={detail ? "要求后续变更" : "选择一个会话"}
          disabled={!detail || busy}
          rows={1}
        />
        <div className="composer-bar">
          <div className="composer-left">
            <button className="icon-button" type="button" title="附件">
              <Paperclip size={17} />
            </button>
            <span className={`permission ${detail?.summary.sandboxType === "dangerFullAccess" ? "danger" : ""}`}>
              <ShieldAlert size={15} />
              {permissionLabel(detail?.summary.sandboxType)}
            </span>
            {sendError && <span className="send-error">{sendError}</span>}
          </div>
          <div className="composer-right">
            <span>{detail?.summary.latestModel ?? "-"}</span>
            <span>{detail?.summary.latestReasoningEffort ?? "-"}</span>
            <button className="send-button" type="submit" disabled={!detail || busy || !text.trim()} title={busy ? "运行中" : "发送"}>
              {busy ? <Square size={16} /> : <ArrowUp size={17} />}
            </button>
          </div>
        </div>
      </form>
    </main>
  );
}

function permissionLabel(value: string | null | undefined) {
  if (value === "dangerFullAccess") return "完全访问";
  if (value === "workspaceWrite") return "工作区";
  if (value === "readOnly") return "只读";
  return "权限";
}

