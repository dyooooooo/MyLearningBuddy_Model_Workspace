// MinerU/corpus page debug drawer — shows Cloud originals + IR + T1.
// Large book-level JSON is page-sliced / truncated server-side; UI also caps
// stringify so opening Cloud layout/middle never freezes the page.

import { corpus } from "../../api/corpus";
import { el } from "../../util/dom";

/** Per-page + job-level Cloud artifacts shown in Debug Pipeline. */
const PANELS: { id: string; label: string }[] = [
  { id: "page_image", label: "输入页图" },
  { id: "mineru_page", label: "① MinerU Cloud 本页 JSON" },
  { id: "cloud_content_list_v2", label: "①b Cloud content_list_v2（本页）" },
  { id: "cloud_content_list", label: "①c Cloud content_list（本页）" },
  { id: "cloud_layout", label: "①d Cloud layout/middle（本页）" },
  { id: "blocks", label: "② DocumentBlock IR" },
  { id: "html", label: "③ 编译 HTML" },
  { id: "meta", label: "④ T1 校验疑点" },
];

/** Hard cap for pretty-printed JSON in the DOM (chars). */
const UI_JSON_MAX_CHARS = 120_000;

export interface CorpusPageDebugOptions {
  /** Open the drawer immediately and load index (dialog use). */
  autoOpen?: boolean;
  /** When set, open IR panel and scroll/highlight this block id. */
  focusBlockId?: string;
  /** Hide the outer summary chrome (dialog already has a title). */
  embedded?: boolean;
  summaryLabel?: string;
}

