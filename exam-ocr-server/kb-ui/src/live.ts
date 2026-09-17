import { agent, type AgentJob } from "./api";

export type LiveEvent = {
  type?: string;
  job_id?: string;
  generation?: number;
  slots?: string[];
  action?: string;
  note?: string;
} & Record<string, unknown>;

export type RestoreNotice = {
  message?: string;
  changes?: { path: string; change: string; label: string }[];
  removed?: number;
  kept_plan?: boolean;
};

export type LiveState = {
  tab?: string;
  conversationId?: string;
  scrollY?: number;
  composerDraft?: string;
  restoreNotice?: RestoreNotice;
  kbExpandedBooks?: string[];
  kbExpandedGroups?: string[];
  kbExpandedToc?: Record<string, string[]>;
  kbIrExpandedBooks?: string[];
  kbIrExpandedToc?: Record<string, string[]>;
  kbBookSort?: string;
};

type Handler = (ev: LiveEvent) => void;

const KEY = "kb.live.state";
const handlers = new Set<Handler>();
let es: EventSource | null = null;
let started = false;

export function rememberState(partial: LiveState): void {
  try {
    const prev = readState();
    sessionStorage.setItem(KEY, JSON.stringify({ ...prev, ...partial }));
  } catch {
    /* ignore */
  }
}

export function clearRestore(): void {
  try {
    const prev = readState();
    delete prev.composerDraft;
    delete prev.restoreNotice;
    sessionStorage.setItem(KEY, JSON.stringify(prev));
  } catch {
    /* ignore */
  }
}

export function rememberRestore(
  conversationId: string,
  message: string,
  notice: RestoreNotice,
): void {
  rememberState({
    conversationId,
    composerDraft: message,
    restoreNotice: { message, ...notice },
  });
}

export function readState(): LiveState {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return {};
    const data = JSON.parse(raw) as LiveState;
    return data && typeof data === "object" ? data : {};
  } catch {
    return {};
  }
}

export function hardReload(): void {
  rememberState({ scrollY: window.scrollY });
  window.location.reload();
}

export function isJob(ev: LiveEvent): ev is LiveEvent & AgentJob {
  return Boolean(ev.job_id) && (!ev.type || ev.type === "job");
}

export function onLive(fn: Handler): () => void {
  startLive();
  handlers.add(fn);
  return () => {
    handlers.delete(fn);
  };
}

export function startLive(): void {
  if (started) return;
  started = true;
  try {
    es = new EventSource(agent.eventsUrl());
    es.onmessage = (raw) => {
      try {
        const ev = JSON.parse(String(raw.data || "{}")) as LiveEvent;
        for (const fn of [...handlers]) {
          try {
            fn(ev);
          } catch {
            /* listener error */
          }
        }
      } catch {
        /* keepalive */
      }
    };
    es.onerror = () => {
      /* browser reconnects EventSource */
    };
  } catch {
    started = false;
  }
}

export function stopLive(): void {
  es?.close();
  es = null;
  started = false;
  handlers.clear();
}
