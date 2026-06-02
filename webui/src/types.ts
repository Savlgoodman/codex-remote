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

export interface ThreadDetail {
  summary: ThreadSummary;
  messages: Message[];
  pagination: Record<string, unknown> | null;
}

export type ServerEvent =
  | ({ type: "ipc.status" } & IpcStatus)
  | { type: "threads.changed"; threads: ThreadSummary[] }
  | { type: "thread.snapshot"; conversationId: string; summary: ThreadSummary; messages: Message[] }
  | { type: "thread.patch"; conversationId: string; summary: ThreadSummary; patches: unknown[] };
