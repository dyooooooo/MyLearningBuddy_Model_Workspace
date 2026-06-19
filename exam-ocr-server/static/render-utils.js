export const COLORS = {
  handwriting: { stroke: "#e07a24", fill: "rgba(224, 122, 36, 0.18)" },
  print: { stroke: "#2b7de9", fill: "rgba(43, 125, 233, 0.15)" },
  section: { stroke: "#6b4ce6", fill: "rgba(107, 76, 230, 0.12)" },
};

const PART_LABELS = {
  header: "页眉 / 标题",
  section: "大题",
  question: "题目",
};

export function escapeHtml(str) {
  return String(str)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pickPrint(p) {
  return p.print ?? p.print_text ?? "";
}

export function renderPartCard(part) {
  const kind = part.kind ?? "question";
  const printText = pickPrint(part);
  const hwText = part.handwriting ?? "";

  if (kind === "header") {
    return `
      <article class="question-card header-card">
        <header class="question-head">${escapeHtml(PART_LABELS.header)}</header>
        <div class="question-print">
          <div class="layer-text">${escapeHtml(printText || "（无）")}</div>
        </div>
      </article>`;
  }

  if (kind === "section") {
    return `
      <article class="question-card section-card">
        <header class="question-head section-head">${escapeHtml(printText || "（无大题标题）")}</header>
      </article>`;
  }

  const id = part.id ?? "?";
  return `
    <article class="question-card">
      <header class="question-head">第 ${escapeHtml(id)} 题</header>
      <div class="question-print">
        <span class="layer-tag print">印刷</span>
        <div class="layer-text">${escapeHtml(printText || "（无印刷文字）")}</div>
      </div>
      <div class="question-handwriting">
        <span class="layer-tag handwriting">手写</span>
        <div class="layer-text">${escapeHtml(hwText || "（无手写内容）")}</div>
      </div>
    </article>`;
}

export function renderPage(parts) {
  if (!parts?.length) {
    return '<p class="placeholder">未识别到内容</p>';
  }
  return `<div class="page-questions">${parts.map(renderPartCard).join("")}</div>`;
}

export function overlayFromParts(parts) {
  const items = [];
  for (const p of parts ?? []) {
    const kind = p.kind ?? "question";
    const labelBase = kind === "question" ? `Q${p.id ?? "?"}` : PART_LABELS[kind] ?? kind;
    if (p.bbox_print?.length >= 4) {
      items.push({ kind: kind === "section" ? "section" : "print", label: `${labelBase}印`, bbox: p.bbox_print });
    }
    if (kind === "question" && p.bbox_handwriting?.length >= 4) {
      items.push({ kind: "handwriting", label: `${labelBase}写`, bbox: p.bbox_handwriting });
    }
  }
  return items;
}

export function normBbox(bbox, canvasW, canvasH, img) {
  if (!Array.isArray(bbox) || bbox.length < 4) return null;
  const [x1, y1, x2, y2] = bbox.map(Number);
  const nw = img?.naturalWidth || canvasW;
  const nh = img?.naturalHeight || canvasH;
  if ([x1, y1, x2, y2].every((v) => v >= 0 && v <= 1)) {
    return [x1 * canvasW, y1 * canvasH, x2 * canvasW, y2 * canvasH];
  }
  return [(x1 / nw) * canvasW, (y1 / nh) * canvasH, (x2 / nw) * canvasW, (y2 / nh) * canvasH];
}

export function layoutOverlay(previewImg, overlay) {
  if (!previewImg.naturalWidth || !previewImg.clientWidth) return false;
  overlay.width = previewImg.clientWidth;
  overlay.height = previewImg.clientHeight;
  overlay.hidden = false;
  return true;
}

export function drawOverlay(previewImg, overlay, items) {
  if (!previewImg.naturalWidth || !previewImg.clientWidth) return;
  if (!layoutOverlay(previewImg, overlay)) return;
  const ctx = overlay.getContext("2d");
  ctx.clearRect(0, 0, overlay.width, overlay.height);

  items.forEach((item) => {
    const rect = normBbox(item.bbox, overlay.width, overlay.height, previewImg);
    if (!rect) return;
    const [x1, y1, x2, y2] = rect;
    const palette = COLORS[item.kind] ?? COLORS.print;
    ctx.fillStyle = palette.fill;
    ctx.strokeStyle = palette.stroke;
    ctx.lineWidth = 2;
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    const label = item.label ?? "";
    if (label) {
      ctx.font = "bold 11px sans-serif";
      const tw = ctx.measureText(label).width;
      ctx.fillStyle = palette.stroke;
      ctx.fillRect(x1, y1 - 16, tw + 8, 16);
      ctx.fillStyle = "#fff";
      ctx.fillText(label, x1 + 4, y1 - 4);
    }
  });
}

export const ACTION_LABELS = {
  init: "首帧",
  merge: "合并",
  skip: "跳过",
  new_page: "换页",
};
