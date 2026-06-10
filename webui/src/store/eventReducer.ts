import type { Message, ServerEvent, ServerStatus, ThreadDetail, ThreadSettings, ThreadSummary } from "../types";

export type DetailState = ThreadDetail & {
  sseConnected: boolean;
  needsResync: boolean;
};

export type AppState = {
  status: ServerStatus | null;
  threads: ThreadSummary[];
  details: Record<string, DetailState>;
};

export const initialState: AppState = {
  status: null,
  threads: [],
  details: {},
};

export type Action =
  | { type: "status.loaded"; status: ServerStatus }
  | { type: "threads.loaded"; threads: ThreadSummary[] }
  | { type: "thread.loaded"; detail: ThreadDetail }
  | { type: "detail.sse"; conversationId: string; connected: boolean }
  | { type: "event"; event: ServerEvent };

export function reducer(state: AppState, action: Action): AppState {
  switch (action.type) {
    case "status.loaded":
      return { ...state, status: action.status };
    case "threads.loaded":
      return { ...state, threads: sortThreads(action.threads) };
    case "thread.loaded":
      return {
        ...state,
        details: {
          ...state.details,
          [action.detail.summary.conversationId]: {
            ...action.detail,
            sseConnected: state.details[action.detail.summary.conversationId]?.sseConnected ?? false,
            needsResync: false,
          },
        },
      };
    case "detail.sse": {
      const detail = state.details[action.conversationId];
      if (!detail) return state;
      return {
        ...state,
        details: {
          ...state.details,
          [action.conversationId]: { ...detail, sseConnected: action.connected },
        },
      };
    }
    case "event":
      return applyServerEvent(state, action.event);
  }
}

function applyServerEvent(state: AppState, event: ServerEvent): AppState {
  if (event.type === "status.changed") {
    return {
      ...state,
      status: {
        ...(state.status ?? { ipc: { online: false }, sdk: { available: false } }),
        ...(event.payload as Partial<ServerStatus>),
      } as ServerStatus,
    };
  }
  if (event.type === "thread.upsert") {
    const summary = (event.payload as { summary?: ThreadSummary }).summary;
    if (!summary) return state;
    const nextThreads = upsertThread(state.threads, summary);
    const detail = state.details[summary.conversationId];
    return {
      ...state,
      threads: nextThreads,
      details: detail
        ? {
            ...state.details,
            [summary.conversationId]: { ...detail, summary },
          }
        : state.details,
    };
  }
  const conversationId = event.conversationId;
  if (!conversationId) return state;
  const detail = state.details[conversationId];
  if (!detail) return state;
  if (event.type === "thread.snapshot") {
    const payload = event.payload as unknown as ThreadDetail;
    return {
      ...state,
      details: {
        ...state.details,
        [conversationId]: {
          ...payload,
          sseConnected: detail.sseConnected,
          needsResync: false,
        },
      },
    };
  }
  if (event.type === "message.upsert") {
    const message = (event.payload as { message?: Message }).message;
    if (!message) return state;
    return updateDetail(state, conversationId, upsertMessage(detail, message));
  }
  if (event.type === "message.patch") {
    const payload = event.payload as { messageId?: string; changes?: Partial<Message> };
    if (!payload.messageId || !payload.changes) return state;
    const messages = detail.messages.map((message) =>
      message.id === payload.messageId ? { ...message, ...payload.changes, text: message.text } : message,
    );
    return updateDetail(state, conversationId, { ...detail, messages });
  }
  if (event.type === "message.append") {
    const payload = event.payload as { messageId?: string; delta?: string; text?: string };
    if (!payload.messageId) return state;
    const messages = detail.messages.map((message) =>
      message.id === payload.messageId
        ? { ...message, text: payload.text ?? `${message.text}${payload.delta ?? ""}` }
        : message,
    );
    return updateDetail(state, conversationId, { ...detail, messages });
  }
  if (event.type === "message.replace") {
    const payload = event.payload as { messageId?: string; text?: string };
    if (!payload.messageId) return state;
    const messages = detail.messages.map((message) =>
      message.id === payload.messageId ? { ...message, text: payload.text ?? "" } : message,
    );
    return updateDetail(state, conversationId, { ...detail, messages });
  }
  if (event.type === "turn.started" || event.type === "turn.finished") {
    const payload = event.payload as { turn?: Record<string, unknown> };
    if (!payload.turn) return state;
    const turn = payload.turn;
    const turnId = typeof turn.id === "string" ? turn.id : null;
    const status = typeof turn.status === "string" ? turn.status : null;
    const turns = upsertTurn(detail.turns, turn);
    const messages =
      turnId && status
        ? detail.messages.map((message) => (message.turnId === turnId ? { ...message, status } : message))
        : detail.messages;
    return updateDetail(state, conversationId, { ...detail, turns, messages });
  }
  if (event.type === "settings.changed") {
    return updateDetail(state, conversationId, {
      ...detail,
      settings: { ...detail.settings, ...(event.payload as Partial<ThreadSettings>) },
    });
  }
  if (event.type === "resync.required") {
    return updateDetail(state, conversationId, { ...detail, needsResync: true });
  }
  return state;
}

function updateDetail(state: AppState, conversationId: string, detail: DetailState): AppState {
  return {
    ...state,
    details: {
      ...state.details,
      [conversationId]: detail,
    },
  };
}

function upsertThread(threads: ThreadSummary[], summary: ThreadSummary): ThreadSummary[] {
  const next = threads.filter((thread) => thread.conversationId !== summary.conversationId);
  next.push(summary);
  return sortThreads(next);
}

function sortThreads(threads: ThreadSummary[]): ThreadSummary[] {
  return [...threads].sort((a, b) => (b.activeAt ?? b.updatedAt ?? 0) - (a.activeAt ?? a.updatedAt ?? 0));
}

function upsertMessage(detail: DetailState, message: Message): DetailState {
  const next = detail.messages.filter((item) => item.id !== message.id);
  next.push(message);
  next.sort((a, b) => a.ordinal - b.ordinal);
  return { ...detail, messages: next };
}

function upsertTurn(turns: Array<Record<string, unknown>>, turn: Record<string, unknown>): Array<Record<string, unknown>> {
  const turnId = typeof turn.id === "string" ? turn.id : null;
  const turnIndex = typeof turn.index === "number" ? turn.index : null;
  const next = turns.filter((item) => {
    if (turnIndex !== null && item.index === turnIndex) return false;
    if (turnId !== null && item.id === turnId) return false;
    return true;
  });
  next.push(turn);
  return next.sort((a, b) => Number(a.index ?? 0) - Number(b.index ?? 0));
}
