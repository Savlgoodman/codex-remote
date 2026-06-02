import type { SendOptions, ServerStatus, ThreadDetail, ThreadSummary } from "./types";

export const API_BASE = import.meta.env.VITE_CODEX_SERVER_URL ?? "http://127.0.0.1:8765";

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

export async function getThreads(): Promise<ThreadSummary[]> {
  const data = await requestJson<{ threads: ThreadSummary[] }>("/api/threads");
  return data.threads;
}

export async function getThreadDetail(conversationId: string): Promise<ThreadDetail> {
  return requestJson<ThreadDetail>(`/api/threads/${encodeURIComponent(conversationId)}`);
}

export async function sendMessage(conversationId: string, text: string, confirmDangerFullAccess = false, options: SendOptions = {}): Promise<unknown> {
  return requestJson(`/api/threads/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ text, confirmDangerFullAccess, ...compactOptions(options) }),
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

