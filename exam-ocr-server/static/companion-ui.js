import {
  ACTION_LABELS,
  drawOverlay,
  escapeHtml,
  overlayFromParts,
  renderPage,
} from "./render-utils.js";
import { isImageFile, normalizeImageFile } from "./image-api.js";

const API = "/api/v1/companion";

export function createCompanionUI(els) {
  const state = {
    sessionId: null,
    session: null,
    file: null,
    normalizedBlob: null,
    objectUrl: null,
    selectedFrameId: null,
    sideTab: "merged",
    normalizeToken: 0,
    viewingPendingUpload: false,
    pendingOverlayParts: [],
    framePreviewCache: new Map(),
  };

  function revokeObjectUrl() {
    if (state.objectUrl) {
      URL.revokeObjectURL(state.objectUrl);
      state.objectUrl = null;
    }
  }

  function overlayPartsForPreview() {
    if (!state.session) return [];
    if (state.sideTab === "frame" && state.selectedFrameId) {
      const frame = state.session.frames.find((f) => f.id === state.selectedFrameId);
      return frame?.ocr_parts ?? [];
    }
    if (state.viewingPendingUpload) return [];
    return state.session.merged_parts ?? [];
  }

  function drawPendingOverlay() {
    drawOverlay(els.previewImg, els.overlay, overlayFromParts(state.pendingOverlayParts));
  }

  function setSessionBar() {
    const active = state.session?.status === "active";
    els.sessionIdLabel.textContent = state.sessionId
      ? `${state.sessionId.slice(0, 8)}… · ${active ? "进行中" : "已结束"}`
      : "未创建会话";
    els.btnAddFrame.disabled = !active || !state.normalizedBlob;
    els.btnFinalize.disabled = !active;
    els.btnEndSession.disabled = !state.sessionId;
    els.btnExport.disabled = !state.sessionId;
  }

  function framePreviewUrl(frameId) {
    return `${API}/sessions/${state.sessionId}/frames/${frameId}/preview`;
  }

  function showPreviewLoading(message) {
    const placeholder = els.canvasWrap.querySelector(".placeholder");
    if (placeholder) {
      placeholder.hidden = false;
      placeholder.innerHTML = `<div class="loading"><span class="spinner"></span>${escapeHtml(message)}</div>`;
    }
    if (els.previewStage) els.previewStage.hidden = true;
    els.previewImg.removeAttribute("src");
    els.overlay.hidden = true;
  }

  function showPreviewStage() {
    const placeholder = els.canvasWrap.querySelector(".placeholder");
    if (placeholder) placeholder.hidden = true;
    if (els.previewStage) els.previewStage.hidden = false;
  }

  function setPreviewFromUrl(url, onReady) {
    showPreviewStage();
    els.previewImg.onload = () => {
      if (onReady) onReady();
      else drawPendingOverlay();
    };
    els.previewImg.onerror = () => {
      showPreviewLoading("预览加载失败");
    };
    els.previewImg.src = url;
    if (els.previewImg.complete && els.previewImg.naturalWidth) {
      els.previewImg.onload();
    }
  }

  async function loadFramePreview(frameId) {
    const cached = state.framePreviewCache.get(frameId);
    if (cached) {
      setPreviewFromUrl(cached, () => drawPendingOverlay());
      return;
    }
    const url = framePreviewUrl(frameId);
    try {
      const res = await fetch(url);
      if (!res.ok) {
        showPreviewLoading(`帧预览不可用（${res.status}）`);
        return;
      }
      const blob = await res.blob();
      const blobUrl = URL.createObjectURL(blob);
      state.framePreviewCache.set(frameId, blobUrl);
      setPreviewFromUrl(blobUrl, () => drawPendingOverlay());
    } catch {
      showPreviewLoading("预览加载失败");
    }
  }

  function renderTimeline() {
    const frames = state.session?.frames ?? [];
    if (!frames.length) {
      els.timeline.innerHTML = '<p class="timeline-empty">创建会话后加帧</p>';
      return;
    }
    els.timeline.innerHTML = frames
      .map((f) => {
        const action = ACTION_LABELS[f.scheduler_action] ?? f.scheduler_action;
        const active = f.id === state.selectedFrameId ? " active" : "";
        const dur = f.duration_ms ? `${(f.duration_ms / 1000).toFixed(1)}s` : "";
        const score =
          f.same_page_score != null ? ` · 同页 ${(f.same_page_score * 100).toFixed(0)}%` : "";
        const skip = f.skip_reason ? ` · ${f.skip_reason}` : "";
        return `<button type="button" class="timeline-item${active}" data-frame-id="${escapeHtml(f.id)}">
          <span class="tl-seq">#${f.seq}</span>
          <span class="tl-action badge-${escapeHtml(f.scheduler_action)}">${escapeHtml(action)}</span>
          <span class="tl-meta">${escapeHtml(dur)}${escapeHtml(score)}${escapeHtml(skip)}</span>
        </button>`;
      })
      .join("");

    els.timeline.querySelectorAll(".timeline-item").forEach((btn) => {
      btn.addEventListener("click", () => selectFrame(btn.dataset.frameId));
    });
  }

  function renderEvents() {
    const events = state.session?.events ?? [];
    if (!events.length) {
      els.eventLog.innerHTML = '<p class="timeline-empty">暂无事件</p>';
      return;
    }
    const tail = events.slice(-12);
    els.eventLog.innerHTML = tail
      .map(
        (e) =>
          `<div class="event-row"><span class="event-ts">${escapeHtml(e.ts?.slice(11, 19) ?? "")}</span>
          <span class="event-type">${escapeHtml(e.type)}</span></div>`,
      )
      .join("");
  }

  function selectFrame(frameId) {
    state.viewingPendingUpload = false;
    state.selectedFrameId = frameId;
    const frame = state.session?.frames?.find((f) => f.id === frameId);
    if (!frame) return;
    renderTimeline();
    state.pendingOverlayParts = overlayPartsForPreview();
    if (state.sessionId) {
      void loadFramePreview(frameId);
    }
    if (state.sideTab === "frame") {
      renderMergedOrFrame({ skipOverlay: true });
    }
    els.frameMeta.hidden = false;
    els.frameMeta.textContent = `#${frame.seq} · ${ACTION_LABELS[frame.scheduler_action] ?? frame.scheduler_action}`;
  }

  function renderMergedOrFrame(opts = {}) {
    const { skipOverlay = false } = opts;
    let parts = state.session?.merged_parts ?? [];
    let title = "当前页合并结果";
    if (state.sideTab === "frame" && state.selectedFrameId) {
      const frame = state.session.frames.find((f) => f.id === state.selectedFrameId);
      parts = frame?.ocr_parts ?? [];
      title = `帧 OCR · #${frame?.seq ?? "?"}`;
    }
    els.mergedTitle.textContent = title;
    const nQ = parts.filter((p) => p.kind === "question").length;
    els.questionCount.hidden = false;
    els.questionCount.textContent = nQ > 0 ? `${nQ} 题` : `${parts.length} 段`;
    els.resultBody.innerHTML = renderPage(parts);
    if (!skipOverlay && (state.sideTab === "merged" || state.sideTab === "frame")) {
      state.pendingOverlayParts = overlayPartsForPreview();
      drawPendingOverlay();
    }
  }

  async function loadHomework() {
    const res = await fetch(`${API}/homework`);
    const data = await res.json();
    const entries = data.entries ?? [];
    if (!entries.length) {
      els.sideList.innerHTML = '<p class="placeholder">暂无定稿作业</p>';
      return;
    }
    els.sideList.innerHTML = entries
      .map(
        (e) =>
          `<article class="list-card" data-hw-id="${escapeHtml(e.id)}">
            <header>${escapeHtml(e.finalized_at?.slice(0, 19) ?? e.id.slice(0, 8))}</header>
            <p>${e.parts?.filter((p) => p.kind === "question").length ?? 0} 题 · ${escapeHtml(e.page_id ?? "")}</p>
          </article>`,
      )
      .join("");
    els.sideList.querySelectorAll(".list-card").forEach((card) => {
      card.addEventListener("click", () => showHomework(card.dataset.hwId));
    });
  }

  async function showHomework(id) {
    const res = await fetch(`${API}/homework/${id}`);
    const entry = await res.json();
    els.mergedTitle.textContent = "作业定稿";
    els.questionCount.hidden = false;
    els.questionCount.textContent = `${entry.parts?.length ?? 0} 段`;
    els.resultBody.innerHTML = renderPage(entry.parts ?? []);
  }

  async function loadMastery() {
    const res = await fetch(`${API}/mastery`);
    const data = await res.json();
    const records = data.records ?? [];
    if (!records.length) {
      els.sideList.innerHTML = '<p class="placeholder">暂无掌握度记录（demo）</p>';
      return;
    }
    els.sideList.innerHTML = records
      .map(
        (r) =>
          `<article class="list-card mastery-card" data-kp="${escapeHtml(r.kp_id)}">
            <header>${escapeHtml(r.label ?? r.kp_id)}</header>
            <select class="mastery-select" data-kp="${escapeHtml(r.kp_id)}">
              ${["unknown", "learning", "mastered"].map(
                (m) =>
                  `<option value="${m}"${r.mastery === m ? " selected" : ""}>${m}</option>`,
              ).join("")}
            </select>
          </article>`,
      )
      .join("");
    els.sideList.querySelectorAll(".mastery-select").forEach((sel) => {
      sel.addEventListener("change", () => patchMastery(sel.dataset.kp, sel.value));
    });
  }

  async function patchMastery(kpId, mastery) {
    await fetch(`${API}/mastery/${encodeURIComponent(kpId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mastery }),
    });
    await loadMastery();
  }

  function setSideTab(tab) {
    state.sideTab = tab;
    els.sideTabs.querySelectorAll(".tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === tab);
    });
    const listMode = tab === "homework" || tab === "mastery";
    els.sideList.hidden = !listMode;
    els.resultBody.hidden = listMode;
    els.mergedTitle.textContent =
      tab === "homework"
        ? "作业库"
        : tab === "mastery"
          ? "A⁺ 掌握度（demo）"
          : tab === "frame"
            ? "本帧 OCR"
            : "当前页合并结果";
  }

  async function refreshSideList() {
    if (state.sideTab === "homework") await loadHomework();
    else if (state.sideTab === "mastery") await loadMastery();
    else renderMergedOrFrame();
  }

  function applySession(session, opts = {}) {
    const { skipPreview = false } = opts;
    state.session = session;
    if (!state.selectedFrameId && session.frames?.length) {
      state.selectedFrameId = session.frames[session.frames.length - 1].id;
    }
    renderTimeline();
    renderEvents();
    renderMergedOrFrame({ skipOverlay: true });
    setSessionBar();
    if (
      !skipPreview &&
      !state.viewingPendingUpload &&
      state.selectedFrameId &&
      state.sessionId
    ) {
      state.pendingOverlayParts = overlayPartsForPreview();
      void loadFramePreview(state.selectedFrameId);
    }
  }

  async function createSession() {
    state.framePreviewCache.forEach((url) => URL.revokeObjectURL(url));
    state.framePreviewCache.clear();
    revokeObjectUrl();
    state.viewingPendingUpload = false;
    const res = await fetch(`${API}/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    const data = await res.json();
    state.sessionId = data.session.id;
    state.selectedFrameId = null;
    state.normalizedBlob = null;
    state.file = null;
    showPreviewLoading("上传图片后在此显示");
    applySession(data.session);
  }

  async function setFile(file) {
    if (!isImageFile(file)) {
      els.resultBody.innerHTML =
        '<div class="error-box">不支持的文件类型，请上传图片（含 HEIC/.JPG 等）</div>';
      return;
    }

    const token = ++state.normalizeToken;
    state.viewingPendingUpload = true;
    state.selectedFrameId = null;
    revokeObjectUrl();
    state.file = file;
    state.normalizedBlob = null;
    setSessionBar();
    showPreviewLoading("转码为 JPEG 预览中…");

    try {
      const { blob, url, sourceFormat, width, height } = await normalizeImageFile(file);
      if (token !== state.normalizeToken) {
        URL.revokeObjectURL(url);
        return;
      }
      state.normalizedBlob = blob;
      state.objectUrl = url;
      state.pendingOverlayParts = [];
      setPreviewFromUrl(url);
      els.frameMeta.hidden = false;
      els.frameMeta.textContent =
        sourceFormat && sourceFormat !== "JPEG"
          ? `待加帧 · ${file.name} · ${sourceFormat}→JPEG · ${width}×${height}`
          : `待加帧 · ${file.name} · ${width}×${height}`;
    } catch (err) {
      if (token !== state.normalizeToken) return;
      state.viewingPendingUpload = false;
      showPreviewLoading("转码失败");
      els.resultBody.innerHTML = `<div class="error-box">${escapeHtml(err.message ?? String(err))}</div>`;
    } finally {
      setSessionBar();
    }
  }

  async function addFrame() {
    if (!state.sessionId || !state.normalizedBlob) return;
    els.btnAddFrame.disabled = true;
    els.resultBody.innerHTML =
      '<div class="loading"><span class="spinner"></span>加帧识别中…</div>';
    const uploadUrl = state.objectUrl;
    try {
      const form = new FormData();
      form.append("file", state.normalizedBlob, "frame.jpg");
      const res = await fetch(`${API}/sessions/${state.sessionId}/frames`, {
        method: "POST",
        headers: { "X-Already-Normalized": "1" },
        body: form,
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? JSON.stringify(data));
      state.viewingPendingUpload = false;
      state.selectedFrameId = data.frame_id;
      if (uploadUrl) {
        state.framePreviewCache.set(data.frame_id, uploadUrl);
        state.objectUrl = null;
      }
      state.normalizedBlob = null;
      state.file = null;
      applySession(data.session, { skipPreview: true });
      if (data.homework_finalized) {
        await loadHomework();
      }
      selectFrame(data.frame_id);
    } catch (err) {
      els.resultBody.innerHTML = `<div class="error-box">${escapeHtml(err.message ?? String(err))}</div>`;
    } finally {
      setSessionBar();
    }
  }

  async function finalizePage() {
    if (!state.sessionId) return;
    const res = await fetch(`${API}/sessions/${state.sessionId}/finalize-page`, { method: "POST" });
    const data = await res.json();
    applySession(data.session);
    await loadHomework();
    if (data.homework) showHomework(data.homework.id);
  }

  async function endSession() {
    if (!state.sessionId) return;
    const res = await fetch(`${API}/sessions/${state.sessionId}/end`, { method: "POST" });
    const data = await res.json();
    applySession(data.session);
    await loadHomework();
    await loadMastery();
  }

  async function exportSession() {
    if (!state.sessionId) return;
    const res = await fetch(`${API}/sessions/${state.sessionId}/export`);
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
    els.btnAddFrame.addEventListener("click", addFrame);
    els.btnFinalize.addEventListener("click", finalizePage);
    els.btnEndSession.addEventListener("click", endSession);
    els.btnExport.addEventListener("click", exportSession);

    els.dropzone.addEventListener("click", () => els.fileInput.click());
    els.fileInput.addEventListener("change", () => {
      const file = els.fileInput.files?.[0];
      if (file) setFile(file);
    });
    els.dropzone.addEventListener("dragover", (e) => {
      e.preventDefault();
      els.dropzone.classList.add("dragover");
    });
    els.dropzone.addEventListener("dragleave", () => els.dropzone.classList.remove("dragover"));
    els.dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      els.dropzone.classList.remove("dragover");
      const file = e.dataTransfer.files?.[0];
      if (file) setFile(file);
    });

    els.sideTabs.querySelectorAll(".tab").forEach((tab) => {
      tab.addEventListener("click", async () => {
        setSideTab(tab.dataset.tab);
        state.pendingOverlayParts = overlayPartsForPreview();
        drawPendingOverlay();
        await refreshSideList();
      });
    });

    window.addEventListener("resize", () => {
      if (state.sideTab === "merged" || state.sideTab === "frame") {
        drawPendingOverlay();
      }
    });
  }

  return {
    init() {
      wireEvents();
      setSideTab("merged");
      setSessionBar();
      createSession();
    },
  };
}
