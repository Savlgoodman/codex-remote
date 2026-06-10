import type { ServerStatus, ThreadDetail, ThreadSummary } from "../types";

export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://127.0.0.1:7000";

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    let message = response.statusText;
    try {
      const body = await response.json();
      message = body?.detail?.error ?? body?.detail ?? body?.error ?? message;
    } catch {
      // Keep status text.
    }
    throw new Error(String(message));
  }
  return response.json() as Promise<T>;
}

export function getStatus(): Promise<ServerStatus> {
  return requestJson<ServerStatus>("/api/status");
}

export async function listThreads(): Promise<ThreadSummary[]> {
  const data = await requestJson<{ threads: ThreadSummary[] }>("/api/threads");
  return data.threads;
}

export function getThread(conversationId: string): Promise<ThreadDetail> {
  return requestJson<ThreadDetail>(`/api/threads/${encodeURIComponent(conversationId)}`);
}

export function sendMessage(conversationId: string, text: string, confirmDangerFullAccess = false): Promise<unknown> {
  return requestJson(`/api/threads/${encodeURIComponent(conversationId)}/messages`, {
    method: "POST",
    body: JSON.stringify({ text, confirmDangerFullAccess }),
  });
}

