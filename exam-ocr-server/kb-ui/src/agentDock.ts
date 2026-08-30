import { agent, type AgentJob, type AgentRestore, type ChatAttachment, type ChatImage } from "./api";
import { latestPlanJobId, renderAgentTurn } from "./agentTurn";
import { mountComposer } from "./composer";
import { isImportChatJob, jobConversationId, pickLiveConversationId } from "./liveConv";
import { clearRestore, hardReload, isJob, onLive, readState, rememberRestore, rememberState, type LiveEvent, type RestoreNotice } from "./live";
import { chevron, closePopover, openPopover } from "./popover";
import { el } from "./util/dom";

const CID_KEY = "kb.agent.conversation_id";

export function mountAgentDock(host: HTMLElement): { destroy: () => void } {
  host.className = "kb-right-dock";
  const head = el("header", "kb-dock-head");
  const currentBtn = el("button", "kb-dock-current") as HTMLButtonElement;
  const newBtn = el("button", "kb-dock-iconbtn") as HTMLButtonElement;
  const foldBtn = el("button", "kb-dock-iconbtn") as HTMLButtonElement;
  currentBtn.type = "button";
  newBtn.type = "button";
  foldBtn.type = "button";
  currentBtn.title = "切换对话";
  newBtn.title = "新对话";
  newBtn.setAttribute("aria-label", "新对话");
  newBtn.innerHTML = plusSvg();
  foldBtn.title = "收起";
  foldBtn.setAttribute("aria-label", "收起");
  foldBtn.innerHTML = chevron("right");
  head.append(currentBtn, newBtn, foldBtn);

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
  type ConvRow = { id: string; title: string; status?: string; updated_at?: string };
  let convs: ConvRow[] = [];
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
  currentBtn.onclick = () => openConvMenu(currentBtn);
  paintHead();

  async function openConversation(id: string, nextTitle = ""): Promise<void> {
    cid = id;
    writeCid(id);
    setTitle(nextTitle);
    pendingId = "";
    pendingTurn = null;
    jobs = [];
    setCollapsed(false);
    stream.replaceChildren(emptyState());
    try {
      await loadThread(false);
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
    paintHead();
  }

  function currentLabel(): string {
    const cur = convs.find((row) => row.id === cid);
    return (cur?.title || title || "新对话").trim() || "新对话";
  }

  function paintHead(): void {
    const label = currentLabel();
    const cur = convs.find((row) => row.id === cid);
    const live = cur?.status === "running" || cur?.status === "queued";
    currentBtn.className = `kb-dock-current${live ? " is-live" : ""}`;
    currentBtn.title = `${label} · 切换对话`;
    currentBtn.replaceChildren();
    const ico = el("span", "kb-dock-current-ico");
    ico.innerHTML = live ? liveSvg() : chatSvg();
    currentBtn.append(ico, el("span", "kb-dock-current-title", label), chevronEl());
  }

  function openConvMenu(anchor: HTMLElement): void {
    closePopover();
    openPopover(anchor, (close) => convMenu(close), { width: 300, align: "right" });
    void refreshConvs();
  }

  async function refreshConvs(): Promise<void> {
    try {
      const listed = await agent.conversations();
      convs = (listed.conversations || []).map((row) => ({
        id: row.id,
        title: row.title,
        status: row.status,
        updated_at: row.updated_at,
      }));
      paintHead();
    } catch {
      /* keep local list */
    }
  }

  function convMenu(close: () => void): HTMLElement {
    const box = el("div", "kb-dock-convmenu");
    const search = document.createElement("input");
    search.type = "search";
    search.className = "kb-pop-search";
    search.placeholder = "搜索对话…";
    search.setAttribute("aria-label", "搜索对话");
    const list = el("div", "kb-pop-list");
    const paint = (): void => {
      list.replaceChildren();
      const q = search.value.trim().toLowerCase();
      const rows = uniqueConvs().filter((row) => {
        if (!q) return true;
        return (row.title || "").toLowerCase().includes(q);
      });
      if (!rows.length) {
        list.append(el("p", "muted kb-pop-empty", q ? "没有匹配的对话" : "还没有对话"));
        return;
      }
      let lastGroup = "";
      for (const row of rows) {
        const group = dateGroup(row.updated_at);
        if (group !== lastGroup) {
          lastGroup = group;
          list.append(el("p", "kb-pop-h", group));
        }
        list.append(convItem(row, close));
      }
    };
    search.addEventListener("input", paint);
    paint();
    box.append(search, list);
    window.setTimeout(() => search.focus({ preventScroll: true }), 0);
    return box;
  }

  function convItem(row: ConvRow, close: () => void): HTMLButtonElement {
    const live = row.status === "running" || row.status === "queued";
    const active = Boolean(row.id) && row.id === cid;
    const btn = el(
      "button",
      `kb-pop-item kb-dock-convitem${active ? " is-on" : ""}${live ? " is-live" : ""}`,
    ) as HTMLButtonElement;
    btn.type = "button";
    const top = el("span", "kb-pop-row");
    const ico = el("span", "kb-dock-convico");
    ico.innerHTML = active ? checkSvg() : live ? liveSvg() : chatSvg();
    top.append(ico, el("strong", "", row.title || "新对话"));
    btn.append(top);
    btn.onclick = () => {
      close();
      if (row.id && row.id !== cid) void openConversation(row.id, row.title);
    };
    return btn;
  }

  function uniqueConvs(): ConvRow[] {
    const seen = new Set<string>();
    const out: ConvRow[] = [];
    for (const row of convs) {
      if (!row.id || seen.has(row.id)) continue;
      seen.add(row.id);
      out.push(row);
    }
    if (cid && !seen.has(cid)) out.unshift({ id: cid, title: title || "新对话" });
    return out;
  }

  newBtn.onclick = () => {
    cid = "";
    pendingId = "";
    writeCid("");
    setTitle("");
    clearStream();
    composer.setBusy(false);
    composer.focus();
    paintHead();
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
    const latest = latestPlanJobId(jobs.filter((j) => sameThread(j)));
    renderAgentTurn(node, job, false, { latestPlan: job.job_id === latest });
    refreshPlanCards(latest);
    if (pin) stream.scrollTop = stream.scrollHeight;
    syncBusy();
  }

  function refreshPlanCards(latest: string): void {
    for (const job of jobs) {
      if (!sameThread(job) || job.job_id === latest) continue;
      const node = turnOf(job.job_id);
      if (!node?.querySelector(".kb-plan-card")) continue;
      renderAgentTurn(node, job, false, { latestPlan: false });
    }
  }

  function isLive(job: AgentJob): boolean {
    return job.status === "queued" || job.status === "running";
  }

  function liveJob(): AgentJob | undefined {
    return jobs.find((j) => sameThread(j) && isLive(j));
  }

  function stopTarget(): string {
    const live = liveJob();
    if (live) return live.job_id;
    // pendingId survives the turn it started, so it may point at a job that
    // already finished while an auto-resume or task turn is the live one.
    if (pendingId) {
      const known = jobs.find((j) => j.job_id === pendingId);
      if (!known || isLive(known)) return pendingId;
    }
    return pendingTurn?.dataset.jobId || "";
  }

  function syncBusy(): void {
    const busy = Boolean(pendingTurn) || Boolean(liveJob());
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
    const id = liveJob()?.job_id || pendingId || pendingTurn?.dataset.jobId || "";
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
    const importCid = isImportChatJob(job) ? jobConversationId(job) : "";
    if (importCid && importCid !== cid) {
      void openConversation(importCid, String(job.result?.title || ""));
      return;
    }
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
    if (nextTitle) title = nextTitle;
    if (cid && !convs.some((row) => row.id === cid)) {
      convs.unshift({
        id: cid,
        title: title || "新对话",
        status: job.status,
        updated_at: job.updated_at || new Date().toISOString(),
      });
    } else if (cid) {
      convs = convs.map((row) =>
        row.id === cid
          ? {
              ...row,
              title: title || row.title,
              status: job.status,
              updated_at: job.updated_at || row.updated_at,
            }
          : row,
      );
    }
    paintHead();
    paintJob(job);
    if (pendingId && job.job_id === pendingId && !isLive(job)) pendingId = "";
  }

  async function loadThread(preferLive = false): Promise<void> {
    try {
      const listed = await agent.conversations();
      const rows = listed.conversations || [];
      convs = rows.map((row) => ({
        id: row.id,
        title: row.title,
        status: row.status,
        updated_at: row.updated_at,
      }));
      const liveId = preferLive ? pickLiveConversationId(rows) : "";
      if (liveId) {
        cid = liveId;
        writeCid(liveId);
      } else if (!cid) {
        const last = rows[0];
        if (last?.id) {
          cid = last.id;
          writeCid(last.id);
        }
      }
      const cur = rows.find((row) => row.id === cid);
      if (cur?.title) title = cur.title;
      paintHead();
    } catch {
      /* keep saved cid */
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
    isBusy: () => composerBusy(),
    onStop: async () => {
      const id = stopTarget();
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
      } finally {
        // A job the server has already dropped would otherwise leave the
        // composer wedged in its stop state with nothing left to stop.
        if (pendingId === id) pendingId = "";
        if (!liveJob()) {
          pendingTurn = null;
          composer.setBusy(false);
        }
      }
    },
    onSubmit: async (body) => {
      await sendChat({
        message: body.message,
        mode: body.mode,
        model: body.model,
        model_params: body.model_params,
        images: body.images,
        files: body.files,
        attachments: body.attachments,
      });
    },
  });

  async function sendChat(body: {
    message: string;
    mode?: "agent" | "plan" | "ask";
    model?: string;
    plan_id?: string;
    model_params?: { id: string; value: string }[];
    images?: ChatImage[];
    files?: { data: string; name: string; mime_type: string }[];
    attachments?: ChatAttachment[];
  }): Promise<void> {
    stream.querySelector(".kb-dock-empty")?.remove();
    stream.querySelector(".kb-dock-rewind")?.remove();
    clearRestore();
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
        plan_id: body.plan_id,
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
        plan_id: body.plan_id || undefined,
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
  }

  void (async () => {
    try {
      await loadThread(true);
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
    window.addEventListener("kb:restore-applied", onRestore as EventListener);
    window.addEventListener("kb:continue-breakpoint", onContinue);
    window.addEventListener("kb:approval-note", onApprovalNote as EventListener);
    window.addEventListener("kb:execute-plan", onExecutePlan as EventListener);
    applySavedRewind();
    stopLive = onLive((ev: LiveEvent) => {
      if (ev.type === "reload" || (ev.type === "checkpoint" && ev.action === "fork")) {
        hardReload();
        return;
      }
      if (ev.type === "checkpoint" && ev.action === "restore") {
        void applyRestore(ev as unknown as AgentRestore);
        return;
      }
      if (
        ev.type === "task" &&
        ev.action === "emit" &&
        (ev.event === "kb.import.completed" || ev.event === "task.import-page-refine.completed")
      ) {
        void followImportConversation(ev);
        return;
      }
      if (isJob(ev)) ingest(ev as unknown as AgentJob);
    });
  })();

  function onRestore(ev: Event): void {
    const detail = (ev as CustomEvent<AgentRestore>).detail;
    if (detail) void applyRestore(detail);
  }

  function onContinue(): void {
    if (composer && !composerBusy()) {
      composer.setDraft("继续", false);
      composer.submit();
    }
  }

  function onApprovalNote(ev: Event): void {
    const text = String((ev as CustomEvent<{ text?: string }>).detail?.text || "").trim();
    if (!text || !composer || composerBusy()) return;
    composer.setDraft(text);
    composer.submit();
  }

  function onExecutePlan(ev: Event): void {
    const detail = (ev as CustomEvent<{
      plan_id?: string;
      path?: string;
      title?: string;
      markdown?: string;
      conversation_id?: string;
    }>).detail || {};
    if (composerBusy()) return;
    const path = String(detail.path || "").trim();
    const title = String(detail.title || "执行计划");
    const markdown = String(detail.markdown || "").trim();
    const planId = String(detail.plan_id || "").trim();
    const nextCid = String(detail.conversation_id || "").trim();
    if (nextCid) {
      cid = nextCid;
      writeCid(nextCid);
    }
    const message = path
      ? `请按工作区里的计划执行：${path}\n先读该文件，再按步骤改工作区。不要另写一份计划。`
      : markdown
        ? `请按这份计划执行。不要另写一份计划。\n\n${markdown}`
        : `请按这份计划执行：${title}`;
    void sendChat({ message, mode: "agent", plan_id: planId || undefined });
  }

  function composerBusy(): boolean {
    return Boolean(pendingTurn) || Boolean(liveJob());
  }

  function applySavedRewind(): void {
    const saved = readState();
    if (saved.restoreNotice?.kept_plan) {
      clearRestore();
      return;
    }
    if (saved.restoreNotice || saved.composerDraft) {
      showRewind(saved.composerDraft || saved.restoreNotice?.message || "", saved.restoreNotice);
    }
  }

  async function applyRestore(out: AgentRestore): Promise<void> {
    const nextCid = String(out.conversation_id || cid || "").trim();
    if (nextCid) {
      cid = nextCid;
      writeCid(nextCid);
    }
    pendingId = "";
    pendingTurn = null;
    rememberRestore(cid, String(out.message || ""), {
      changes: out.changes || [],
      removed: (out.removed_job_ids || []).length,
      kept_plan: Boolean(out.kept_plan),
    });
    try {
      await loadThread();
    } catch {
      /* keep current */
    }
    if (!out.kept_plan) {
      showRewind(String(out.message || ""), {
        message: String(out.message || ""),
        changes: out.changes || [],
        removed: (out.removed_job_ids || []).length,
      });
    } else {
      clearRestore();
    }
    try {
      const files = await agent.books();
      composer.setFiles(files.files || []);
    } catch {
      /* ignore */
    }
    composer.setBusy(false);
  }

  function showRewind(message: string, notice?: RestoreNotice | null): void {
    stream.querySelector(".kb-dock-empty")?.remove();
    stream.querySelector(".kb-dock-rewind")?.remove();
    const box = el("div", "kb-dock-rewind");
    box.append(el("p", "kb-dock-rewind-title", "已回到这一轮开始前"));
    const removed = notice?.removed || 0;
    const hint = removed
      ? `后面 ${removed} 轮已从对话拿掉。改完这条再发送，就会重新开始。`
      : "改完这条再发送，就会重新开始。";
    box.append(el("p", "muted", hint));
    const changes = notice?.changes || [];
    if (changes.length) {
      const list = el("ul", "kb-dock-rewind-files");
      for (const row of changes.slice(0, 40)) {
        const item = el("li", "");
        item.append(el("span", "kb-dock-rewind-op", row.label || "变动"), el("code", "", row.path));
        list.append(item);
      }
      if (changes.length > 40) list.append(el("li", "muted", `还有 ${changes.length - 40} 个文件`));
      box.append(list);
    } else {
      box.append(el("p", "muted", "工作区文件没有变动。"));
    }
    const draft = el("textarea", "kb-dock-rewind-input") as HTMLTextAreaElement;
    draft.rows = 3;
    draft.value = message;
    draft.setAttribute("aria-label", "回退后的用户消息");
    const go = el("button", "btn kb-dock-rewind-go", "重新开始") as HTMLButtonElement;
    go.type = "button";
    const sync = (): void => composer.setDraft(draft.value, false);
    draft.addEventListener("input", sync);
    go.onclick = () => {
      composer.setDraft(draft.value);
      composer.submit();
      box.remove();
    };
    box.append(draft, go);
    stream.append(box);
    stream.scrollTop = stream.scrollHeight;
    composer.setDraft(message);
  }

  async function followImportConversation(ev: LiveEvent): Promise<void> {
    const ids = Array.isArray(ev.conversation_ids)
      ? ev.conversation_ids.map((id) => String(id || "").trim()).filter(Boolean)
      : [];
    if (ids[0] && ids[0] !== cid) {
      await openConversation(ids[0]);
      return;
    }
    if (Number(ev.started) <= 0) return;
    try {
      const listed = await agent.conversations();
      const hit = (listed.conversations || []).find((row) =>
        String(row.preview || "").includes("教材已导入完成"),
      );
      const next = String(hit?.id || "").trim();
      if (next && next !== cid) await openConversation(next, hit?.title || "");
    } catch {
      /* job ingest will switch */
    }
  }

  function onOpenConv(ev: Event): void {
    const detail = (ev as CustomEvent<{ id?: string; title?: string }>).detail || {};
    const id = String(detail.id || "").trim();
    if (!id) return;
    void openConversation(id, String(detail.title || ""));
  }

  return {
    destroy() {
      window.removeEventListener("kb:open-conversation", onOpenConv as EventListener);
      window.removeEventListener("kb:restore-applied", onRestore as EventListener);
      window.removeEventListener("kb:continue-breakpoint", onContinue);
      window.removeEventListener("kb:approval-note", onApprovalNote as EventListener);
      window.removeEventListener("kb:execute-plan", onExecutePlan as EventListener);
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
  rememberState({ conversationId: id });
}

function dateGroup(iso?: string): string {
  const day = shanghaiDay(iso);
  const today = shanghaiDay(new Date().toISOString());
  if (day && day === today) return "今天";
  if (!day) return "更早";
  const t = Date.parse(`${today}T00:00:00+08:00`);
  const x = Date.parse(`${day}T00:00:00+08:00`);
  const days = (t - x) / 86400000;
  if (days >= 0 && days < 7) return "近 7 天";
  return "更早";
}

function shanghaiDay(iso?: string): string {
  const d = iso ? new Date(iso) : new Date();
  if (Number.isNaN(d.getTime())) return "";
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(d);
}

function chevronEl(): HTMLElement {
  const n = el("span", "kb-dock-chevron");
  n.innerHTML = chevron("down");
  return n;
}

function plusSvg(): string {
  return `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" d="M8 3.2v9.6M3.2 8h9.6"/></svg>`;
}

function chatSvg(): string {
  return `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.3" d="M3.2 3.6h9.6v7.2H7.1L4.2 13V10.8H3.2z"/></svg>`;
}

function checkSvg(): string {
  return `<svg viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><circle cx="8" cy="8" r="5.2" fill="none" stroke="currentColor" stroke-width="1.3"/><path fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" d="m5.4 8.1 1.8 1.8 3.4-3.5"/></svg>`;
}

function liveSvg(): string {
  return `<svg viewBox="0 0 16 16" width="14" height="14" aria-hidden="true"><circle cx="8" cy="8" r="3.2" fill="currentColor"/></svg>`;
}
