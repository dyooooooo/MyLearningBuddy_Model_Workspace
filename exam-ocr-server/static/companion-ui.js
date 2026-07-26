import {
  ACTION_LABELS,
  ACTION_LABELS_SHORT,
  clearOverlay,
  drawOverlay,
  escapeHtml,
  overlayFromParts,
  renderMasteryPanel,
  renderHomeworkQuestions,
  renderOcrComparePanel,
  renderWrongBookPanel,
  countOcrPrintChanges,
  renderPage,
} from "./render-utils.js?v=20260622evalfinish";
import { getLocale, setLocale } from "./i18n.js";
import { isImageFile, normalizeImageFile } from "./image-api.js";

const API = "/api/v1/companion";
const LS_STUDENT = "lb_student_id";
const STUDENT_ID_RE = /^[a-z][a-z0-9_-]{2,31}$/;

function parseEvalUpdated(detail) {
  const m = String(detail || "").match(/eval_updated=(\d+)/);
  return m ? parseInt(m[1], 10) : -1;
}

function normalizeStudentId(raw) {
  return (raw || "")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, "-")
    .replace(/[^a-z0-9_-]/g, "");
}

function validateStudentId(sid) {
  if (!sid) return "请输入学生 ID";
  if (!STUDENT_ID_RE.test(sid)) {
    return "格式：小写字母开头，3–32 位，仅 a-z、0-9、_、-（例：bowen、student-01）";
  }
  return null;
}

function formatStudentError(detail, status, knownIds = []) {
  const raw = typeof detail === "string" ? detail : JSON.stringify(detail || {});
  let innerDetail = raw;
  if (raw.startsWith("Buddy admin failed:")) {
    try {
      const inner = JSON.parse(raw.replace(/^Buddy admin failed:\s*/, ""));
      innerDetail = inner.detail || raw;
    } catch {
      innerDetail = raw;
    }
  }
  if (/invalid student id/i.test(innerDetail)) {
    return "学生 ID 无效：须小写字母开头（如 bowen，不是 Bowen）";
  }
  if (/max students/i.test(innerDetail)) {
    const hint = knownIds.length
      ? `请从已有 ID 中选择：${knownIds.join("、")}`
      : "请改用已有学生 ID（如 student-01），或联系管理员清理测试账号";
    return `试用档学生名额已满（最多 5 个）。${hint}`;
  }
  return innerDetail || raw || `学生注册失败 (${status})`;
}

const TAB_HELP = {
  merged: "同一页多帧 OCR 累积合并后的最终文本（预览区默认不叠框，便于阅读）",
  frame: "当前选中帧的单次 OCR；勾选「显示识别框」可查看 bbox",
  compare: "并排对比 Vision OCR 草稿与 OCR-refine 结果，仅看印刷体 print 是否被改正",
  homework: "已定稿的作业（A 层）。需先点底部「定稿当前页」或「结束会话」",
  mastery: "A⁺ 掌握度：考纲/题库 RAG 映射知识点，定稿后聚合；可标错题、手动调整",
  wrongbook: "错题本：Agent 自动判错 + 人工补加/改判",
  agentruns: "M3 Agent 编排：定稿 ingest 后 LangGraph 运行步骤",
};

