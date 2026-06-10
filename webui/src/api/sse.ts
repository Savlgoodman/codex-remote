import { API_BASE } from "./client";
import type { ServerEvent } from "../types";

export function openEvents(path: string, onEvent: (event: ServerEvent) => void, onState?: (open: boolean) => void): EventSource {
  const source = new EventSource(`${API_BASE}${path}`);
  source.onopen = () => onState?.(true);
  source.onerror = () => onState?.(false);
  const names = [
    "status.changed",
    "thread.upsert",
    "thread.snapshot",
    "thread.read",
    "turn.started",
    "turn.finished",
    "message.upsert",
    "message.patch",
    "message.append",
    "message.replace",
    "settings.changed",
    "token.changed",
    "resync.required",
  ];
  for (const name of names) {
    source.addEventListener(name, (message) => {
      onEvent(JSON.parse((message as MessageEvent).data) as ServerEvent);
    });
  }
  return source;
}
