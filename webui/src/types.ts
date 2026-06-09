export type ThreadSource = "live" | "stale" | "history-only";
export type MessageRole = "user" | "assistant" | "system" | "tool" | "command" | "reasoning";

export interface IpcStatus {
  online: boolean;
  clientId: string | null;
  connectedAt: number | null;
  lastError: string | null;
  lastSeenAt: number | null;
}

export interface ServerStatus {
  ipc: IpcStatus;
  sdk: {
    available: boolean;
    lastError: string | null;
  };
  control?: {
    enabled: boolean;
  };
  time: number;
}

export interface ThreadSummary {
  conversationId: string;
  title: string;
  cwd: string;
  source: ThreadSource;
  runtimeStatus: string;
  latestTurnStatus: string;
  latestItemPreview: string;
  activeAt: number | null;
  updatedAt: number | null;
  hasLiveOwner: boolean;
  latestModel: string | null;
  latestReasoningEffort: string | null;
  approvalPolicy: string | null;
  sandboxMode: string | null;
}

export interface Message {
  id: string;
  role: MessageRole;
  text: string;
  status: string | null;
  createdAt: number | null;
  raw?: Record<string, unknown>;
}

export interface SendOptions {
  model?: string;
  reasoningEffort?: string;
  approvalPolicy?: string;
  sandboxMode?: string;
}

export interface SendMessageRequest {
  conversationId: string;
  text: string;
  confirmDangerFullAccess?: boolean;
  options?: SendOptions;
}

export type SendMode = "ipc-owner" | "sdk-background";

export interface MessageRoute {
  mode: SendMode;
  reason: string;
}

export interface SendMessageResponse {
  ok: boolean;
  mode: SendMode;
  reason: string;
  route: MessageRoute;
  ipcResponse?: unknown;
}

export interface ThreadSettingsResponse {
  ok: boolean;
  summary: ThreadSummary | null;
  ipcSync?: unknown;
}

export interface ReadMessagesQuery {
  conversationId: string;
}

export interface WsLogEntry {
  id: number;
  timestamp: number;
  direction: "system" | "in";
  eventType: string;
  size: number | null;
  conversationId?: string;
  summary?: string;
  payloadPreview?: string;
  payloadTruncated?: boolean;
}

export interface IpcRawEvent {
  type: "ipc.raw";
  version?: number;
  direction: "in" | "out";
  timestamp: number;
  size: number;
  ipcType: string;
  method: string | null;
  requestId: string | null;
  conversationId: string | null;
  summary: string;
  payload: unknown;
  payloadPreview: string;
  payloadTruncated: boolean;
}

export interface ThreadDetail {
  summary: ThreadSummary;
  messages: Message[];
  pagination: Record<string, unknown> | null;
}

export type ServerEvent =
  | ({ type: "ipc.status"; version?: number } & IpcStatus)
  | { type: "ipc.monitor.status"; version?: number; capturing: boolean }
  | IpcRawEvent
  | { type: "threads.snapshot"; version?: number; reason?: string; threads: ThreadSummary[] }
  | { type: "thread.summary"; version?: number; conversationId: string; summary: ThreadSummary }
  | { type: "thread.message.upsert"; version?: number; conversationId: string; message: Message }
  | { type: "thread.snapshot"; version?: number; reason?: string; conversationId: string; summary: ThreadSummary; messages: Message[] };