export function createCompanionUI(els) {
  let knownStudentIds = [];

  async function refreshStudentPicker() {
    try {
      const res = await fetch(`${API}/buddy/students`);
      if (!res.ok) return;
      const data = await res.json();
      knownStudentIds = (data.students || []).map((s) => s.id).filter(Boolean);
      const dl = els.studentIdList;
      if (dl) {
        dl.innerHTML = knownStudentIds
          .map((id) => `<option value="${escapeHtml(id)}"></option>`)
          .join("");
      }
    } catch {
      /* ignore */
    }
  }
  function getStudentId() {
    const fromInput = els.studentIdInput?.value;
    const normalized = normalizeStudentId(fromInput);
    if (normalized) return normalized;
    return normalizeStudentId(localStorage.getItem(LS_STUDENT) || "") || "student-01";
  }

  function applyStudentIdFromInput() {
    if (!els.studentIdInput) return null;
    const raw = els.studentIdInput.value;
    const sid = normalizeStudentId(raw);
    if (sid && sid !== raw.trim()) {
      els.studentIdInput.value = sid;
    }
    return sid;
  }

  function saveStudentId() {
    const sid = applyStudentIdFromInput() || getStudentId();
    if (sid) localStorage.setItem(LS_STUDENT, sid);
  }

  function apiHeaders(extra = {}) {
    return { "X-Student-Id": getStudentId(), "X-Locale": getLocale(), ...extra };
  }

  function showStudentBanner(message, kind = "warn") {
    const el = els.studentBanner;
    if (!el) return;
    if (!message) {
      el.hidden = true;
      el.textContent = "";
      return;
    }
    el.hidden = false;
    el.className = `student-banner ${kind}`;
    el.textContent = message;
  }

  async function ensureStudentRegistered() {
    const sid = applyStudentIdFromInput() || getStudentId();
    const invalid = validateStudentId(sid);
    if (invalid) {
      showStudentBanner(invalid, "err");
      return false;
    }
    saveStudentId();
    try {
      const res = await fetch(`${API}/buddy/ensure-student`, {
        method: "POST",
        headers: apiHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ id: sid, label: sid, grade_band: "hs" }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        if (/max students/i.test(String(err.detail || ""))) {
          await refreshStudentPicker();
        }
        showStudentBanner(formatStudentError(err.detail, res.status, knownStudentIds), "err");
        return false;
      }
      const data = await res.json();
      showStudentBanner(
        data.created ? `已自动注册 ${sid}（trial）` : `学生 ${sid} 已就绪 · ${data.tier || "trial"}`,
        "ok",
      );
      return true;
    } catch (e) {
      showStudentBanner(e.message || "无法连接 Buddy API", "err");
      return false;
    }
  }

  const state = {
    sessionId: null,
    session: null,
    batchQueue: [],
    selectedQueueKey: null,
    processing: false,
    batchAbort: null,
    batchTotal: 0,
    batchDone: 0,
    objectUrl: null,
    selectedFrameId: null,
    sideTab: "merged",
    showOverlay: false,
    hideDoneQueue: true,
    viewingPendingUpload: false,
    framePreviewCache: new Map(),
    workflowStep: 1,
    previewToken: 0,
    processingPreviewUrl: null,
    homeworkDetailId: null,
    pipelineTiming: null,
    tierInfo: null,
    hwView: null,
    locale: getLocale(),
  };

  function wireQuestionCardActions(root = els.resultBody) {
    root.querySelectorAll(".btn-goto-wb").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        void setSideTab("wrongbook");
      });
    });
    root.querySelectorAll(".btn-edit-q").forEach((btn) => {
      btn.addEventListener("click", () => void editQuestionFields(btn.dataset.qid));
    });
    root.querySelectorAll(".btn-del-q").forEach((btn) => {
      btn.addEventListener("click", () => void deleteQuestion(btn.dataset.qid));
    });
  }

  function wireWrongBookActions() {
    const root = els.resultBody;
    root.querySelectorAll(".btn-wb-re-eval").forEach((btn) => {
      btn.addEventListener("click", () => void reEvalWrongBook(false));
    });
    root.querySelectorAll(".btn-wb-re-eval-all").forEach((btn) => {
      btn.addEventListener("click", () => {
        if (!confirm(getLocale() === "en" ? "Re-eval ALL homework for this student?" : "确定对该学生全部作业重新 Agent 评测？")) return;
        void reEvalWrongBook(true);
      });
    });
    root.querySelectorAll(".btn-wb-add").forEach((btn) => {
      btn.addEventListener("click", () => void patchQuestionWrong(btn.dataset.qid, true));
    });
    root.querySelectorAll(".btn-wb-remove").forEach((btn) => {
      btn.addEventListener("click", () => void patchQuestionWrong(btn.dataset.qid, false));
    });
    root.querySelectorAll(".btn-wb-restore").forEach((btn) => {
      btn.addEventListener("click", () => void restoreAgentVerdict(btn.dataset.qid));
    });
  }

  function wireMasteryActions() {
    els.resultBody.querySelectorAll(".mastery-select").forEach((sel) => {
      sel.addEventListener("change", () => patchMastery(sel.dataset.kp, sel.value));
    });
    els.resultBody.querySelectorAll(".btn-del-kp").forEach((btn) => {
      btn.addEventListener("click", () => void deleteMastery(btn.dataset.kp));
    });
    els.resultBody.querySelectorAll(".btn-add-kp").forEach((btn) => {
      btn.addEventListener("click", () => void showAddKpDialog());
    });
    els.resultBody.querySelectorAll(".btn-recompute-mastery").forEach((btn) => {
      btn.addEventListener("click", () => void recomputeMastery());
    });
  }

  async function fetchTierInfo() {
    try {
      const res = await fetch(`${API}/buddy/tier`, { headers: apiHeaders() });
      if (res.ok) {
        state.tierInfo = await res.json();
      }
    } catch {
      state.tierInfo = null;
    }
    return state.tierInfo;
  }

  function renderQuestionTimingsSummary(timings) {
    if (!timings?.length) return "";
    const batchWarn =
      state.pipelineTiming?.mode === "batch"
        ? `<p class="timing-hint timing-warn">当前为「批量加速」：帧时间戳按服务端流水线间隔推算，<strong>不能</strong>代表真实拍照间隔；请用「顺序处理全部」或端侧逐帧上传。</p>`
        : "";
    const rows = timings
      .map(
        (t) =>
          `<tr><td>#${escapeHtml(t.question_id)}</td><td>${escapeHtml(String(t.answer_order))}</td>` +
          `<td>${escapeHtml((t.first_answer_at || "").slice(11, 19))}</td>` +
          `<td>${escapeHtml(formatDurationMs(t.duration_ms))}</td>` +
          `<td>${t.duration_source === "next_answer" ? "至下一题首答" : "至本页末帧"}</td></tr>`,
      )
      .join("");
    return `<div class="timing-summary timing-answers">
      <div class="timing-head"><span class="timing-title">答题用时（帧间推算）</span></div>
      ${batchWarn}
      <p class="timing-hint">Q<sub>n</sub> 用时 ≈ 下一题<strong>首次出现</strong>答案的时间 − 本题首次出现答案的时间（按出现顺序，非题号）。</p>
      <table class="timing-table timing-table-frames">
        <thead><tr><th>题</th><th>答题序</th><th>首答时刻</th><th>估算用时</th><th>依据</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
  }

  function formatDurationMs(ms) {
    if (ms == null || Number.isNaN(ms)) return "—";
    if (ms < 1000) return `${ms}ms`;
    const sec = Math.round(ms / 1000);
    if (sec < 60) return `${sec}秒`;
    const min = Math.floor(sec / 60);
    const rem = sec % 60;
    return rem ? `${min}分${rem}秒` : `${min}分钟`;
  }

  function msToSec(ms) {
    if (ms == null || Number.isNaN(ms)) return "—";
    return `${(ms / 1000).toFixed(1)}s`;
  }

  function frameMetrics(frames) {
    return (frames ?? []).map((f) => {
      const ocr = f.ocr_ms ?? f.duration_ms ?? 0;
      const refine = f.refine_ms ?? 0;
      const total = f.total_ms ?? ocr + refine;
      return { frame: f, ocr, refine, total };
    });
  }

  /** Stagger batch wall ≈ OCR₁ + Σ max(refineᵢ₋₁, OCRᵢ) + refineₙ */
  function estimateStaggerWallMs(metrics) {
    if (!metrics.length) return 0;
    if (metrics.length === 1) return metrics[0].total;
    let wall = metrics[0].ocr;
    for (let i = 1; i < metrics.length; i += 1) {
      wall += Math.max(metrics[i - 1].refine, metrics[i].ocr);
    }
    wall += metrics[metrics.length - 1].refine;
    return wall;
  }

  function recordPipelineTiming({ mode, wallMs, serverBatchMs, frameCount, frameIds }) {
    state.pipelineTiming = {
      mode,
      wallMs,
      serverBatchMs: serverBatchMs ?? null,
      frameCount,
      frameIds: frameIds ?? [],
      recordedAt: Date.now(),
    };
  }

  function framesForTimingSummary(allFrames) {
    const pt = state.pipelineTiming;
    if (!pt?.frameIds?.length) return allFrames ?? [];
    const idSet = new Set(pt.frameIds);
    const filtered = (allFrames ?? []).filter((f) => idSet.has(f.id));
    return filtered.length ? filtered : allFrames ?? [];
  }

  function renderPipelineTimingBlock(allFrames) {
    const frames = framesForTimingSummary(allFrames);
    const metrics = frameMetrics(frames);
    if (!metrics.length) return "";

    const sumOcr = metrics.reduce((s, m) => s + m.ocr, 0);
    const sumRefine = metrics.reduce((s, m) => s + m.refine, 0);
    const sumSequential = metrics.reduce((s, m) => s + m.total, 0);
    const staggerEst = estimateStaggerWallMs(metrics);

    const pt = state.pipelineTiming;
    const timingApplies =
      pt &&
      pt.frameIds?.length === metrics.length &&
      metrics.every((m) => pt.frameIds.includes(m.frame.id));
    const wallMs = timingApplies
      ? pt.serverBatchMs ?? pt.wallMs
      : null;
    const clientWallMs =
      timingApplies && pt.wallMs && pt.serverBatchMs && Math.abs(pt.wallMs - pt.serverBatchMs) > 80
        ? pt.wallMs
        : null;
    const savedMs = wallMs != null ? Math.max(0, sumSequential - wallMs) : null;
    const savedPct =
      savedMs != null && sumSequential > 0
        ? Math.round((savedMs / sumSequential) * 100)
        : null;

    const modeLabel = {
      batch: "批量错叉（OCRᵢ ∥ refineᵢ₋₁）",
      sequential: "顺序逐张（无重叠）",
      single: "单张",
    }[timingApplies ? pt.mode : ""] ?? "—";

    const wallRow =
      wallMs != null
        ? `<tr><td>墙钟（实际等待）</td><td><strong>${msToSec(wallMs)}</strong>${clientWallMs != null ? ` <span class="timing-sub">客户端 ${msToSec(clientWallMs)}</span>` : ""}</td></tr>
           <tr><td>若完全顺序（各帧合计相加）</td><td>${msToSec(sumSequential)}</td></tr>
           <tr><td>重叠节省</td><td>${msToSec(savedMs)}${savedPct != null ? ` · ${savedPct}%` : ""}</td></tr>
           <tr><td>错叉公式估算</td><td>${msToSec(staggerEst)}${wallMs != null ? ` · Δ${msToSec(Math.abs(staggerEst - wallMs))}` : ""}</td></tr>`
        : `<tr><td>各帧合计（顺序等价墙钟）</td><td><strong>${msToSec(sumSequential)}</strong></td></tr>
           <tr><td>错叉公式估算（批量时）</td><td>${msToSec(staggerEst)}</td></tr>`;

    const frameRows = metrics
      .map((m, i) => {
        const overlap =
          i === 0
            ? "—"
            : `${msToSec(Math.max(metrics[i - 1].refine, m.ocr))} ∥`;
        return `<tr>
          <td>#${m.frame.seq}</td>
          <td>${msToSec(m.ocr)}</td>
          <td>${msToSec(m.refine)}</td>
          <td>${msToSec(m.total)}</td>
          <td>${overlap}</td>
        </tr>`;
      })
      .join("");

    const waterfall = metrics
      .map((m, i) => {
        const ocrW = Math.max(4, Math.round((m.ocr / sumSequential) * 100));
        const refineW = Math.max(4, Math.round((m.refine / sumSequential) * 100));
        const indent = i === 0 ? 0 : Math.min(40, Math.round((metrics[i - 1].refine / sumSequential) * 50));
        return `<div class="wf-row" style="--wf-indent:${indent}%">
          <span class="wf-label">#${m.frame.seq}</span>
          <span class="wf-track">
            <span class="wf-ocr" style="width:${ocrW}%" title="OCR ${msToSec(m.ocr)}"></span>
            <span class="wf-refine" style="width:${refineW}%" title="refine ${msToSec(m.refine)}"></span>
          </span>
        </div>`;
      })
      .join("");

    return `<div class="timing-summary">
      <div class="timing-head">
        <span class="timing-title">流水线耗时</span>
        <span class="timing-mode">${escapeHtml(modeLabel)} · ${metrics.length} 帧</span>
      </div>
      <table class="timing-table timing-table-compact">
        ${wallRow}
        <tr class="timing-subhead"><td colspan="2">累计 OCR ${msToSec(sumOcr)} · refine ${msToSec(sumRefine)}</td></tr>
      </table>
      <p class="timing-hint">错叉段 = max(上一帧 refine, 本帧 OCR) 并行；墙钟 &lt; 各帧「OCR+refine」简单相加。</p>
      <table class="timing-table timing-table-frames">
        <thead><tr><th>帧</th><th>OCR</th><th>refine</th><th>帧内合计</th><th>错叉段</th></tr></thead>
        <tbody>${frameRows}</tbody>
        <tfoot><tr><td>Σ</td><td>${msToSec(sumOcr)}</td><td>${msToSec(sumRefine)}</td><td>${msToSec(sumSequential)}</td><td></td></tr></tfoot>
      </table>
      <div class="timing-waterfall" aria-label="各帧 OCR/refine 比例（非严格墙钟甘特图）">
        <div class="wf-legend"><span class="wf-ocr-swatch"></span>OCR <span class="wf-refine-swatch"></span>refine</div>
        ${waterfall}
      </div>
    </div>`;
  }

  function revokeObjectUrl() {
    if (state.objectUrl) {
      URL.revokeObjectURL(state.objectUrl);
      state.objectUrl = null;
    }
  }

  function revokeQueueUrls() {
    for (const item of state.batchQueue) {
      if (item.objectUrl) {
        URL.revokeObjectURL(item.objectUrl);
        item.objectUrl = null;
      }
    }
  }

  function revokeFrameCache() {
    state.framePreviewCache.forEach((url) => URL.revokeObjectURL(url));
    state.framePreviewCache.clear();
  }

  function reviveQueueObjectUrl(item) {
    if (!item?.normalizedBlob) return null;
    if (item.objectUrl) {
      URL.revokeObjectURL(item.objectUrl);
    }
    item.objectUrl = URL.createObjectURL(item.normalizedBlob);
    return item.objectUrl;
  }

  const PIPELINE_EVENT_LABELS = {
    FRAME_ADDED: "已入队",
    OCR_START: "OCR 识图中",
    OCR_SKIPPED: "图像无变化，跳过 OCR",
    OCR_REFINED: "refine 校正题干",
    OCR_DONE: "识别完成，合并中",
    MERGED: "合并完成",
    SKIPPED: "跳过合并",
  };

  function inProgressFrameId(session) {
    const doneIds = new Set((session?.frames ?? []).map((f) => f.id));
    let fid = null;
    for (const e of session?.events ?? []) {
      const id = e.payload?.frame_id;
      if (!id || doneIds.has(id)) continue;
      if (["FRAME_ADDED", "OCR_START", "OCR_SKIPPED", "OCR_REFINED", "OCR_DONE"].includes(e.type)) {
        fid = id;
      }
    }
    return fid;
  }

  function pipelineStatusLabel(session) {
    const fid = inProgressFrameId(session);
    if (!fid) return "识别中…";
    let label = "识别中…";
    for (const e of session?.events ?? []) {
      if (e.payload?.frame_id !== fid) continue;
      if (PIPELINE_EVENT_LABELS[e.type]) label = PIPELINE_EVENT_LABELS[e.type];
    }
    return label;
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function pollPipelineStatus(sessionId, signal, onStatus) {
    while (!signal?.aborted) {
      await sleep(450);
      if (signal?.aborted) break;
      try {
        const res = await fetch(`${API}/sessions/${sessionId}`, {
          headers: apiHeaders(),
          signal,
        });
        if (!res.ok) break;
        const data = await res.json();
        onStatus(pipelineStatusLabel(data.session));
        if (!inProgressFrameId(data.session)) break;
      } catch {
        break;
      }
    }
  }

  function updateProcessingStatus(item, status, sequenceLabel = "") {
    const prefix = sequenceLabel ? `${sequenceLabel} · ` : "";
    const text = `${prefix}${item.label} · ${status}`;
    setProcessingBanner(text);
    els.frameMeta.textContent = text;
    showPreviewBusy(status);
  }

  function defaultLabel(index) {
    return `第${index + 1}张`;
  }

  function renumberQueueLabels() {
    state.batchQueue.forEach((item, i) => {
      if (!item.customLabel) item.label = defaultLabel(i);
    });
  }

  function visibleQueueItems() {
    if (!state.hideDoneQueue) return state.batchQueue;
    return state.batchQueue.filter((q) => q.status !== "done");
  }

  function overlayPartsForPreview() {
    if (!state.showOverlay || state.sideTab !== "frame" || !state.selectedFrameId) return [];
    const frame = state.session?.frames?.find((f) => f.id === state.selectedFrameId);
    return overlayFromParts(frame?.ocr_parts ?? []);
  }

  function refreshOverlay() {
    if (!state.showOverlay || state.sideTab !== "frame") {
      clearOverlay(els.overlay);
      return;
    }
    drawOverlay(els.previewImg, els.overlay, overlayPartsForPreview());
  }

  function showToast(message, kind = "info") {
    if (!els.toastHost) return;
    const node = document.createElement("div");
    node.className = `toast toast-${kind}`;
    node.textContent = message;
    els.toastHost.appendChild(node);
    setTimeout(() => node.classList.add("show"), 10);
    setTimeout(() => {
      node.classList.remove("show");
      setTimeout(() => node.remove(), 300);
    }, 4500);
  }

  function setWorkflowStep(step) {
    state.workflowStep = step;
    els.workflowSteps?.querySelectorAll(".step").forEach((el) => {
      const n = Number(el.dataset.step);
      el.classList.toggle("active", n === step);
      el.classList.toggle("done", n < step);
    });
  }

  function resetProcessingUI() {
    state.processing = false;
    setProcessingBanner(null);
    for (const item of state.batchQueue) {
      if (item.status === "processing") item.status = "done";
    }
    updateBatchActions();
  }

  function updateBatchProgress() {
    if (!els.batchProgress) return;
    const active = state.batchTotal > 0 && state.processing;
    els.batchProgress.hidden = !active;
    if (!active) return;
    const pct = state.batchTotal ? Math.round((state.batchDone / state.batchTotal) * 100) : 0;
    els.batchProgressLabel.textContent = `批量处理 ${state.batchDone}/${state.batchTotal} · ${pct}%`;
    els.batchProgressFill.style.width = `${pct}%`;
  }

  function setProcessingBanner(text) {
    if (!els.processingBanner) return;
    if (text) {
      els.processingBanner.hidden = false;
      els.processingBannerText.textContent = text;
    } else {
      els.processingBanner.hidden = true;
    }
  }

  function updateBatchActions() {
    const ready = state.batchQueue.filter((q) => q.status === "ready");
    const active = state.session?.status === "active";
    els.btnProcessOne.disabled = state.processing || !active || ready.length === 0;
    els.btnProcessAll.disabled = state.processing || !active || ready.length === 0;
    if (els.btnProcessBatch) {
      els.btnProcessBatch.disabled = state.processing || !active || ready.length < 2;
    }
    els.btnClearQueue.disabled =
      state.processing || !state.batchQueue.some((q) => q.status !== "processing");
    if (els.btnCancelBatch) els.btnCancelBatch.hidden = !state.processing;
  }

  function syncDockButtons() {
    const active = state.session?.status === "active";
    const hasFrames = (state.session?.frames?.length ?? 0) > 0;
    els.btnFinalizeDock.disabled = !active || !hasFrames;
    els.btnEndSessionDock.disabled = !state.sessionId;
    if (els.dockHint) {
      if (!hasFrames) {
        els.dockHint.textContent = "请先批量选图并加帧";
      } else if (active) {
        els.dockHint.textContent = "第 4 步：定稿当前页写入作业库，或结束会话并更新 A⁺ demo";
      } else {
        els.dockHint.textContent = "会话已结束，可在「作业库 / A⁺ demo」查看结果";
      }
    }
  }

  function setSessionBar() {
    const active = state.session?.status === "active";
    els.sessionIdLabel.textContent = state.sessionId
      ? `${state.sessionId.slice(0, 8)}… · ${active ? "进行中" : "已结束"}`
      : "未创建会话";
    els.btnExport.disabled = !state.sessionId;
    updateBatchActions();
    syncDockButtons();
    if (hasFrames()) setWorkflowStep(Math.max(state.workflowStep, 3));
  }

  function hasFrames() {
    return (state.session?.frames?.length ?? 0) > 0;
  }

  function framePreviewUrl(frameId) {
    return `${API}/sessions/${state.sessionId}/frames/${frameId}/preview`;
  }

  function hidePreviewBusy() {
    els.previewStage?.querySelector(".preview-busy")?.remove();
  }

  function showPreviewBusy(message) {
    showPreviewStage();
    hidePreviewBusy();
    const busy = document.createElement("div");
    busy.className = "preview-busy";
    busy.innerHTML = `<div class="loading"><span class="spinner"></span>${escapeHtml(message)}</div>`;
    els.previewStage?.appendChild(busy);
  }

  function showPreviewLoading(message) {
    hidePreviewBusy();
    const placeholder = els.canvasWrap.querySelector(".placeholder");
    if (placeholder) {
      placeholder.hidden = false;
      placeholder.innerHTML = `<div class="loading"><span class="spinner"></span>${escapeHtml(message)}</div>`;
    }
    if (els.previewStage) els.previewStage.hidden = true;
    delete els.previewImg.dataset.previewUrl;
    els.previewImg.removeAttribute("src");
    clearOverlay(els.overlay);
  }

  function showPreviewStage() {
    const placeholder = els.canvasWrap.querySelector(".placeholder");
    if (placeholder) placeholder.hidden = true;
    if (els.previewStage) els.previewStage.hidden = false;
  }

  function setPreviewFromUrl(url, onReady, { onRevivedUrl } = {}) {
    if (!url) return;
    const img = els.previewImg;
    const token = ++state.previewToken;

    const finish = () => {
      if (token !== state.previewToken) return;
      hidePreviewBusy();
      showPreviewStage();
      if (onReady) onReady();
      else refreshOverlay();
    };

    const tryLoad = (srcUrl) => {
      if (img.dataset.previewUrl === srcUrl && img.complete && img.naturalWidth > 0) {
        finish();
        return;
      }

      if (img.dataset.previewUrl === srcUrl && !img.complete) {
        showPreviewStage();
        img.onload = () => finish();
        img.onerror = () => {
          if (token !== state.previewToken) return;
          const revived = onRevivedUrl?.();
          if (revived && revived !== srcUrl) {
            tryLoad(revived);
            return;
          }
          if (state.processing) return;
          delete img.dataset.previewUrl;
          showPreviewLoading("预览加载失败");
        };
        return;
      }

      showPreviewStage();
      img.dataset.previewUrl = srcUrl;
      img.onload = () => finish();
      img.onerror = () => {
        if (token !== state.previewToken) return;
        const revived = onRevivedUrl?.();
        if (revived && revived !== srcUrl) {
          tryLoad(revived);
          return;
        }
        if (state.processing) return;
        delete img.dataset.previewUrl;
        showPreviewLoading("预览加载失败");
      };
      img.src = srcUrl;
    };

    tryLoad(url);
  }

  async function loadFramePreview(frameId) {
    if (state.processing) return;
    const cached = state.framePreviewCache.get(frameId);
    if (cached) {
      setPreviewFromUrl(cached, () => refreshOverlay());
      return;
    }
    const token = ++state.previewToken;
    try {
      const res = await fetch(framePreviewUrl(frameId), { headers: apiHeaders() });
      if (token !== state.previewToken) return;
      if (!res.ok) throw new Error(String(res.status));
      const blob = await res.blob();
      if (token !== state.previewToken) return;
      const blobUrl = URL.createObjectURL(blob);
      state.framePreviewCache.set(frameId, blobUrl);
      setPreviewFromUrl(blobUrl, () => refreshOverlay());
    } catch {
      if (token !== state.previewToken) return;
      const fallback = state.batchQueue.find((q) => q.frameId === frameId && q.objectUrl);
      if (fallback?.objectUrl) {
        setPreviewFromUrl(fallback.objectUrl, () => clearOverlay(els.overlay));
        return;
      }
      if (state.processing && state.processingPreviewUrl) {
        setPreviewFromUrl(state.processingPreviewUrl, () => clearOverlay(els.overlay));
        return;
      }
      if (!state.processing) showPreviewLoading("预览加载失败");
    }
  }

  function renderBatchQueue() {
    const visible = visibleQueueItems();
    const doneCount = state.batchQueue.filter((q) => q.status === "done").length;

    if (!state.batchQueue.length) {
      els.batchQueue.innerHTML = '<li class="batch-empty">队列为空 — 拖入或选择多张图片</li>';
      updateBatchActions();
      return;
    }

    if (!visible.length && state.hideDoneQueue && doneCount > 0) {
      els.batchQueue.innerHTML = `<li class="batch-empty">待处理队列为空 · 已完成 ${doneCount} 张（取消勾选「隐藏已完成」可查看）</li>`;
      updateBatchActions();
      return;
    }

    els.batchQueue.innerHTML = visible
      .map((item, idx) => {
        const active = item.key === state.selectedQueueKey ? " active" : "";
        const statusClass = `status-${item.status}`;
        const statusText =
          {
            ready: "就绪",
            pending: "转码中…",
            processing: "处理中…",
            done: "已完成",
            error: "失败",
          }[item.status] ?? "";
        const seq = state.batchQueue.indexOf(item) + 1;
        const thumb = item.objectUrl
          ? `<img class="batch-thumb" src="${item.objectUrl}" alt="" loading="lazy" decoding="async" />`
          : `<span class="batch-thumb placeholder-thumb"></span>`;
        return `<li class="batch-item${active} ${statusClass}" data-key="${escapeHtml(item.key)}">
          <span class="batch-seq">${seq}</span>
          ${thumb}
          <div class="batch-body">
            <input type="text" class="batch-label" data-key="${escapeHtml(item.key)}" value="${escapeHtml(item.label)}" ${item.status === "done" ? "disabled" : ""} />
            <span class="batch-file">${escapeHtml(item.fileName)}</span>
            <span class="batch-status">${escapeHtml(statusText)}</span>
          </div>
          <button type="button" class="batch-remove" data-key="${escapeHtml(item.key)}" title="移出队列" ${item.status === "processing" ? "disabled" : ""}>×</button>
        </li>`;
      })
      .join("");

    els.batchQueue.querySelectorAll(".batch-item").forEach((row) => {
      row.addEventListener("click", (e) => {
        if (e.target.closest(".batch-label") || e.target.closest(".batch-remove")) return;
        selectQueueItem(row.dataset.key);
      });
    });
    els.batchQueue.querySelectorAll(".batch-label").forEach((input) => {
      input.addEventListener("change", () => {
        const item = state.batchQueue.find((q) => q.key === input.dataset.key);
        if (item) {
          item.label = input.value.trim() || item.label;
          item.customLabel = true;
        }
      });
      input.addEventListener("click", (e) => e.stopPropagation());
    });
    els.batchQueue.querySelectorAll(".batch-remove").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.stopPropagation();
        removeQueueItem(btn.dataset.key);
      });
    });
    updateBatchActions();
  }

  function selectQueueItem(key) {
    const item = state.batchQueue.find((q) => q.key === key);
    if (!item?.objectUrl && item?.normalizedBlob) {
      reviveQueueObjectUrl(item);
    }
    if (!item?.objectUrl) return;
    state.selectedQueueKey = key;
    state.viewingPendingUpload = true;
    state.selectedFrameId = null;
    renderBatchQueue();
    setPreviewFromUrl(item.objectUrl, () => clearOverlay(els.overlay), {
      onRevivedUrl: () => reviveQueueObjectUrl(item),
    });
    els.frameMeta.hidden = false;
    const statusLabel =
      item.status === "processing"
        ? "处理中"
        : item.status === "done"
          ? "已完成"
          : "待加帧";
    els.frameMeta.textContent = `${item.label} · ${statusLabel} · ${item.fileName}`;
  }

  function removeQueueItem(key) {
    const idx = state.batchQueue.findIndex((q) => q.key === key);
    if (idx < 0) return;
    const [removed] = state.batchQueue.splice(idx, 1);
    if (removed.objectUrl) {
      URL.revokeObjectURL(removed.objectUrl);
      removed.objectUrl = null;
    }
    if (state.selectedQueueKey === key) {
      state.selectedQueueKey = null;
      showPreviewLoading("从队列选图或点击 Timeline 帧");
      els.frameMeta.hidden = true;
    }
    renumberQueueLabels();
    renderBatchQueue();
  }

  function clearQueue() {
    const keep = state.batchQueue.filter((q) => q.status === "processing");
    for (const item of state.batchQueue) {
      if (item.status !== "processing" && item.objectUrl) {
        URL.revokeObjectURL(item.objectUrl);
      }
    }
    state.batchQueue = keep;
    if (!keep.some((q) => q.key === state.selectedQueueKey)) {
      state.selectedQueueKey = null;
      showPreviewLoading("从队列选图或点击 Timeline 帧");
      els.frameMeta.hidden = true;
    }
    renumberQueueLabels();
    renderBatchQueue();
  }

  async function enqueueFiles(fileList) {
    const files = Array.from(fileList).filter(isImageFile);
    if (!files.length) {
      showToast("未识别到有效图片", "err");
      return;
    }
    setWorkflowStep(1);

    const startIdx = state.batchQueue.length;
    for (let i = 0; i < files.length; i++) {
      state.batchQueue.push({
        key: `q-${Date.now()}-${i}-${Math.random().toString(36).slice(2, 7)}`,
        file: files[i],
        fileName: files[i].name,
        label: defaultLabel(startIdx + i),
        customLabel: false,
        status: "pending",
        normalizedBlob: null,
        objectUrl: null,
        meta: null,
      });
    }
    renderBatchQueue();

    for (const item of state.batchQueue.filter((q) => q.status === "pending")) {
      try {
        const { blob, url, sourceFormat, width, height } = await normalizeImageFile(item.file);
        item.normalizedBlob = blob;
        item.objectUrl = url;
        item.status = "ready";
        item.meta = { sourceFormat, width, height };
      } catch (err) {
        item.status = "error";
        item.error = err.message ?? String(err);
      }
      renderBatchQueue();
    }

    const firstReady = state.batchQueue.find((q) => q.status === "ready");
    if (firstReady && !state.selectedQueueKey) selectQueueItem(firstReady.key);
    setWorkflowStep(2);
  }

  async function processQueueItem(item, signal, opts = {}) {
    const { partOfSequence = false, sequenceLabel = "" } = opts;
    if (!item || item.status !== "ready" || !state.sessionId || !item.normalizedBlob) return false;
    if (state.session?.status !== "active") return false;

    if (state.selectedQueueKey !== item.key) {
      selectQueueItem(item.key);
    }
    state.selectedQueueKey = item.key;

    state.processing = true;
    state.processingPreviewUrl = item.objectUrl;
    item.status = "processing";
    renderBatchQueue();
    state.viewingPendingUpload = true;

    if (item.objectUrl) {
      setPreviewFromUrl(item.objectUrl, () => clearOverlay(els.overlay), {
        onRevivedUrl: () => reviveQueueObjectUrl(item),
      });
    }
    updateProcessingStatus(item, "识别中…", sequenceLabel);

    let frameId = null;
    let sessionAfter = null;
    let ok = false;
    const wallT0 = performance.now();
    const pollAbort = new AbortController();
    if (signal) {
      signal.addEventListener("abort", () => pollAbort.abort(), { once: true });
    }
    const pollTask = pollPipelineStatus(state.sessionId, pollAbort.signal, (status) => {
      updateProcessingStatus(item, status, sequenceLabel);
    });

    try {
      const form = new FormData();
      form.append("file", item.normalizedBlob, "frame.jpg");
      const res = await fetch(`${API}/sessions/${state.sessionId}/frames`, {
        method: "POST",
        headers: apiHeaders({ "X-Already-Normalized": "1" }),
        body: form,
        signal,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? JSON.stringify(data));

      item.status = "done";
      frameId = data.frame_id;
      item.frameId = frameId;
      sessionAfter = data.session;
      state.selectedFrameId = frameId;
      applySession(data.session, { skipPreview: true });

      if (data.homework_finalized) {
        showToast("检测到新页：上一页已自动定稿并写入作业库", "info");
        await loadHomework();
      }
      setWorkflowStep(3);
      ok = true;
    } catch (err) {
      if (err.name === "AbortError") {
        item.status = "ready";
        showToast("已停止处理", "warn");
      } else {
        item.status = "error";
        item.error = err.message ?? String(err);
        showToast(item.error, "err");
      }
    } finally {
      pollAbort.abort();
      await pollTask.catch(() => {});
      if (!partOfSequence) {
        state.processing = false;
        state.processingPreviewUrl = null;
        hidePreviewBusy();
        setProcessingBanner(null);
        renderBatchQueue();
        setSessionBar();
        updateBatchProgress();
      } else {
        hidePreviewBusy();
        renderBatchQueue();
      }
    }

    if (ok && frameId && sessionAfter) {
      if (!partOfSequence) {
        recordPipelineTiming({
          mode: "single",
          wallMs: Math.round(performance.now() - wallT0),
          frameCount: 1,
          frameIds: [frameId],
        });
      }
      state.viewingPendingUpload = false;
      els.frameMeta.textContent = `${item.label} · 已加帧 #${sessionAfter.frames.length}`;
      await loadFramePreview(frameId);
      renderTimeline();
      if (state.sideTab === "merged" || state.sideTab === "frame") {
        renderActivePanel({ skipOverlay: true });
      }
    }
    return ok;
  }

  async function processOne() {
    let item = null;
    if (state.selectedQueueKey) {
      item = state.batchQueue.find(
        (q) => q.key === state.selectedQueueKey && q.status === "ready",
      );
    }
    if (!item) item = state.batchQueue.find((q) => q.status === "ready");
    if (!item) return;
    state.batchTotal = 1;
    state.batchDone = 0;
    updateBatchProgress();
    await processQueueItem(item, undefined);
    state.batchTotal = 0;
    updateBatchProgress();
  }

  async function processAll() {
    const ready = state.batchQueue.filter((q) => q.status === "ready");
    if (!ready.length || !state.sessionId) return;
    if (state.session?.status !== "active") return;

    state.batchTotal = ready.length;
    state.batchDone = 0;
    state.batchAbort = new AbortController();
    const signal = state.batchAbort.signal;
    updateBatchProgress();
    setWorkflowStep(2);
    state.processing = true;

    let completed = 0;
    const wallT0 = performance.now();
    const frameIds = [];
    try {
      for (let i = 0; i < ready.length; i++) {
        if (signal.aborted) break;
        const item = ready[i];
        const seqLabel = `顺序 ${i + 1}/${ready.length}`;
        setProcessingBanner(`${seqLabel} · 准备处理 ${item.label}…`);
        const ok = await processQueueItem(item, signal, {
          partOfSequence: i < ready.length - 1,
          sequenceLabel: seqLabel,
        });
        if (!ok && signal.aborted) break;
        if (ok) {
          completed += 1;
          if (item.frameId) frameIds.push(item.frameId);
          state.batchDone = completed;
          updateBatchProgress();
        } else if (!signal.aborted) {
          break;
        }
      }

      if (completed > 0 && !signal.aborted) {
        recordPipelineTiming({
          mode: "sequential",
          wallMs: Math.round(performance.now() - wallT0),
          frameCount: completed,
          frameIds,
        });
        renderTimeline();
        showToast(`顺序完成 ${completed}/${ready.length} 张 · 请查看「合并」`, "ok");
        setWorkflowStep(4);
        setSideTab("merged");
        await refreshSideList();
      }
    } finally {
      state.processing = false;
      state.processingPreviewUrl = null;
      state.batchAbort = null;
      hidePreviewBusy();
      setProcessingBanner(null);
      state.batchTotal = 0;
      updateBatchProgress();
      renderBatchQueue();
      setSessionBar();
    }
  }

  async function processBatchAccelerated() {
    const ready = state.batchQueue.filter((q) => q.status === "ready");
    if (!ready.length || !state.sessionId) return;
    if (state.session?.status !== "active") return;

    state.batchTotal = ready.length;
    state.batchDone = 0;
    state.batchAbort = new AbortController();
    const signal = state.batchAbort.signal;
    updateBatchProgress();
    setWorkflowStep(2);
    state.processing = true;

    if (ready[0]) selectQueueItem(ready[0].key);

    const pollAbort = new AbortController();
    signal.addEventListener("abort", () => pollAbort.abort(), { once: true });

    const syncQueueFromSession = (session) => {
      const frames = session?.frames ?? [];
      const inProgressIdx = frames.length;
      ready.forEach((item, i) => {
        if (i < frames.length) {
          item.status = "done";
          item.frameId = frames[i]?.id ?? item.frameId;
        } else if (i === inProgressIdx && inProgressFrameId(session)) {
          item.status = "processing";
        } else if (item.status !== "error") {
          item.status = "ready";
        }
      });
      state.batchDone = Math.min(frames.length, ready.length);
      updateBatchProgress();
      renderBatchQueue();
      const activeItem = ready[inProgressIdx];
      if (activeItem?.status === "processing") {
        updateProcessingStatus(activeItem, pipelineStatusLabel(session), `批量 ${state.batchDone + 1}/${ready.length}`);
      }
    };

    const pollTask = (async () => {
      while (!pollAbort.signal.aborted) {
        await sleep(450);
        if (pollAbort.signal.aborted) break;
        try {
          const res = await fetch(`${API}/sessions/${state.sessionId}`, {
            headers: apiHeaders(),
            signal: pollAbort.signal,
          });
          if (!res.ok) break;
          const data = await res.json();
          syncQueueFromSession(data.session);
        } catch {
          break;
        }
      }
    })();

    const wallT0 = performance.now();
    try {
      const form = new FormData();
      ready.forEach((item, i) => {
        form.append("files", item.normalizedBlob, `frame${i + 1}.jpg`);
      });
      setProcessingBanner(`批量流水线启动…（${ready.length} 张，OCR 与 refine 错叉并行）`);
      showPreviewBusy("批量流水线处理中…");

      const res = await fetch(`${API}/sessions/${state.sessionId}/frames/batch`, {
        method: "POST",
        headers: apiHeaders({ "X-Already-Normalized": "1" }),
        body: form,
        signal,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? JSON.stringify(data));

      data.frames.forEach((fr, i) => {
        const item = ready[i];
        if (!item) return;
        item.status = "done";
        item.frameId = fr.frame_id;
      });
      if (data.homework_finalized?.length) {
        showToast("检测到新页：上一页已自动定稿并写入作业库", "info");
        await loadHomework();
      }
      const lastItem = ready[ready.length - 1];
      if (lastItem?.frameId) {
        state.selectedFrameId = lastItem.frameId;
        state.selectedQueueKey = lastItem.key;
      }
      applySession(data.session, { skipPreview: true });
      state.batchDone = data.frames.length;
      updateBatchProgress();
      setWorkflowStep(3);

      recordPipelineTiming({
        mode: "batch",
        wallMs: Math.round(performance.now() - wallT0),
        serverBatchMs: data.batch_total_ms,
        frameCount: data.frames.length,
        frameIds: data.frames.map((fr) => fr.frame_id),
      });

      if (lastItem?.frameId) {
        state.viewingPendingUpload = false;
        await loadFramePreview(lastItem.frameId);
      }
      renderTimeline();
      if (state.sideTab === "merged" || state.sideTab === "frame") {
        renderActivePanel({ skipOverlay: true });
      }

      const metrics = frameMetrics(data.session?.frames ?? []);
      const sumSeq = metrics.reduce((s, m) => s + m.total, 0);
      const saved = Math.max(0, sumSeq - data.batch_total_ms);
      showToast(
        `批量完成 ${data.frames.length} 张 · 墙钟 ${msToSec(data.batch_total_ms)} · 节省 ${msToSec(saved)}`,
        "ok",
      );
      setWorkflowStep(4);
      setSideTab("merged");
      await refreshSideList();
    } catch (err) {
      if (err.name === "AbortError") {
        for (const item of ready) {
          if (item.status === "processing") item.status = "ready";
        }
        showToast("已停止批量处理", "warn");
      } else {
        for (const item of ready) {
          if (item.status === "processing") {
            item.status = "error";
            item.error = err.message ?? String(err);
          }
        }
        showToast(err.message ?? String(err), "err");
      }
    } finally {
      pollAbort.abort();
      await pollTask.catch(() => {});
      state.processing = false;
      state.processingPreviewUrl = null;
      state.batchAbort = null;
      hidePreviewBusy();
      setProcessingBanner(null);
      state.batchTotal = 0;
      updateBatchProgress();
      renderBatchQueue();
      setSessionBar();
    }
  }

  function cancelBatch() {
    state.batchAbort?.abort();
  }

  function renderTimeline() {
    const frames = state.session?.frames ?? [];
    if (!frames.length) {
      els.timeline.innerHTML = '<p class="timeline-empty">加帧后显示</p>';
      return;
    }
    const summary = renderPipelineTimingBlock(frames);
    const items = frames
      .map((f) => {
        const action = ACTION_LABELS_SHORT[f.scheduler_action] ?? f.scheduler_action;
        const active = f.id === state.selectedFrameId ? " active" : "";
        return `<button type="button" class="timeline-item${active}" data-frame-id="${escapeHtml(f.id)}">
          <span class="tl-seq">#${f.seq}</span>
          <span class="tl-action badge-${escapeHtml(f.scheduler_action)}">${escapeHtml(action)}</span>
          <span class="tl-meta tl-meta-debug">${escapeHtml(formatTimelineDebug(f))}</span>
        </button>`;
      })
      .join("");
    els.timeline.innerHTML = `${summary}${renderQuestionTimingsSummary(state.session?.question_timings)}<div class="timeline-frames">${items}</div>`;
    els.timeline.querySelectorAll(".timeline-item").forEach((btn) => {
      btn.addEventListener("click", () => selectFrame(btn.dataset.frameId));
    });
  }

  function formatTimelineDebug(f) {
    const parts = [];
    const ocr = f.ocr_ms ?? f.duration_ms;
    const refine = f.refine_ms ?? 0;
    const total = f.total_ms ?? (ocr && refine ? ocr + refine : ocr);
    if (ocr) parts.push(`OCR ${(ocr / 1000).toFixed(1)}s`);
    if (refine) parts.push(`refine ${(refine / 1000).toFixed(1)}s`);
    if (total) parts.push(`帧内 ${(total / 1000).toFixed(1)}s`);
    if (f.same_page_score != null) parts.push(`同页 ${(f.same_page_score * 100).toFixed(0)}%`);
    if (f.skip_reason) parts.push(f.skip_reason);
    return parts.join(" · ") || "—";
  }

  function formatEventPayload(e) {
    const p = e.payload ?? {};
    const bits = [];
    if (p.frame_id) bits.push(`frame=${String(p.frame_id).slice(0, 8)}`);
    if (p.ocr_ms != null) bits.push(`ocr=${msToSec(p.ocr_ms)}`);
    if (p.refine_ms != null) bits.push(`refine=${msToSec(p.refine_ms)}`);
    if (p.total_ms != null) bits.push(`total=${msToSec(p.total_ms)}`);
    if (p.duration_ms != null && p.ocr_ms == null) bits.push(`dur=${msToSec(p.duration_ms)}`);
    if (p.changes != null) bits.push(`Δprint=${p.changes}`);
    if (p.action) bits.push(String(p.action));
    if (p.reason) bits.push(String(p.reason));
    return bits.length ? bits.join(" · ") : "";
  }

  function renderEvents() {
    const events = state.session?.events ?? [];
    if (!events.length) {
      els.eventLog.innerHTML = '<p class="timeline-empty">暂无事件</p>';
      return;
    }
    els.eventLog.innerHTML = events
      .slice(-24)
      .map((e) => {
        const detail = formatEventPayload(e);
        return `<div class="event-row">
          <span class="event-ts">${escapeHtml(e.ts?.slice(11, 19) ?? "")}</span>
          <span class="event-type">${escapeHtml(e.type)}</span>
          ${detail ? `<span class="event-detail">${escapeHtml(detail)}</span>` : ""}
        </div>`;
      })
      .join("");
  }

  function selectFrame(frameId) {
    state.viewingPendingUpload = false;
    state.selectedFrameId = frameId;
    state.selectedQueueKey = null;
    renderBatchQueue();
    const frame = state.session?.frames?.find((f) => f.id === frameId);
    if (!frame) return;
    renderTimeline();
    if (state.sessionId) void loadFramePreview(frameId);
    if (state.sideTab === "frame" || state.sideTab === "merged" || state.sideTab === "compare") {
      renderActivePanel({ skipOverlay: true });
    }
    els.frameMeta.hidden = false;
    const dbg = formatTimelineDebug(frame);
    els.frameMeta.textContent = `#${frame.seq} · ${ACTION_LABELS[frame.scheduler_action] ?? frame.scheduler_action} · ${dbg}`;
  }

  let mathEnrichSeq = 0;

  function partsNeedMathRich(parts) {
    return (parts ?? []).some((p) => {
      const pt = p.print ?? p.print_text ?? "";
      const hw = p.handwriting ?? "";
      const hasPrintRich = Array.isArray(p.print_rich?.segments) && p.print_rich.segments.length;
      const hasHwRich =
        Array.isArray(p.handwriting_rich?.segments) && p.handwriting_rich.segments.length;
      return (pt && !hasPrintRich) || (hw && !hasHwRich);
    });
  }

  async function enrichPartsRich(parts) {
    if (!parts?.length || !partsNeedMathRich(parts)) return parts;
    const res = await fetch(`${API}/math/normalize-parts`, {
      method: "POST",
      headers: { ...apiHeaders(), "Content-Type": "application/json" },
      body: JSON.stringify({
        parts: parts.map((p) => ({
          kind: p.kind,
          id: p.id,
          print: p.print ?? p.print_text ?? "",
          handwriting: p.handwriting ?? "",
        })),
      }),
    });
    if (!res.ok) return parts;
    const data = await res.json();
    return parts.map((p, i) => ({ ...p, ...(data.parts?.[i] ?? {}) }));
  }

  async function renderOcrCompareView() {
    const frame = state.session?.frames?.find((f) => f.id === state.selectedFrameId);
    if (!frame) {
      els.mergedTitle.textContent = "OCR 对比";
      els.questionCount.hidden = true;
      els.resultBody.innerHTML =
        '<p class="placeholder">请在时间轴选择一帧，或加帧后查看 Vision vs refine</p>';
      return;
    }
    const seq = ++mathEnrichSeq;
    let vision = frame.vision_parts ?? [];
    let refined = frame.ocr_parts ?? [];
    if (partsNeedMathRich(vision) || partsNeedMathRich(refined)) {
      [vision, refined] = await Promise.all([
        enrichPartsRich(vision),
        enrichPartsRich(refined),
      ]);
      if (seq !== mathEnrichSeq) return;
      frame.vision_parts = vision;
      frame.ocr_parts = refined;
    }
    const changes = countOcrPrintChanges(vision, refined);
    els.mergedTitle.textContent = `OCR 对比 · 帧 #${frame.seq}`;
    els.questionCount.hidden = false;
    els.questionCount.textContent =
      changes > 0 ? `印刷体 ${changes} 处差异` : "印刷体无差异";
    els.resultBody.innerHTML = renderOcrComparePanel(vision, refined, {
      ocr_ms: frame.ocr_ms,
      refine_ms: frame.refine_ms,
      n_refine_changes: frame.n_refine_changes,
    });
  }

  function renderActivePanel(opts = {}) {
    if (state.sideTab === "compare") {
      void renderOcrCompareView();
      return;
    }
    void renderMergedOrFrame(opts);
  }

  async function renderMergedOrFrame(opts = {}) {
    const { skipOverlay = false } = opts;
    let parts = state.session?.merged_parts ?? [];
    let title = "当前页合并结果";
    if (state.sideTab === "frame" && state.selectedFrameId) {
      const frame = state.session.frames.find((f) => f.id === state.selectedFrameId);
      parts = frame?.ocr_parts ?? [];
      title = `本帧 OCR · #${frame?.seq ?? "?"}`;
    }
    els.mergedTitle.textContent = title;
    const nQ = parts.filter((p) => p.kind === "question").length;
    els.questionCount.hidden = false;
    els.questionCount.textContent = nQ > 0 ? `${nQ} 题` : `${parts.length} 段`;
    const seq = ++mathEnrichSeq;
    if (partsNeedMathRich(parts)) {
      parts = await enrichPartsRich(parts);
      if (seq !== mathEnrichSeq) return;
      if (state.sideTab === "frame" && state.selectedFrameId) {
        const frame = state.session.frames.find((f) => f.id === state.selectedFrameId);
        if (frame) frame.ocr_parts = parts;
      } else {
        state.session.merged_parts = parts;
      }
    }
    els.resultBody.innerHTML = renderPage(parts, state.session?.question_timings);
    wireQuestionCardActions(els.resultBody);
    if (!skipOverlay) refreshOverlay();
  }

  async function loadHomework() {
    const res = await fetch(`${API}/homework`, { headers: apiHeaders() });
    if (!res.ok) {
      els.sideList.innerHTML = `<p class="placeholder error">作业库加载失败（${res.status}）<br><small>Buddy API 异常，请稍后重试或查看 gateway 日志</small></p>`;
      return;
    }
    const data = await res.json();
    const entries = data.entries ?? [];
    if (!entries.length) {
      els.sideList.innerHTML =
        '<p class="placeholder">暂无定稿作业<br><small>请先加帧，再点底部「定稿当前页」或「结束会话」</small></p>';
      return;
    }
    els.sideList.innerHTML = entries
      .map(
        (e) =>
          `<article class="list-card hw-list-card" data-hw-id="${escapeHtml(e.id)}">
            <header>${escapeHtml(e.meta?.title || e.finalized_at?.slice(0, 19) || e.id.slice(0, 8))}</header>
            <p>${e.parts?.filter((p) => p.kind === "question").length ?? 0} 题 · ${e.source_frame_ids?.length ?? 0} 帧</p>
            <footer class="hw-list-actions">
              <button type="button" class="btn ghost sm btn-hw-open" data-hw-id="${escapeHtml(e.id)}">打开</button>
              <button type="button" class="btn ghost sm btn-hw-edit" data-hw-id="${escapeHtml(e.id)}">编辑</button>
              <button type="button" class="btn ghost sm btn-hw-del" data-hw-id="${escapeHtml(e.id)}">删除</button>
            </footer>
          </article>`,
      )
      .join("");
    els.sideList.querySelectorAll(".btn-hw-open, .list-card.hw-list-card header, .list-card.hw-list-card p").forEach((el) => {
      el.addEventListener("click", (ev) => {
        if (ev.target.closest(".hw-list-actions")) return;
        const card = el.closest("[data-hw-id]");
        if (card) showHomework(card.dataset.hwId);
      });
    });
    els.sideList.querySelectorAll(".btn-hw-open").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        showHomework(btn.dataset.hwId);
      });
    });
    els.sideList.querySelectorAll(".btn-hw-edit").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        void editHomeworkMeta(btn.dataset.hwId);
      });
    });
    els.sideList.querySelectorAll(".btn-hw-del").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        void deleteHomework(btn.dataset.hwId);
      });
    });
  }

  async function showHomework(id) {
    setProcessingBanner(null);
    state.homeworkDetailId = id;
    const res = await fetch(`${API}/homework/${id}`, { headers: apiHeaders() });
    const entry = await res.json();
    els.mergedTitle.textContent = "作业定稿详情";
    els.questionCount.hidden = false;
    const questions = entry.questions ?? [];
    const nQ = questions.length || entry.parts?.filter((p) => p.kind === "question").length || 0;
    els.questionCount.textContent = nQ > 0 ? `${nQ} 题` : `${entry.parts?.length ?? 0} 段`;
    els.sideList.hidden = true;
    els.resultBody.hidden = false;

    const frameMap = entry.meta?.question_frame_map ?? {};
    const frameIds = entry.source_frame_ids ?? [];
    const frameByQ = {};
    for (const q of questions) {
      frameByQ[q.ordinal] = frameMap[String(q.ordinal)] || frameIds[frameIds.length - 1] || "";
    }
    state.hwView = {
      entry,
      frameByQ,
      focusOrdinal: questions[0]?.ordinal ?? null,
      sessionId: entry.session_id,
    };
    renderHomeworkDetail();
  }

  function buildFrameSelector(q, frameIds, selectedId) {
    if (!frameIds.length) return "";
    const opts = frameIds
      .map(
        (fid, i) =>
          `<option value="${escapeHtml(fid)}"${fid === selectedId ? " selected" : ""}>帧 ${i + 1}</option>`,
      )
      .join("");
    return `<label class="frame-select">预览帧 <select class="hw-frame-select" data-ordinal="${escapeHtml(q.ordinal)}">${opts}</select></label>`;
  }

  function renderHomeworkDetail() {
    const hw = state.hwView;
    if (!hw) return;
    const { entry, frameByQ, focusOrdinal } = hw;
    const questions = entry.questions ?? [];
    const frameIds = entry.source_frame_ids ?? [];
    const backBtn = `<button type="button" class="btn ghost sm hw-back" id="hwBackBtn">← 返回作业列表</button>`;

    const questionsHtml = questions.length
      ? renderHomeworkQuestions(questions, {
          frameSelectorFor: (q) => buildFrameSelector(q, frameIds, frameByQ[q.ordinal]),
          locale: getLocale(),
          editable: true,
        })
      : renderPage(entry.parts ?? [], entry.meta?.question_timings);

    const hwToolbar = `<div class="hw-toolbar">
      <button type="button" class="btn ghost sm" id="hwReEvalBtn">重新评测</button>
      <button type="button" class="btn ghost sm" id="hwEditMetaBtn">编辑备注</button>
      <button type="button" class="btn ghost sm" id="hwDeleteBtn">删除作业</button>
    </div>`;

    const previewHint =
      frameIds.length && entry.session_id
        ? `<p class="hw-preview-hint">点击题目切换左侧帧预览与 bbox；帧图来自 MinIO 缓存</p>`
        : `<p class="hw-preview-hint muted">无帧预览（会话帧已过期或未保存）</p>`;

    els.resultBody.innerHTML = `${backBtn}${hwToolbar}
      <div class="hw-detail-layout">
        <div class="hw-preview-col">
          <div class="hw-preview-stage" id="hwPreviewStage">
            <img id="hwPreviewImg" alt="作业帧预览" />
            <canvas id="hwOverlay"></canvas>
          </div>
          ${previewHint}
        </div>
        <div class="hw-questions-col">${questionsHtml}</div>
      </div>`;

    document.getElementById("hwBackBtn")?.addEventListener("click", () => {
      state.homeworkDetailId = null;
      state.hwView = null;
      els.sideList.hidden = false;
      els.resultBody.hidden = true;
      els.mergedTitle.textContent = "作业库";
    });
    document.getElementById("hwReEvalBtn")?.addEventListener("click", () => void reEvalHomework(entry.id));
    document.getElementById("hwEditMetaBtn")?.addEventListener("click", () => void editHomeworkMeta(entry.id));
    document.getElementById("hwDeleteBtn")?.addEventListener("click", () => void deleteHomework(entry.id));

    wireQuestionCardActions(els.resultBody);

    els.resultBody.querySelectorAll(".hw-frame-select").forEach((sel) => {
      sel.addEventListener("change", () => {
        const ord = sel.dataset.ordinal;
        hw.frameByQ[ord] = sel.value;
        hw.focusOrdinal = ord;
        void loadHwPreview();
      });
    });

    els.resultBody.querySelectorAll(".question-card.homework-q").forEach((card) => {
      card.addEventListener("click", (ev) => {
        if (ev.target.closest("button, select, summary, a")) return;
        hw.focusOrdinal = card.dataset.ordinal;
        void loadHwPreview();
        els.resultBody.querySelectorAll(".question-card.homework-q").forEach((c) => {
          c.classList.toggle("hw-focused", c.dataset.ordinal === hw.focusOrdinal);
        });
      });
    });

    void loadHwPreview();
  }

  async function loadHwPreview() {
    const hw = state.hwView;
    if (!hw?.focusOrdinal) return;
    const img = document.getElementById("hwPreviewImg");
    const canvas = document.getElementById("hwOverlay");
    if (!img || !canvas) return;

    const q = (hw.entry.questions ?? []).find((row) => row.ordinal === hw.focusOrdinal);
    const frameId = hw.frameByQ[hw.focusOrdinal];
    const sessionId = hw.sessionId;

    if (!q || !frameId || !sessionId) {
      clearOverlay(canvas);
      img.removeAttribute("src");
      return;
    }

    try {
      const res = await fetch(`${API}/sessions/${sessionId}/frames/${frameId}/preview`, {
        headers: apiHeaders(),
      });
      if (!res.ok) throw new Error("preview unavailable");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      img.onload = () => {
        URL.revokeObjectURL(url);
        const parts = [
          {
            kind: "question",
            id: q.ordinal,
            bbox_print: q.bbox_print,
            bbox_handwriting: q.bbox_handwriting,
          },
        ];
        drawOverlay(img, canvas, overlayFromParts(parts));
      };
      img.onerror = () => {
        clearOverlay(canvas);
      };
      img.src = url;
    } catch {
      clearOverlay(canvas);
      const hint = document.querySelector(".hw-preview-hint");
      if (hint) hint.textContent = "帧图已过期或不可读，仅显示文本";
    }

    els.resultBody.querySelectorAll(".question-card.homework-q").forEach((c) => {
      c.classList.toggle("hw-focused", c.dataset.ordinal === hw.focusOrdinal);
    });
  }

  async function pollGraphEval(homeworkId, maxSec = 180) {
    showToast(getLocale() === "en" ? "Agent evaluating…" : "Agent 评测中…", "info");
    for (let i = 0; i < maxSec; i++) {
      const res = await fetch(`${API}/graph-runs`, { headers: apiHeaders() });
      if (!res.ok) break;
      const runs = await res.json();
      const related = runs.filter((r) => r.homework_id === homeworkId);
      const run = related.find((r) => r.status === "succeeded");
      const failed = related.find((r) => r.status === "failed");
      if (run) {
        const evalStep = (run.steps ?? []).find((s) => s.node === "eval_agent");
        if (evalStep && parseEvalUpdated(evalStep.detail) > 0) {
          showToast(getLocale() === "en" ? "Agent eval complete" : "Agent 评测完成", "ok");
          await setSideTab("wrongbook");
          return true;
        }
      }
      if (failed && i > 5) {
        const hasEval = (failed.steps ?? []).some(
          (s) => s.node === "eval_agent" && parseEvalUpdated(s.detail) > 0,
        );
        if (hasEval) {
          showToast(getLocale() === "en" ? "Agent eval done (graph partial)" : "Agent 评测完成（graph 部分失败）", "ok");
          await setSideTab("wrongbook");
          return true;
        }
        if (i > 15 && !(failed.steps ?? []).some((s) => s.node === "eval_agent")) {
          showToast(
            getLocale() === "en"
              ? "Graph failed before eval — use Re-eval in wrong book"
              : "Graph 在评测前失败，请点错题本「重新 Agent 评测」",
            "warn",
          );
          return false;
        }
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
    showToast(getLocale() === "en" ? "Eval timeout — check Agent tab or re-eval" : "评测超时，请查看 Agent 或点重新评测", "warn");
    return false;
  }

  async function restoreAgentVerdict(questionId) {
    await fetch(`${API}/questions/${encodeURIComponent(questionId)}`, {
      method: "PATCH",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ restore_agent: true }),
    });
    await loadWrongBook();
    showToast(getLocale() === "en" ? "Restored Agent verdict" : "已恢复 Agent 判定", "ok");
  }

  async function patchQuestionWrong(questionId, markWrong) {
    await fetch(`${API}/questions/${encodeURIComponent(questionId)}`, {
      method: "PATCH",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ is_wrong: markWrong }),
    });
    if (state.homeworkDetailId) await showHomework(state.homeworkDetailId);
    await loadWrongBook();
    showToast(markWrong ? (getLocale() === "en" ? "Added to wrong book" : "已加入错题本") : (getLocale() === "en" ? "Removed from wrong book" : "已移出错题本"), "ok");
  }

  async function deleteQuestion(questionId) {
    if (!confirm(getLocale() === "en" ? "Delete this question?" : "确定删除该题目？")) return;
    await fetch(`${API}/questions/${encodeURIComponent(questionId)}`, {
      method: "DELETE",
      headers: apiHeaders(),
    });
    if (state.homeworkDetailId) await showHomework(state.homeworkDetailId);
    else await loadHomework();
    showToast(getLocale() === "en" ? "Question deleted" : "题目已删除", "ok");
  }

  async function editQuestionFields(questionId) {
    const entry = state.hwView?.entry;
    const q = (entry?.questions ?? []).find((x) => x.id === questionId);
    if (!q) return;
    const print_text = prompt(getLocale() === "en" ? "Print text:" : "印刷题干：", q.print_text || "");
    if (print_text === null) return;
    const handwriting = prompt(getLocale() === "en" ? "Handwriting:" : "手写作答：", q.handwriting || "");
    if (handwriting === null) return;
    await fetch(`${API}/questions/${encodeURIComponent(questionId)}`, {
      method: "PATCH",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ print_text, handwriting }),
    });
    await showHomework(state.homeworkDetailId);
    showToast(getLocale() === "en" ? "Question updated" : "题目已更新", "ok");
  }

  async function deleteHomework(hwId) {
    if (!confirm(getLocale() === "en" ? "Delete this homework?" : "确定删除该作业？不可恢复。")) return;
    await fetch(`${API}/homework/${encodeURIComponent(hwId)}`, {
      method: "DELETE",
      headers: apiHeaders(),
    });
    state.homeworkDetailId = null;
    state.hwView = null;
    els.sideList.hidden = false;
    els.resultBody.hidden = true;
    await loadHomework();
    showToast(getLocale() === "en" ? "Homework deleted" : "作业已删除", "ok");
  }

  async function editHomeworkMeta(hwId) {
    const title = prompt(getLocale() === "en" ? "Display title:" : "显示标题：", "");
    if (title === null) return;
    const note = prompt(getLocale() === "en" ? "Note:" : "备注：", "");
    if (note === null) return;
    await fetch(`${API}/homework/${encodeURIComponent(hwId)}`, {
      method: "PATCH",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ meta: { title, note } }),
    });
    await showHomework(hwId);
  }

  async function reEvalWrongBook(allHomework = false) {
    showToast(getLocale() === "en" ? "Agent re-eval running…" : "Agent 重新评测中…", "info");
    const qs = allHomework ? "?all_homework=true" : "";
    const res = await fetch(`${API}/wrong-book/re-eval${qs}`, {
      method: "POST",
      headers: apiHeaders(),
    });
    if (!res.ok) {
      showToast(getLocale() === "en" ? "Re-eval failed" : "重新评测失败", "err");
      return;
    }
    const data = await res.json();
    await loadWrongBook();
    showToast(
      getLocale() === "en"
        ? `Re-eval done: ${data.eval_updated ?? 0} questions`
        : `重新评测完成：${data.eval_updated ?? 0} 题`,
      "ok",
    );
  }

  async function reEvalHomework(hwId) {
    showToast(getLocale() === "en" ? "Re-eval started…" : "重新评测中…", "info");
    await fetch(`${API}/homework/${encodeURIComponent(hwId)}/re-eval`, {
      method: "POST",
      headers: apiHeaders(),
    });
    await showHomework(hwId);
    await pollGraphEval(hwId, 120);
  }

  async function deleteMastery(kpId) {
    if (!confirm(getLocale() === "en" ? "Delete this mastery entry?" : "确定删除该知识点记录？")) return;
    await fetch(`${API}/mastery/${encodeURIComponent(kpId)}`, {
      method: "DELETE",
      headers: apiHeaders(),
    });
    await loadMastery();
  }

  async function recomputeMastery() {
    await fetch(`${API}/mastery/recompute`, { method: "POST", headers: apiHeaders() });
    await loadMastery();
    showToast(getLocale() === "en" ? "Mastery recomputed" : "已重新聚合 A⁺", "ok");
  }

  async function showAddKpDialog() {
    const res = await fetch(`${API}/rag/syllabus-kps`, { headers: apiHeaders() });
    if (!res.ok) return;
    const kps = await res.json();
    const pick = prompt(
      (getLocale() === "en" ? "Enter kp_id from syllabus:\n" : "输入考纲 kp_id：\n") +
        kps.slice(0, 8).map((k) => `${k.kp_id} · ${k.label}`).join("\n"),
    );
    if (!pick) return;
    await fetch(`${API}/mastery`, {
      method: "POST",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ kp_id: pick.trim(), mastery: "unknown" }),
    });
    await loadMastery();
  }

  async function loadWrongBook() {
    const res = await fetch(`${API}/wrong-book`, { headers: apiHeaders() });
    if (!res.ok) {
      els.questionCount.hidden = false;
      els.questionCount.textContent = "加载失败";
      els.resultBody.innerHTML = `<p class="placeholder error">错题本加载失败（${res.status}）</p>`;
      return;
    }
    const data = await res.json();
    els.questionCount.hidden = false;
    els.questionCount.textContent = `${data.count ?? 0} 错题 · ${data.candidate_count ?? 0} 待复核`;
    els.resultBody.innerHTML = renderWrongBookPanel(data, getLocale());
    wireWrongBookActions();
  }

  async function showHomeworkEvalTrace(hwId) {
    state.sideTab = "homework";
    await showHomework(hwId);
    requestAnimationFrame(() => {
      els.resultBody.querySelectorAll(".eval-trace-timeline").forEach((el) => {
        el.open = true;
      });
      const first = els.resultBody.querySelector(".eval-trace-timeline");
      if (first) first.scrollIntoView({ behavior: "smooth", block: "nearest" });
    });
    showToast(getLocale() === "en" ? "Homework eval trace opened" : "已打开作业评测调试", "ok");
  }

  function wireAgentRunActions() {
    els.resultBody.querySelectorAll(".btn-agent-hw-trace").forEach((btn) => {
      btn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        const hwId = btn.dataset.hwid;
        if (hwId) void showHomeworkEvalTrace(hwId);
      });
    });
  }

  async function loadAgentRuns() {
    const res = await fetch(`${API}/graph-runs`, { headers: apiHeaders() });
    const runs = res.ok ? await res.json() : [];
    els.questionCount.hidden = false;
    els.questionCount.textContent = `${runs.length} runs`;
    if (!runs.length) {
      els.resultBody.innerHTML =
        '<p class="placeholder">暂无 Agent 运行记录<br><small>定稿 ingest 后自动触发 LangGraph</small></p>';
      return;
    }
    els.resultBody.innerHTML = runs
      .slice(0, 20)
      .map(
        (r) => {
          const evalStep = (r.steps ?? []).find((s) => s.node === "eval_agent");
          const evalDetail = evalStep?.detail ? escapeHtml(evalStep.detail) : "";
          const traceLbl = getLocale() === "en" ? "Eval trace" : "评测调试";
          const hwBtn = r.homework_id
            ? `<button type="button" class="btn ghost sm btn-agent-hw-trace" data-hwid="${escapeHtml(r.homework_id)}">${traceLbl}</button>`
            : "";
          return `<article class="list-card agent-run">
          <header><span class="badge">${escapeHtml(r.status)}</span> ${escapeHtml(r.started_at?.slice(0, 19) ?? r.id.slice(0, 8))}</header>
          <p>homework: <code>${escapeHtml(r.homework_id ?? "")}</code> ${hwBtn}</p>
          ${evalDetail ? `<p class="muted agent-eval-detail">${evalDetail}</p>` : ""}
          <ol class="agent-steps">${(r.steps ?? [])
            .map((s) => `<li><code>${escapeHtml(s.node)}</code> ${escapeHtml(s.detail ?? "")}</li>`)
            .join("")}</ol>
        </article>`;
        },
      )
      .join("");
    wireAgentRunActions();
  }

  async function loadMastery() {
    await fetchTierInfo();
    const res = await fetch(`${API}/buddy/mastery`, { headers: apiHeaders() });
    const records = res.ok ? await res.json() : [];
    els.questionCount.hidden = false;
    els.questionCount.textContent = records.length > 0 ? `${records.length} 知识点` : "0 知识点";
    els.resultBody.innerHTML = renderMasteryPanel(records, {
      demo: false,
      tierInfo: state.tierInfo,
      locale: getLocale(),
    });
    wireMasteryActions();
  }

  async function patchMastery(kpId, mastery) {
    await fetch(`${API}/mastery/${encodeURIComponent(kpId)}`, {
      method: "PATCH",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ mastery }),
    });
    await loadMastery();
  }

  function setSideTab(tab) {
    state.sideTab = tab;
    state.homeworkDetailId = null;
    setProcessingBanner(null);
    els.sideTabs.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === tab);
    });
    if (els.tabHelp) els.tabHelp.textContent = TAB_HELP[tab] ?? "";
    const listMode = tab === "homework";
    els.sideList.hidden = !listMode;
    els.resultBody.hidden = listMode && !state.homeworkDetailId;
    els.mergedTitle.textContent =
      tab === "homework"
        ? "作业库"
        : tab === "mastery"
          ? "A⁺ 掌握度"
          : tab === "wrongbook"
            ? "错题本"
            : tab === "agentruns"
              ? "Agent Runs"
              : tab === "frame"
                ? "本帧 OCR"
                : tab === "compare"
                  ? "OCR 对比"
                  : "当前页合并结果";
    if (els.showOverlay) {
      els.showOverlay.disabled = tab !== "frame";
      if (tab !== "frame") {
        state.showOverlay = false;
      } else {
        state.showOverlay = els.showOverlay.checked;
      }
    }
    return refreshSideList();
  }

  async function refreshSideList() {
    if (state.sideTab === "homework") await loadHomework();
    else if (state.sideTab === "mastery") await loadMastery();
    else if (state.sideTab === "wrongbook") await loadWrongBook();
    else if (state.sideTab === "agentruns") await loadAgentRuns();
    else renderActivePanel();
  }

  function applySession(session, opts = {}) {
    const { skipPreview = false } = opts;
    state.session = session;
    if (!state.selectedFrameId && session.frames?.length) {
      state.selectedFrameId = session.frames[session.frames.length - 1].id;
    }
    renderTimeline();
    renderEvents();
    renderActivePanel({ skipOverlay: true });
    setSessionBar();
    if (
      !skipPreview &&
      !state.viewingPendingUpload &&
      state.selectedFrameId &&
      state.sessionId
    ) {
      void loadFramePreview(state.selectedFrameId);
    }
  }

  async function createSession() {
    revokeFrameCache();
    revokeObjectUrl();
    revokeQueueUrls();
    state.batchQueue = [];
    state.selectedQueueKey = null;
    state.viewingPendingUpload = false;
    state.batchAbort?.abort();
    state.batchAbort = null;
    state.pipelineTiming = null;
    renderBatchQueue();
    setProcessingBanner(null);

    saveStudentId();
    if (!(await ensureStudentRegistered())) return;

    const res = await fetch(`${API}/sessions`, {
      method: "POST",
      headers: apiHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify({ subject: "math", note: "" }),
    });
    const data = await res.json();
    state.sessionId = data.session.id;
    state.selectedFrameId = null;
    showPreviewLoading("从队列选图或点击 Timeline 帧");
    applySession(data.session);
    setWorkflowStep(1);
  }

  async function finalizePage() {
    if (!state.sessionId) return;
    const res = await fetch(`${API}/sessions/${state.sessionId}/finalize-page`, {
      method: "POST",
      headers: apiHeaders(),
    });
    const data = await res.json();
    applySession(data.session);
    const hwId = data.homework?.id;
    await loadHomework();
    setSideTab("homework");
    if (hwId) {
      showHomework(hwId);
      void pollGraphEval(hwId);
    }
    showToast("当前页已写入作业库 · Agent 评测已排队", "ok");
    setWorkflowStep(4);
  }

  async function endSession() {
    if (!state.sessionId) return;
    const res = await fetch(`${API}/sessions/${state.sessionId}/end`, {
      method: "POST",
      headers: apiHeaders(),
    });
    const data = await res.json();
    applySession(data.session);
    const hwId = data.homework?.id;
    await loadHomework();
    if (hwId) void pollGraphEval(hwId);
    setSideTab("mastery");
    showToast("会话已结束 · Agent 评测已排队", "ok");
    setWorkflowStep(4);
  }

  async function exportSession() {
    if (!state.sessionId) return;
    const res = await fetch(`${API}/sessions/${state.sessionId}/export`, { headers: apiHeaders() });
    const data = await res.json();
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `companion-${state.sessionId.slice(0, 8)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  function wireEvents() {
    els.btnNewSession.addEventListener("click", createSession);
    els.btnProcessOne.addEventListener("click", processOne);
    els.btnProcessAll.addEventListener("click", processAll);
    els.btnProcessBatch?.addEventListener("click", processBatchAccelerated);
    els.btnCancelBatch?.addEventListener("click", cancelBatch);
    els.btnClearQueue.addEventListener("click", clearQueue);
    els.btnFinalizeDock?.addEventListener("click", finalizePage);
    els.btnEndSessionDock?.addEventListener("click", endSession);
    els.btnExport.addEventListener("click", exportSession);

    els.hideDoneQueue?.addEventListener("change", () => {
      state.hideDoneQueue = els.hideDoneQueue.checked;
      renderBatchQueue();
    });

    els.showOverlay?.addEventListener("change", () => {
      state.showOverlay = els.showOverlay.checked && state.sideTab === "frame";
      refreshOverlay();
    });

    els.dropzone.addEventListener("click", () => els.fileInput.click());
    els.fileInput.addEventListener("change", () => {
      if (els.fileInput.files?.length) void enqueueFiles(els.fileInput.files);
      els.fileInput.value = "";
    });
    els.dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      els.dropzone.classList.add("dragover");
    });
    els.dropzone.addEventListener("dragleave", () => els.dropzone.classList.remove("dragover"));
    els.dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      els.dropzone.classList.remove("dragover");
      if (e.dataTransfer.files?.length) void enqueueFiles(e.dataTransfer.files);
    });

    els.sideTabs.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", async () => {
        setSideTab(tab.dataset.tab);
        refreshOverlay();
        await refreshSideList();
      });
    });

    window.addEventListener("resize", () => refreshOverlay());

    if (els.localeSelect) {
      els.localeSelect.value = getLocale();
      els.localeSelect.addEventListener("change", async () => {
        setLocale(els.localeSelect.value);
        state.locale = getLocale();
        await refreshSideList();
      });
    }
  }

  return {
    init() {
      void refreshStudentPicker();
      if (els.studentIdInput) {
        const saved = localStorage.getItem(LS_STUDENT);
        if (saved) els.studentIdInput.value = saved;
        els.studentIdInput.addEventListener("change", saveStudentId);
        els.studentIdInput.addEventListener("blur", () => void ensureStudentRegistered());
      }
      wireEvents();
      state.hideDoneQueue = els.hideDoneQueue?.checked ?? true;
      setSideTab("merged");
      renderBatchQueue();
      setSessionBar();
      setWorkflowStep(1);
      createSession();
    },
  };
}
