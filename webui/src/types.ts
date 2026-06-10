export type ThreadSource = "live" | "stale" | "history-only";
export type RuntimeStatus = "idle" | "active" | "unknown";
export type TurnStatus = "inProgress" | "completed" | "interrupted" | "failed" | "unknown" | "-";
export type MessageRole = "user" | "assistant" | "reasoning" | "tool" | "command" | "system";

export type ServerStatus = {
  ipc: {
    online: boolean;
    client_id?: string | null;
    clientId?: string | null;
    connected_at?: number | null;
    connectedAt?: number | null;
    last_seen_at?: number | null;
    lastSeenAt?: number | null;
    last_error?: string | null;
    lastError?: string | null;
  };
  sdk: {
    available: boolean;
    lastRefreshAt?: number | null;
    lastError?: string | null;
  };
};

export type ThreadSummary = {
  conversationId: string;
  title: string | null;
  cwd: string | null;
  source: ThreadSource;
  ownerSourceClientId: string | null;
  hasLiveOwner: boolean;
  runtimeStatus: RuntimeStatus;
  latestTurnStatus: TurnStatus;
  latestModel: string | null;
  latestReasoningEffort: string | null;
  approvalPolicy: string | null;
  sandboxType: string | null;
  latestPreview: string | null;
  updatedAt: number | null;
  activeAt: number | null;
  tokenTotal: number | null;
};

export type ThreadSettings = {
  model: string | null;
  reasoningEffort: string | null;
  approvalPolicy: string | null;
  approvalsReviewer: string | null;
  sandboxType: string | null;
  serviceTier: string | null;
  permissions: string | null;
};

export type Message = {
  id: string;
  conversationId: string;
  turnId: string | null;
  role: MessageRole;
  phase: string | null;
  text: string;
  status: string | null;
  createdAt: number | null;
  updatedAt: number | null;
  ordinal: number;
};

export type ThreadDetail = {
  summary: ThreadSummary;
  settings: ThreadSettings;
  messages: Message[];
  turns: Array<Record<string, unknown>>;
  rawRevision: number | null;
  rolloutPath: string | null;
};

export type ServerEvent = {
  type: string;
  eventId: number;
  time: number;
  conversationId: string | null;
  payload: Record<string, unknown>;
};

