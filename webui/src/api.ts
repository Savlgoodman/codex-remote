import type {
  ReadMessagesQuery,
  SendMessageRequest,
  SendMessageResponse,
  SendOptions,
  ServerStatus,
  ThreadDetail,
  ThreadSettingsResponse,
  ThreadSummary,
} from "./types";

export const API_BASE = import.meta.env.VITE_CODEX_SERVER_URL ?? "http://127.0.0.1:7002";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data?.message || data?.detail || response.statusText;
    throw Object.assign(new Error(String(message)), { data, status: response.status });
  }
  return data as T;
}

export async function getStatus(): Promise<ServerStatus> {
  return requestJson<ServerStatus>("/api/status");
}

export async function refreshThreads(): Promise<{ ok: boolean; threads: number }> {
  return requestJson("/api/refresh", { method: "POST", body: "{}" });
}

export async function startIpcMonitor(): Promise<{ ok: boolean; capturing: boolean }> {
  return requestJson("/api/ipc-monitor/start", { method: "POST", body: "{}" });
}

export async function pauseIpcMonitor(): Promise<{ ok: boolean; capturing: boolean }> {
  return requestJson("/api/ipc-monitor/pause", { method: "POST", body: "{}" });
}

export async function getThreads(): Promise<ThreadSummary[]> {
  const data = await requestJson<{ threads: ThreadSummary[] }>("/api/threads");
  return data.threads;
}

export async function getThreadDetail(query: ReadMessagesQuery): Promise<ThreadDetail> {
  return requestJson<ThreadDetail>(`/api/threads/${encodeURIComponent(query.conversationId)}`);
}

export async function sendMessage(command: SendMessageRequest): Promise<SendMessageResponse> {
  return requestJson<SendMessageResponse>(`/api/threads/${encodeURIComponent(command.conversationId)}/messages`, {
    method: "POST",
    body: JSON.stringify({
      text: command.text,
      confirmDangerFullAccess: command.confirmDangerFullAccess ?? false,
      ...compactOptions(command.options ?? {}),
    }),
  });
}

export async function updateThreadSettings(
  conversationId: string,
  options: SendOptions,
  confirmDangerFullAccess = false,
): Promise<ThreadSettingsResponse> {
  return requestJson<ThreadSettingsResponse>(`/api/threads/${encodeURIComponent(conversationId)}/settings`, {
    method: "POST",
    body: JSON.stringify({
      confirmDangerFullAccess,
      ...options,
    }),
  });
}

export function websocketUrl(): string {
  const url = new URL(API_BASE);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/api/events";
  url.search = "";
  return url.toString();
}

function compactOptions(options: SendOptions): SendOptions {
  return Object.fromEntries(Object.entries(options).filter(([, value]) => value && value !== "inherit")) as SendOptions;
}