function formatBytes(n: number): string {
  if (!n || n < 0) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(1)} MB`;
}

function renderJsonSafe(
  body: HTMLElement,
  value: unknown,
  prefix = "",
  focusBlockId = "",
): void {
  body.textContent = "格式化中…";
  window.setTimeout(() => {
    try {
      let text = prefix + JSON.stringify(value, null, 2);
      if (text.length > UI_JSON_MAX_CHARS) {
        text =
          `/* UI truncated: ${text.length} chars → showing first ${UI_JSON_MAX_CHARS} */\n` +
          text.slice(0, UI_JSON_MAX_CHARS) +
          "\n/* … truncated … */\n";
      }
      body.textContent = text;
      body.dataset.formatted = "1";
      if (focusBlockId) {
        highlightBlockInPre(body, focusBlockId);
      }
    } catch (e) {
      body.textContent = `无法展示 JSON：${(e as Error).message}`;
      body.dataset.formatted = "1";
    }
  }, 0);
}

/** Scroll a <pre> of pretty JSON to the line containing the block id. */
function highlightBlockInPre(body: HTMLElement, blockId: string): void {
  const text = body.textContent || "";
  if (!text || !blockId) return;
  const needle = `"id": "${blockId}"`;
  const alt = `"id":"${blockId}"`;
  let idx = text.indexOf(needle);
  if (idx < 0) idx = text.indexOf(alt);
  if (idx < 0) idx = text.indexOf(blockId);
  if (idx < 0) {
    const tip = el(
      "div",
      "muted corpus-debug-focus-miss",
      `未在本产物中找到块 ${blockId}（可展开其他阶段查看）`,
    );
    body.parentElement?.insertBefore(tip, body);
    return;
  }
  const escaped = blockId.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  body.textContent = text.replace(
    new RegExp(`("id"\\s*:\\s*"${escaped}")`),
    ">>> $1 <<<",
  );
  const line = text.slice(0, idx).split("\n").length;
  const lineHeight = parseFloat(getComputedStyle(body).lineHeight) || 18;
  body.scrollTop = Math.max(0, (line - 4) * lineHeight);
}

export function mountCorpusPageDebugDrawer(
  container: HTMLElement,
  jobId: string,
  pageNo: number,
  opts: CorpusPageDebugOptions = {},
): void {
  const root = document.createElement("details");
  root.className = "corpus-page-debug-drawer";
  if (opts.embedded) root.classList.add("corpus-page-debug-drawer--embedded");
  const summary = document.createElement("summary");
  summary.textContent = opts.summaryLabel || "调试输出（MinerU PipeDebug）";
  root.appendChild(summary);

  const inner = el("div", "corpus-page-debug-inner");
  const summaryBar = el("div", "muted", "展开后加载…");
  const stagesHost = el("div", "corpus-debug-stages");
  inner.append(summaryBar, stagesHost);
  root.appendChild(inner);
  container.appendChild(root);

  let loaded = false;
  const focusBlockId = (opts.focusBlockId || "").trim();

  async function openPanel(panelId: string): Promise<HTMLElement | null> {
    const details = stagesHost.querySelector<HTMLElement>(
      `.corpus-debug-stage-panel[data-artifact="${CSS.escape(panelId)}"]`,
    );
    if (!details) return null;
    details.setAttribute("open", "");
    const body = details.querySelector<HTMLElement>(".corpus-debug-pre");
    // Trigger load via toggle handler if needed
    if (body && !body.dataset.loaded) {
      details.dispatchEvent(new Event("toggle"));
      // Wait briefly for async load
      for (let i = 0; i < 60; i++) {
        if (
          body.dataset.formatted === "1" ||
          (body.textContent &&
            body.textContent !== "加载中…" &&
            body.textContent !== "格式化中…" &&
            !body.textContent.startsWith("加载中"))
        ) {
          break;
        }
        await new Promise((r) => window.setTimeout(r, 50));
      }
    }
    details.scrollIntoView({ block: "nearest" });
    return body;
  }

  root.addEventListener("toggle", () => {
    if (!root.open || loaded) return;
    loaded = true;
    void loadIndex();
  });

  async function loadIndex(): Promise<void> {
    summaryBar.textContent = "加载中…";
    try {
      const index = await corpus.pageDebugIndex(jobId, pageNo);
      const findings = index.meta?.findings || [];
      const src = index.content_list_source ? ` · Cloud JSON ${index.content_list_source}` : "";
      summaryBar.textContent = findings.length
        ? `T1 疑点 ${findings.length} 条${src}`
        : `无 T1 疑点${src}`;
      if (focusBlockId) {
        summaryBar.textContent += ` · 定位 IR 块 ${focusBlockId}（Cloud JSON 无此 id）`;
      }
      stagesHost.innerHTML = "";
      for (const panel of PANELS) {
        const art = index.artifacts?.[panel.id];
        const avail = !!art?.available;
        if (
          !avail &&
          (panel.id.startsWith("cloud_") || panel.id === "page_image")
        ) {
          continue;
        }
        const details = document.createElement("details");
        details.className = "corpus-debug-stage-panel";
        details.dataset.artifact = panel.id;
        const sum = document.createElement("summary");
        const sizeHint = art?.size_bytes ? ` · ${formatBytes(Number(art.size_bytes))}` : "";
        const resolved =
          art?.resolved_as && art.resolved_as !== "layout"
            ? ` · 源 ${art.resolved_as}`
            : "";
        const fallback =
          art?.source === "cloud_raw_fallback" ? " · 从全书切片" : "";
        const sliced = art?.page_sliced ? " · 按页" : "";
        sum.textContent = `${panel.label}${avail ? `${fallback}${sliced}${resolved}${sizeHint}` : "（无）"}`;
        details.appendChild(sum);
        const body = el("pre", "corpus-debug-pre", "");
        details.appendChild(body);
        details.addEventListener("toggle", () => {
          if (!details.open || body.dataset.loaded) return;
          body.dataset.loaded = "1";
          if (!avail) {
            body.textContent =
              panel.id === "mineru_page"
                ? "本页 Cloud JSON 未落盘。请确认导入已完成且 mineru_raw_keys 存在，或点「重新渲染」补写 debug。"
                : "该产物不可用。";
            return;
          }
          body.textContent = "加载中…";
          void (async () => {
            try {
              const out = await corpus.pageDebugArtifact(jobId, pageNo, panel.id);
              if (out.bytes_b64) {
                const img = document.createElement("img");
                img.src = `data:image/jpeg;base64,${out.bytes_b64}`;
                img.style.maxWidth = "100%";
                body.replaceWith(img);
              } else if (out.text != null && out.json == null) {
                const note = out.truncated
                  ? `/* 已截断 · ${formatBytes(Number(out.size_bytes || 0))} */\n`
                  : out.minio_key
                    ? `/* MinIO: ${out.minio_key} */\n`
                    : "";
                body.textContent = note + String(out.text || "");
                body.dataset.formatted = "1";
                if (focusBlockId) highlightBlockInPre(body, focusBlockId);
              } else if (out.json != null) {
                const noteParts: string[] = [];
                if (out.source) noteParts.push(`来源: ${out.source}`);
                if (out.minio_key) noteParts.push(`MinIO: ${out.minio_key}`);
                if (out.truncated) noteParts.push("已截断");
                const note = noteParts.length
                  ? `/* ${noteParts.join(" · ")} */\n`
                  : "";
                // IR block ids live only in DocumentBlock IR — not MinerU Cloud JSON.
                const focus =
                  focusBlockId && panel.id === "blocks" ? focusBlockId : "";
                renderJsonSafe(body, out.json, note, focus);
              } else {
                body.textContent = String(out.text || "");
              }
            } catch (e) {
              body.textContent = `加载失败：${(e as Error).message}`;
            }
          })();
        });
        stagesHost.appendChild(details);
      }

      // Block mode: IR ids ≠ Cloud ids — only locate in DocumentBlock IR.
      if (focusBlockId) {
        await openPanel("blocks");
      } else if (opts.autoOpen) {
        await openPanel("mineru_page");
        await openPanel("blocks");
      }
    } catch (e) {
      summaryBar.textContent = `加载失败：${(e as Error).message}`;
    }
  }

  if (opts.autoOpen) {
    root.open = true;
    loaded = true;
    void loadIndex();
  }
}
