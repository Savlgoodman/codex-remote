import { FormEvent, useEffect, useMemo, useState } from "react";
import { Send } from "lucide-react";
import type { SendOptions, ThreadSummary } from "../types";

interface Props {
  summary: ThreadSummary;
  ipcOnline: boolean;
  controlEnabled: boolean;
  onSend: (text: string, options?: SendOptions) => Promise<void>;
  onSettingsChange?: (options: SendOptions, confirmDangerFullAccess?: boolean) => Promise<void>;
}

export function Composer({ summary, ipcOnline, controlEnabled, onSend, onSettingsChange }: Props) {
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const [model, setModel] = useState("");
  const [reasoningEffort, setReasoningEffort] = useState("inherit");
  const [approvalPolicy, setApprovalPolicy] = useState("inherit");
  const [sandboxMode, setSandboxMode] = useState("inherit");
  const liveOwnerReady = ipcOnline && summary.hasLiveOwner;
  const disabledReason = useMemo(() => {
    if (!controlEnabled) return "Control disabled";
    if (
      liveOwnerReady &&
      summary.runtimeStatus !== "idle" &&
      summary.runtimeStatus !== "unknown" &&
      summary.latestTurnStatus !== "completed" &&
      summary.latestTurnStatus !== "-" &&
      summary.latestTurnStatus !== "failed"
    ) {
      return "Thread busy";
    }
    return null;
  }, [controlEnabled, summary, liveOwnerReady]);

  useEffect(() => {
    setModel(summary.latestModel ?? "");
    setReasoningEffort(summary.latestReasoningEffort ?? "inherit");
    setApprovalPolicy(summary.approvalPolicy ?? "inherit");
    setSandboxMode(summary.sandboxMode ?? "inherit");
  }, [summary.approvalPolicy, summary.latestModel, summary.latestReasoningEffort, summary.sandboxMode]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const value = text.trim();
    if (!value || disabledReason || sending) return;
    setSending(true);
    try {
      await onSend(value, { model: model.trim(), reasoningEffort, approvalPolicy, sandboxMode });
      setText("");
    } finally {
      setSending(false);
    }
  }

  function settings(overrides: SendOptions = {}): SendOptions {
    return {
      model: model.trim(),
      reasoningEffort,
      approvalPolicy,
      sandboxMode,
      ...overrides,
    };
  }

  function syncSettings(next: SendOptions, confirmDangerFullAccess = false) {
    if (disabledReason) return;
    void onSettingsChange?.(next, confirmDangerFullAccess);
  }

  return (
    <form className="composer" onSubmit={submit}>
      <div className="composer-input">
        <div className="composer-mode">
          {disabledReason ?? (liveOwnerReady ? "Backend routes to IPC owner" : "Backend routes to SDK resume")}
        </div>
        <div className="composer-settings">
          <label>
            <span>Model</span>
            <input
              value={model}
              onChange={(event) => {
                setModel(event.target.value);
              }}
              onBlur={() => syncSettings(settings())}
              placeholder="inherit"
              disabled={Boolean(disabledReason)}
            />
          </label>
          <label>
            <span>Reasoning</span>
            <select
              value={reasoningEffort}
              onChange={(event) => {
                const value = event.target.value;
                setReasoningEffort(value);
                syncSettings(settings({ reasoningEffort: value }));
              }}
              disabled={Boolean(disabledReason)}
            >
              <option value="inherit">inherit</option>
              <option value="none">none</option>
              <option value="minimal">minimal</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="xhigh">xhigh</option>
            </select>
          </label>
          <label>
            <span>Approval</span>
            <select
              value={approvalPolicy}
              onChange={(event) => {
                const value = event.target.value;
                setApprovalPolicy(value);
                syncSettings(settings({ approvalPolicy: value }));
              }}
              disabled={Boolean(disabledReason)}
            >
              <option value="inherit">inherit</option>
              <option value="on-request">on request</option>
              <option value="never">never</option>
            </select>
          </label>
          <label>
            <span>Sandbox</span>
            <select
              value={sandboxMode}
              onChange={(event) => {
                const value = event.target.value;
                let confirmDangerFullAccess = false;
                if (value === "danger-full-access" && sandboxMode !== value) {
                  confirmDangerFullAccess = window.confirm("This settings change enables full access. Continue?");
                  if (!confirmDangerFullAccess) return;
                }
                setSandboxMode(value);
                syncSettings(settings({ sandboxMode: value }), confirmDangerFullAccess);
              }}
              disabled={Boolean(disabledReason)}
            >
              <option value="inherit">inherit</option>
              <option value="read-only">read only</option>
              <option value="workspace-write">workspace write</option>
              <option value="workspace-write-network">workspace + network</option>
              <option value="danger-full-access">full access</option>
            </select>
          </label>
        </div>
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
