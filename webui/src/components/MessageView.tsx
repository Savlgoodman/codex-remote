import type { Message } from "../types";

interface Props {
  message: Message;
}

export function MessageView({ message }: Props) {
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

