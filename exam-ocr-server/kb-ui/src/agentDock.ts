import { agent, type AgentJob } from "./api";
import { renderAgentTurn } from "./agentTurn";
import { mountComposer } from "./composer";
import { hardReload, isJob, onLive, type LiveEvent } from "./live";
import { el } from "./util/dom";

const CID_KEY = "kb.agent.conversation_id";

export function mountAgentDock(host: HTMLElement): { destroy: () => void } {
  host.className = "kb-right-dock";
  const head = el("header", "kb-dock-head");
  const newBtn = el("button", "kb-dock-iconbtn", "+") as HTMLButtonElement;
  const foldBtn = el("button", "kb-dock-iconbtn", "⟩") as HTMLButtonElement;
  newBtn.type = "button";
  foldBtn.type = "button";
  newBtn.title = "新对话";
  foldBtn.title = "收起";
  const titleEl = el("span", "kb-dock-title", "新对话");
  head.append(titleEl, newBtn, foldBtn);

  const body = el("div", "kb-dock-body");
  const stream = el("div", "kb-dock-stream");
  stream.append(emptyState());
  const form = el("form", "kb-dock-composer");
  body.append(stream, form);

  const openBtn = el("button", "kb-dock-open hidden", "对话") as HTMLButtonElement;
  openBtn.type = "button";
  host.append(head, body, openBtn);

  const app = () => host.closest(".kb-app") || host.parentElement;
  let cid = readCid();
  let title = "";
  let jobs: AgentJob[] = [];
  let pendingId = "";
  let pendingTurn: HTMLElement | null = null;
  let stopLive: (() => void) | null = null;
  let pollTimer = 0;

  function setCollapsed(on: boolean): void {
    app()?.classList.toggle("is-dock-collapsed", on);
    host.classList.toggle("is-collapsed", on);
    body.classList.toggle("hidden", on);
    head.classList.toggle("hidden", on);
    openBtn.classList.toggle("hidden", !on);
  }

  foldBtn.onclick = () => setCollapsed(true);
  openBtn.onclick = () => setCollapsed(false);

  async function openConversation(id: string, nextTitle = ""): Promise<void> {
    cid = id;
    writeCid(id);
    setTitle(nextTitle);
    pendingId = "";
    pendingTurn = null;
    setCollapsed(false);
    stream.replaceChildren(emptyState());
    try {
      await loadThread();
    } catch {
      /* keep empty */
    }
  }

  function clearStream(): void {
    pendingTurn = null;
    stream.replaceChildren(emptyState());
  }

  function setTitle(next: string): void {
    title = (next || "").trim();
    titleEl.textContent = title || "新对话";
    titleEl.title = title || "新对话";
  }

  newBtn.onclick = () => {
    cid = "";
    pendingId = "";
    writeCid("");
    setTitle("");
    clearStream();
    composer.setBusy(false);
    composer.focus();
  };

  function turnOf(jobId: string): HTMLElement | null {
    return stream.querySelector<HTMLElement>(`[data-job-id="${cssEscape(jobId)}"]`);
  }

  function paintJob(job: AgentJob): void {
    stream.querySelector(".kb-dock-empty")?.remove();
    let node = turnOf(job.job_id);
    if (
      pendingTurn &&
      (pendingTurn === node ||
        pendingTurn.dataset.jobId === job.job_id ||
        pendingTurn.dataset.jobId === "pending")
    ) {
      if (!node) {
        node = pendingTurn;
        node.dataset.jobId = job.job_id;
      }
      pendingTurn = null;
    }
    if (!node) {
      node = el("article", "kb-turn");
      node.dataset.jobId = job.job_id;
      stream.append(node);
    }
    const pin = stream.scrollHeight - stream.scrollTop - stream.clientHeight < 56;
    renderAgentTurn(node, job);
    if (pin) stream.scrollTop = stream.scrollHeight;
    syncBusy();
  }

  function syncBusy(): void {
    const busy =
      Boolean(pendingTurn) ||
      jobs.some((j) => sameThread(j) && (j.status === "queued" || j.status === "running"));
    composer.setBusy(busy);
    if (busy && !pollTimer) {
      pollTimer = window.setInterval(() => {
        void refreshActive();
      }, 2500);
    }
    if (!busy && pollTimer) {
      window.clearInterval(pollTimer);
      pollTimer = 0;
    }
  }

  async function refreshActive(): Promise<void> {
    const id = pendingId || pendingTurn?.dataset.jobId || "";
    if (!id || id === "pending") return;
    try {
      ingest(await agent.job(id));
    } catch {
      /* next tick */
    }
  }

  function sameThread(job: AgentJob): boolean {
    if (job.kind !== "chat") return false;
    if (pendingId && job.job_id === pendingId) return true;
    if (!cid) return false;
    const payloadCid = String(job.payload?.conversation_id || "");
    const resultCid = String(job.result?.conversation_id || "");
    return payloadCid === cid || resultCid === cid;
  }

  function ingest(job: AgentJob): void {
    const idx = jobs.findIndex((j) => j.job_id === job.job_id);
    if (idx >= 0) jobs[idx] = job;
    else jobs.unshift(job);
    if (!sameThread(job)) return;
    if (!cid) {
      const got = String(job.payload?.conversation_id || job.result?.conversation_id || "").trim();
      if (got && got !== "none" && got !== "local") {
        cid = got;
        writeCid(got);
      }
    }
    const nextTitle = String(job.result?.title || "").trim();
    if (nextTitle) setTitle(nextTitle);
    paintJob(job);
  }

  async function loadThread(): Promise<void> {
    if (!cid) {
      try {
        const listed = await agent.conversations();
        const last = (listed.conversations || [])[0];
        if (last?.id) {
          cid = last.id;
          writeCid(last.id);
          setTitle(last.title || "");
        }
      } catch {
        /* empty */
      }
    }
    if (!cid) return;
    try {
      const thread = await agent.conversation(cid);
      cid = thread.conversation?.id || cid;
      writeCid(cid);
      setTitle(thread.conversation?.title || "");
      jobs = thread.jobs || [];
    } catch {
      jobs = [];
    }
    if (!jobs.length) {
      stream.replaceChildren(emptyState());
      return;
    }
    stream.replaceChildren();
    for (const job of jobs) paintJob(job);
  }

  const composer = mountComposer(form, {
    onReview: () => window.dispatchEvent(new CustomEvent("kb:tab", { detail: "agent" })),
    isBusy: () =>
      Boolean(pendingTurn) ||
      jobs.some((j) => sameThread(j) && (j.status === "queued" || j.status === "running")),
    onStop: async () => {
      const id =
        pendingId ||
        jobs.find((j) => sameThread(j) && (j.status === "queued" || j.status === "running"))?.job_id ||
        pendingTurn?.dataset.jobId ||
        "";
      if (!id || id === "pending") {
        if (pendingTurn) {
          renderAgentTurn(pendingTurn, {
            job_id: "pending",
            kind: "chat",
            status: "cancelled",
            payload: {},
            result: null,
            error: "",
            trace: [],
            created_at: "",
            updated_at: "",
          });
          pendingTurn = null;
        }
        composer.setBusy(false);
        return;
      }
      try {
        ingest(await agent.cancelJob(id));
      } catch (e) {
        window.alert((e as Error).message || "停止失败");
      }
    },
    onSubmit: async (body) => {
      stream.querySelector(".kb-dock-empty")?.remove();
      composer.setBusy(true);
      pendingTurn = el("article", "kb-turn");
      pendingTurn.dataset.jobId = "pending";
      renderAgentTurn(pendingTurn, {
        job_id: "pending",
        kind: "chat",
        status: "queued",
        payload: {
          message: body.message,
          mode: body.mode,
          model: body.model,
          model_params: body.model_params,
          attachments: body.attachments,
        },
        result: null,
        error: "",
        trace: [],
        created_at: "",
        updated_at: "",
      });
      stream.append(pendingTurn);
      stream.scrollTop = stream.scrollHeight;
      try {
        const out = await agent.chat({
          message: body.message,
          conversation_id: cid || undefined,
          mode: body.mode,
          model: body.model || undefined,
          model_params: body.model_params,
          images: body.images,
          files: body.files,
        });
        pendingId = out.job_id;
        if (out.conversation_id) {
          cid = out.conversation_id;
          writeCid(out.conversation_id);
        }
        if (out.title) setTitle(out.title);
        if (pendingTurn) pendingTurn.dataset.jobId = out.job_id;
      } catch (e) {
        renderAgentTurn(pendingTurn, {
          job_id: "pending",
          kind: "chat",
          status: "failed",
          payload: { message: body.message, mode: body.mode, attachments: body.attachments },
          result: null,
          error: (e as Error).message || "发送失败",
          trace: [],
          created_at: "",
          updated_at: "",
        });
        pendingTurn = null;
        syncBusy();
      }
    },
  });

  void (async () => {
    try {
      await loadThread();
    } catch {
      /* empty thread is fine */
    }
    try {
      const files = await agent.books();
      composer.setFiles(files.files || []);
    } catch {
      /* ignore */
    }
    window.addEventListener("kb:open-conversation", onOpenConv as EventListener);
    stopLive = onLive((ev: LiveEvent) => {
      if (ev.type === "reload" || (ev.type === "checkpoint" && (ev.action === "restore" || ev.action === "fork"))) {
        hardReload();
        return;
      }
      if (isJob(ev)) ingest(ev as unknown as AgentJob);
    });
  })();

  function onOpenConv(ev: Event): void {
    const detail = (ev as CustomEvent<{ id?: string; title?: string }>).detail || {};
    const id = String(detail.id || "").trim();
    if (!id) return;
    void openConversation(id, String(detail.title || ""));
  }

  return {
    destroy() {
      window.removeEventListener("kb:open-conversation", onOpenConv as EventListener);
      stopLive?.();
      stopLive = null;
      if (pollTimer) {
        window.clearInterval(pollTimer);
        pollTimer = 0;
      }
    },
  };
}

function emptyState(): HTMLElement {
  const box = el("div", "kb-dock-empty");
  box.append(el("p", "kb-dock-empty-title", "Agent"));
  box.append(el("p", "muted", "查教材、改规矩，或给自己加一个小工具。"));
  return box;
}

function cssEscape(s: string): string {
  return s.replace(/["\\]/g, "\\$&");
}

function readCid(): string {
  try {
    return window.localStorage.getItem(CID_KEY) || "";
  } catch {
    return "";
  }
}

function writeCid(id: string): void {
  try {
    if (id) window.localStorage.setItem(CID_KEY, id);
    else window.localStorage.removeItem(CID_KEY);
  } catch {
    /* ignore */
  }
}
