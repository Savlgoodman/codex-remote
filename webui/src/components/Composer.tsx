import { FormEvent, useMemo, useState } from "react";
import { Send } from "lucide-react";
import type { ThreadSummary } from "../types";

interface Props {
  summary: ThreadSummary;
  ipcOnline: boolean;
  controlEnabled: boolean;
  onSend: (text: string) => Promise<void>;
}

export function Composer({ summary, ipcOnline, controlEnabled, onSend }: Props) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const willUseIpc = ipcOnline && summary.hasLiveOwner;
  const sendMode = willUseIpc ? "IPC owner" : "SDK resume";
  const disabledReason = useMemo(() => {
    if (!controlEnabled) return "Control disabled";
    if (
      willUseIpc &&
      summary.runtimeStatus !== "idle" &&
      summary.runtimeStatus !== "unknown" &&
      summary.latestTurnStatus !== "completed" &&
      summary.latestTurnStatus !== "-" &&
      summary.latestTurnStatus !== "failed"
    ) {
      return "Thread busy";
    }
    return null;
  }, [controlEnabled, summary, willUseIpc]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (!value || disabledReason || sending) return;
    setSending(true);
    try {
      await onSend(value);
      setText("");
    } finally {
      setSending(false);
    }
  }

  return (
    <form className="composer" onSubmit={submit}>
      <div className="composer-input">
        <div className="composer-mode">{disabledReason ?? `Send via ${sendMode}`}</div>
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={disabledReason ?? "Send a message"}
          disabled={Boolean(disabledReason)}
        />
      </div>
      <button className="send-button" disabled={Boolean(disabledReason) || !text.trim() || sending} title={disabledReason ?? "发送"}>
        <Send size={17} />
      </button>
    </form>
  );
}
