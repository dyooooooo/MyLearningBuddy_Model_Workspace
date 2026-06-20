import { createCompanionUI } from "./companion-ui.js?v=20260625";
import { drawOverlay, overlayFromParts, renderPage } from "./render-utils.js";
import { isImageFile, normalizeImageFile } from "./image-api.js";

const $ = (sel) => document.querySelector(sel);

const els = {
  healthStatus: $("#healthStatus"),
  modelInfo: $("#modelInfo"),
  sessionIdLabel: $("#sessionIdLabel"),
  studentIdInput: $("#studentIdInput"),
  studentIdList: $("#studentIdList"),
  studentBanner: $("#studentBanner"),
  btnNewSession: $("#btnNewSession"),
  btnProcessOne: $("#btnProcessOne"),
  btnProcessAll: $("#btnProcessAll"),
  btnProcessBatch: $("#btnProcessBatch"),
  btnCancelBatch: $("#btnCancelBatch"),
  btnClearQueue: $("#btnClearQueue"),
  batchQueue: $("#batchQueue"),
  batchProgress: $("#batchProgress"),
  batchProgressLabel: $("#batchProgressLabel"),
  batchProgressFill: $("#batchProgressFill"),
  hideDoneQueue: $("#hideDoneQueue"),
  tabHelp: $("#tabHelp"),
  btnExport: $("#btnExport"),
  btnFinalizeDock: $("#btnFinalizeDock"),
  btnEndSessionDock: $("#btnEndSessionDock"),
  dockHint: $("#dockHint"),
  workflowSteps: $("#workflowSteps"),
  toastHost: $("#toastHost"),
  dropzone: $("#dropzone"),
  fileInput: $("#fileInput"),
  timeline: $("#timeline"),
  eventLog: $("#eventLog"),
  canvasWrap: $("#canvasWrap"),
  previewStage: $("#previewStage"),
  previewImg: $("#previewImg"),
  overlay: $("#overlay"),
  showOverlay: $("#showOverlay"),
  mergedTitle: $("#mergedTitle"),
  questionCount: $("#questionCount"),
  resultBody: $("#resultBody"),
  processingBanner: $("#processingBanner"),
  processingBannerText: $("#processingBannerText"),
  frameMeta: $("#frameMeta"),
  sideTabs: $("#sideTabs"),
  sideList: $("#sideList"),
  labRunBtn: $("#labRunBtn"),
  labFileInput: $("#labFileInput"),
  labDropzone: $("#labDropzone"),
  labResult: $("#labResult"),
  labPreviewStage: $("#labPreviewStage"),
  labPreview: $("#labPreview"),
  labOverlay: $("#labOverlay"),
};

function setHealth(ok, label, detail) {
  els.healthStatus.className = `status ${ok ? "ok" : ok === false ? "err" : "warn"}`;
  els.healthStatus.querySelector(".label").textContent = label;
  if (detail) els.modelInfo.textContent = detail;
}

async function pollHealth() {
  try {
    const res = await fetch("/api/v1/system/health");
    const data = await res.json();
    const reachable = data.vllm_reachable === true;
    setHealth(
      reachable,
      reachable ? "vLLM 就绪" : "网关在线 · vLLM 未就绪",
      `${data.vllm_model ?? ""} · ${data.vllm_base_url ?? ""}`,
    );
  } catch {
    setHealth(false, "网关不可达", "");
  }
}

const labState = { file: null, normalizedBlob: null, objectUrl: null, token: 0 };

function revokeLabUrl() {
  if (labState.objectUrl) {
    URL.revokeObjectURL(labState.objectUrl);
    labState.objectUrl = null;
  }
}

async function setLabFile(file) {
  if (!isImageFile(file)) return;
  const token = ++labState.token;
  revokeLabUrl();
  labState.file = file;
  labState.normalizedBlob = null;
  els.labRunBtn.disabled = true;
  els.labResult.innerHTML = '<div class="loading"><span class="spinner"></span>转码预览中…</div>';
  try {
    const { blob, url } = await normalizeImageFile(file);
    if (token !== labState.token) {
      URL.revokeObjectURL(url);
      return;
    }
    labState.normalizedBlob = blob;
    labState.objectUrl = url;
    els.labPreviewStage.hidden = false;
    els.labPreview.src = url;
    els.labPreview.onload = () => drawOverlay(els.labPreview, els.labOverlay, []);
    els.labResult.innerHTML = '<p class="placeholder">已转码，可单帧识别</p>';
    els.labRunBtn.disabled = false;
  } catch (err) {
    if (token !== labState.token) return;
    els.labResult.innerHTML = `<div class="error-box">${err.message}</div>`;
  }
}

async function runLabOcr() {
  if (!labState.normalizedBlob) return;
  els.labRunBtn.disabled = true;
  els.labResult.innerHTML = '<div class="loading"><span class="spinner"></span>识别中…</div>';
  try {
    const form = new FormData();
    form.append("file", labState.normalizedBlob, "frame.jpg");
    const res = await fetch("/api/v1/ocr/page", {
      method: "POST",
      headers: { "X-Already-Normalized": "1" },
      body: form,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail ?? JSON.stringify(data));
    els.labResult.innerHTML = renderPage(data.parts ?? []);
    drawOverlay(els.labPreview, els.labOverlay, overlayFromParts(data.parts ?? []));
  } catch (err) {
    els.labResult.innerHTML = `<div class="error-box">${err.message}</div>`;
  } finally {
    els.labRunBtn.disabled = !labState.normalizedBlob;
  }
}

if (els.labDropzone) {
  els.labDropzone.addEventListener("click", () => els.labFileInput.click());
  els.labFileInput.addEventListener("change", () => {
    const f = els.labFileInput.files?.[0];
    if (f) setLabFile(f);
  });
  els.labRunBtn.addEventListener("click", runLabOcr);
}

createCompanionUI(els).init();
pollHealth();
setInterval(pollHealth, 15000);
