import type { Message } from "../types";

interface Props {
  message: Message;
}

export function MessageView({ message }: Props) {
  if (message.role === "tool") {
    const tool = toolInfo(message);
    return (
      <article className="message message-tool">
        <div className="message-meta">
          <span>{tool.kind}</span>
          {tool.name ? <span>{tool.name}</span> : null}
          {message.status ? <span>{message.status}</span> : null}
        </div>
        <div className="tool-body">
          {tool.summary ? <div className="tool-summary">{tool.summary}</div> : null}
          {tool.details.map((detail) => (
            <div className="tool-detail" key={detail.label}>
              <span>{detail.label}</span>
              <code>{detail.value}</code>
            </div>
          ))}
        </div>
      </article>
    );
  }

  return (
    <article className={`message message-${message.role}`}>
      <div className="message-meta">
        <span>{roleLabel(message.role)}</span>
        {message.status ? <span>{message.status}</span> : null}
      </div>
      <pre>{message.text || "(empty)"}</pre>
    </article>
  );
}

function roleLabel(role: Message["role"]) {
  if (role === "assistant") return "agent";
  if (role === "command") return "command";
  if (role === "reasoning") return "reasoning";
  return role;
}

function toolInfo(message: Message) {
  const raw = unwrapRoot(message.raw);
  const type = stringValue(raw?.type ?? raw?.kind) || "tool";
  if (type === "webSearch") {
    return {
      kind: "web search",
      name: actionLabel(raw?.action),
      summary: stringValue(raw?.query) || firstLine(message.text),
      details: detailRows([{ label: "query", value: raw?.query }]),
    };
  }
  if (type === "mcpToolCall" || type === "toolCall") {
    const server = stringValue(raw?.server);
    const tool = stringValue(raw?.tool ?? raw?.name ?? raw?.toolName ?? raw?.method);
    return {
      kind: "tool call",
      name: [server, tool].filter(Boolean).join("."),
      summary: firstLine(message.text),
      details: detailRows([
        { label: "args", value: raw?.arguments },
        { label: "result", value: raw?.result },
        { label: "error", value: raw?.error },
      ]),
    };
  }
  if (type === "dynamicToolCall") {
    const namespace = stringValue(raw?.namespace);
    const tool = stringValue(raw?.tool ?? raw?.name);
    return {
      kind: "tool call",
      name: [namespace, tool].filter(Boolean).join("."),
      summary: firstLine(message.text),
      details: detailRows([
        { label: "args", value: raw?.arguments },
        { label: "output", value: raw?.contentItems ?? raw?.content_items },
      ]),
    };
  }
  if (type === "fileChange") {
    const changes = Array.isArray(raw?.changes) ? raw.changes.filter(isRecord) : [];
    const fileRows = changes
      .map((change) => {
        const kind = stringValue(change.kind) || "update";
        const path = stringValue(change.path ?? change.newPath ?? change.oldPath) || "-";
        const summary = diffSummaryLabel(change.diffSummary);
        return `${kind}: ${path}${summary ? ` (${summary})` : ""}`;
      })
      .join("\n");
    const previews = changes
      .map((change) => stringValue(change.diffPreview))
      .filter(Boolean)
      .join("\n\n");
    return {
      kind: "code changes",
      name: changes.length ? `${changes.length} file${changes.length === 1 ? "" : "s"}` : "",
      summary: firstLine(message.text),
      details: detailRows([
        { label: "files", value: fileRows },
        { label: "preview", value: previews },
      ]),
    };
  }
  return {
    kind: typeLabel(type),
    name: stringValue(raw?.tool ?? raw?.name ?? raw?.id),
    summary: firstLine(message.text),
    details: detailRows([{ label: "details", value: message.text }]),
  };
}

function unwrapRoot(value: unknown): Record<string, unknown> | undefined {
  if (!isRecord(value)) return undefined;
  const root = value.root;
  return isRecord(root) ? root : value;
}

function actionLabel(value: unknown) {
  const action = unwrapRoot(value);
  return stringValue(action?.type);
}

function detailRows(rows: Array<{ label: string; value: unknown }>) {
  return rows
    .map((row) => ({ label: row.label, value: compactValue(row.value) }))
    .filter((row) => row.value);
}

function compactValue(value: unknown) {
  if (value === null || value === undefined || value === "") return "";
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 520 ? `${text.slice(0, 519)}...` : text;
}

function firstLine(text: string) {
  return text.split("\n").map((part) => part.trim()).find(Boolean) ?? "";
}

function stringValue(value: unknown) {
  return typeof value === "string" ? value : "";
}

function diffSummaryLabel(value: unknown) {
  if (!isRecord(value)) return "";
  const added = numberValue(value.added);
  const removed = numberValue(value.removed);
  const chunks = numberValue(value.chunks);
  const parts = [];
  if (added) parts.push(`+${added}`);
  if (removed) parts.push(`-${removed}`);
  if (chunks) parts.push(`${chunks} chunk${chunks === 1 ? "" : "s"}`);
  return parts.join(" ");
}

function numberValue(value: unknown) {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function typeLabel(type: string) {
  return type.replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase();
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

