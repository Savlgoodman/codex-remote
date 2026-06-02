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
  const disabledReason = useMemo(() => {
    if (!controlEnabled) return "Control disabled";
    if (!ipcOnline) return "IPC offline";
    if (!summary.hasLiveOwner) return "No live owner";
    if (summary.runtimeStatus !== "idle" && summary.latestTurnStatus !== "completed" && summary.latestTurnStatus !== "-") return "Thread busy";
    return null;
  }, [controlEnabled, ipcOnline, summary]);

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
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        placeholder={disabledReason ?? "Send a message through the App/VSCode IPC owner"}
        disabled={Boolean(disabledReason)}
      />
      <button className="send-button" disabled={Boolean(disabledReason) || !text.trim() || sending} title={disabledReason ?? "发送"}>
        <Send size={17} />
      </button>
    </form>
  );
}
