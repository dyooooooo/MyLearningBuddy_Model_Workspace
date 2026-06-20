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

export function formatDurationMs(ms) {
  if (ms == null || Number.isNaN(ms)) return "—";
  if (ms < 1000) return `${ms}ms`;
  const sec = Math.round(ms / 1000);
  if (sec < 60) return `${sec}秒`;
  const min = Math.floor(sec / 60);
  const rem = sec % 60;
  return rem ? `${min}分${rem}秒` : `${min}分钟`;
}

export function renderQuestionCard(q, opts = {}) {
  const {
    timing = null,
    mode = "homework",
    draftWrong = false,
    frameSelectorHtml = "",
  } = opts;

  const ordinal = q.ordinal ?? q.id ?? "?";
  const printText = q.print_text ?? pickPrint(q);
  const hwText = q.handwriting ?? "";
  const tags = (q.kp_tags ?? [])
    .map(
      (t) =>
        `<span class="kp-badge" title="score ${t.score ?? ""}">${escapeHtml(t.label || t.kp_id)}</span>`,
    )
    .join("");
  const unmapped = mode !== "draft" && !(q.kp_tags ?? []).length
    ? '<span class="kp-badge unmapped">未映射知识点</span>'
    : "";

  const isWrong = mode === "draft" ? draftWrong : q.is_wrong === true;
  const wrongCls = isWrong ? " is-wrong" : "";
  const wrongBadge = isWrong ? '<span class="badge wrong-badge">错题</span>' : "";

  const dwellMs = q.dwell_ms ?? timing?.duration_ms;
  const timingBadge =
    timing && !dwellMs
      ? `<span class="question-timing" title="首答 ${timing.first_answer_at?.slice(11, 19) ?? ""}">答题 ${formatDurationMs(timing.duration_ms)}</span>`
      : dwellMs != null
        ? `<span class="question-timing">用时 ${formatDurationMs(dwellMs)}</span>`
        : "";

  const evalConf =
    q.eval_confidence != null
      ? `<span class="eval-badge" title="评测置信度">评测 ${Math.round(Number(q.eval_confidence) * 100)}%</span>`
      : "";

  let evalDetails = "";
  const evalObj =
    typeof q.eval_json === "string"
      ? (() => {
          try {
            return JSON.parse(q.eval_json);
          } catch {
            return null;
          }
        })()
      : q.eval_json;
  if (evalObj && typeof evalObj === "object") {
    const bits = [];
    if (evalObj.error_type) bits.push(`错因: ${evalObj.error_type}`);
    if (evalObj.match_notes) bits.push(String(evalObj.match_notes));
    if (evalObj.reference_answer) bits.push(`参考: ${String(evalObj.reference_answer).slice(0, 80)}`);
    if (bits.length) {
      evalDetails = `<details class="q-details"><summary>评测详情</summary><p>${escapeHtml(bits.join(" · "))}</p></details>`;
    }
  }

  const ragItems = q.rag_evidence ?? [];
  const ragDetails = ragItems.length
    ? `<details class="q-details"><summary>RAG 证据 (${ragItems.length})</summary><ul class="rag-evidence-list">${ragItems
        .map(
          (ev) =>
            `<li><code>${escapeHtml(ev.kp_id || "")}</code> ${Math.round((ev.score ?? 0) * 100)}% · ${escapeHtml((ev.text || "").slice(0, 120))}</li>`,
        )
        .join("")}</ul></details>`
    : "";

  const qidAttr = q.id ? ` data-qid="${escapeHtml(q.id)}"` : "";
  const ordinalAttr = mode === "draft" ? ` data-ordinal="${escapeHtml(String(ordinal))}"` : "";

  let footer = "";
  if (mode === "homework" && q.id) {
    footer = `<footer class="hw-q-actions">
      <button type="button" class="btn ghost sm btn-mark-wrong" data-qid="${escapeHtml(q.id)}" data-wrong="${q.is_wrong ? "0" : "1"}">${q.is_wrong ? "取消错题" : "标为错题"}</button>
    </footer>`;
  } else if (mode === "draft") {
    footer = `<footer class="hw-q-actions">
      <button type="button" class="btn ghost sm btn-mark-wrong-draft" data-ordinal="${escapeHtml(String(ordinal))}" data-wrong="${isWrong ? "0" : "1"}">${isWrong ? "取消错题" : "标为错题"}</button>
    </footer>`;
  }

  return `<article class="question-card homework-q${wrongCls}"${qidAttr}${ordinalAttr} data-ordinal="${escapeHtml(String(ordinal))}">
    <header class="question-head">
      第 ${escapeHtml(String(ordinal))} 题 ${wrongBadge} ${timingBadge} ${evalConf}
      ${frameSelectorHtml}
      <span class="kp-tags">${tags}${unmapped}</span>
    </header>
    <div class="question-print">
      <span class="layer-tag print">印刷题干</span>
      <div class="layer-text">${escapeHtml(printText || "（无）")}</div>
    </div>
    <div class="question-handwriting">
      <span class="layer-tag handwriting">手写作答</span>
      <div class="layer-text">${escapeHtml(hwText || "（无）")}</div>
    </div>
    ${ragDetails}
    ${evalDetails}
    ${footer}
  </article>`;
}

export function renderPartCard(part, timing) {
  const kind = part.kind ?? "question";
  const printText = pickPrint(part);

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

  return renderQuestionCard(
    {
      id: part.id,
      ordinal: part.id,
      print_text: printText,
      handwriting: part.handwriting ?? "",
      bbox_print: part.bbox_print,
      bbox_handwriting: part.bbox_handwriting,
    },
    { timing, mode: "draft", draftWrong: false },
  );
}

export function renderPage(parts, questionTimings, draftWrongMap = {}) {
  if (!parts?.length) {
    return '<p class="placeholder">未识别到内容</p>';
  }
  const byQ = {};
  for (const t of questionTimings ?? []) {
    byQ[t.question_id] = t;
  }
  return `<div class="page-questions">${parts
    .map((p) => {
      if (p.kind === "header" || p.kind === "section") {
        return renderPartCard(p, null);
      }
      if (p.kind === "question" && p.id) {
        return renderQuestionCard(
          {
            id: p.id,
            ordinal: p.id,
            print_text: pickPrint(p),
            handwriting: p.handwriting ?? "",
          },
          {
            timing: byQ[p.id] ?? null,
            mode: "draft",
            draftWrong: !!draftWrongMap[p.id],
          },
        );
      }
      return renderPartCard(p, p.kind === "question" && p.id ? byQ[p.id] : null);
    })
    .join("")}</div>`;
}

/** Clamp and validate bbox in 0–1 space; returns null if unusable. */
export function sanitizeBbox(bbox) {
  if (!Array.isArray(bbox) || bbox.length < 4) return null;
  let [x1, y1, x2, y2] = bbox.map(Number);
  if ([x1, y1, x2, y2].some((v) => Number.isNaN(v))) return null;
  if (maxAbs(x1, y1, x2, y2) > 1.0) return null;
  const lo = Math.max(0, Math.min(x1, x2));
  const hi = Math.min(1, Math.max(x1, x2));
  const loY = Math.max(0, Math.min(y1, y2));
  const hiY = Math.min(1, Math.max(y1, y2));
  if (hi - lo < 0.002 || hiY - loY < 0.002) return null;
  return [lo, loY, hi, hiY];
}

function maxAbs(...vals) {
  return Math.max(...vals.map((v) => Math.abs(v)));
}

export function overlayFromParts(parts, opts = {}) {
  const { print = true, handwriting = true } = opts;
  const items = [];
  for (const p of parts ?? []) {
    const kind = p.kind ?? "question";
    const labelBase = kind === "question" ? `Q${p.id ?? "?"}` : PART_LABELS[kind] ?? kind;
    if (print && p.bbox_print?.length >= 4) {
      const bbox = sanitizeBbox(p.bbox_print);
      if (bbox) {
        items.push({
          kind: kind === "section" ? "section" : "print",
          label: `${labelBase}印`,
          bbox,
        });
      }
    }
    if (handwriting && kind === "question" && p.bbox_handwriting?.length >= 4) {
      const bbox = sanitizeBbox(p.bbox_handwriting);
      if (bbox) {
        items.push({ kind: "handwriting", label: `${labelBase}写`, bbox });
      }
    }
  }
  return items;
}

export function normBbox(bbox, canvasW, canvasH) {
  const clean = sanitizeBbox(bbox);
  if (!clean) return null;
  const [x1, y1, x2, y2] = clean;
  return [x1 * canvasW, y1 * canvasH, x2 * canvasW, y2 * canvasH];
}

export function layoutOverlay(previewImg, overlay) {
  if (!previewImg.naturalWidth || !previewImg.clientWidth) return false;
  const w = previewImg.clientWidth;
  const h = previewImg.clientHeight;
  overlay.width = w;
  overlay.height = h;
  overlay.style.width = `${w}px`;
  overlay.style.height = `${h}px`;
  overlay.hidden = false;
  return true;
}

export function clearOverlay(overlay) {
  if (!overlay) return;
  overlay.hidden = true;
  const ctx = overlay.getContext("2d");
  if (ctx) ctx.clearRect(0, 0, overlay.width, overlay.height);
}

export function drawOverlay(previewImg, overlay, items) {
  if (!items?.length) {
    clearOverlay(overlay);
    return;
  }
  if (!previewImg.naturalWidth || !previewImg.clientWidth) return;
  if (!layoutOverlay(previewImg, overlay)) return;
  const ctx = overlay.getContext("2d");
  ctx.clearRect(0, 0, overlay.width, overlay.height);

  items.forEach((item) => {
    const rect = normBbox(item.bbox, overlay.width, overlay.height);
    if (!rect) return;
    const [x1, y1, x2, y2] = rect;
    const palette = COLORS[item.kind] ?? COLORS.print;
    ctx.fillStyle = palette.fill;
    ctx.strokeStyle = palette.stroke;
    ctx.lineWidth = 2;
    ctx.fillRect(x1, y1, x2 - x1, y2 - y1);
    ctx.strokeRect(x1, y1, x2 - x1, y2 - y1);
    const label = item.label ?? "";
    if (label && y1 > 18) {
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

export const ACTION_LABELS_SHORT = {
  init: "首帧",
  merge: "合并",
  skip: "跳过",
  new_page: "换页",
};

const MASTERY_LABELS = {
  unknown: "未知",
  learning: "学习中",
  mastered: "已掌握",
};

export function renderHomeworkQuestions(questions, opts = {}) {
  if (!questions?.length) {
    return '<p class="placeholder">无题目实体</p>';
  }
  const { frameSelectorFor = null } = opts;
  return `<div class="page-questions">${questions
    .map((q) => {
      const frameSelectorHtml =
        typeof frameSelectorFor === "function" ? frameSelectorFor(q) : "";
      return renderQuestionCard(q, { mode: "homework", frameSelectorHtml });
    })
    .join("")}</div>`;
}

export function renderMasteryPanel(records, opts = {}) {
  const { demo = false, tierInfo = null } = opts;
  if (!records?.length) {
    return `<div class="mastery-empty placeholder">
      <p>暂无 A⁺ 掌握度记录</p>
      <small>定稿作业后，考纲/题库 RAG 会映射知识点并聚合掌握度</small>
    </div>`;
  }

  const counts = { unknown: 0, learning: 0, mastered: 0 };
  for (const r of records) counts[r.mastery] = (counts[r.mastery] ?? 0) + 1;

  let tierBlock = "";
  if (tierInfo) {
    const feats = (tierInfo.features ?? []).slice(0, 8).join(", ");
    tierBlock = `<p class="tier-hint">档位 <strong>${escapeHtml(tierInfo.tier || "trial")}</strong>${feats ? ` · 功能: ${escapeHtml(feats)}` : ""}</p>`;
    if (!(tierInfo.features ?? []).includes("eval")) {
      tierBlock += '<p class="tier-warn muted">当前档位不含 eval Agent；评测字段可能为空</p>';
    }
  }

  const intro = `<div class="mastery-intro">
    <p><strong>A⁺ 掌握视图</strong>（learning-buddy-api）：定稿后通过<strong>考纲 + 题库 RAG</strong> 映射知识点，再据 A/B 事件聚合掌握度。</p>
    <div class="mastery-stats">
      <span class="stat unknown">未知 ${counts.unknown ?? 0}</span>
      <span class="stat learning">学习中 ${counts.learning ?? 0}</span>
      <span class="stat mastered">已掌握 ${counts.mastered ?? 0}</span>
    </div>
    ${tierBlock}
    ${demo ? '<p class="mastery-demo-badge">demo: true</p>' : '<p class="mastery-demo-badge">Buddy API · demo: false</p>'}
  </div>`;

  const cards = records
    .map((r) => {
      const weak = r.weak ? '<span class="weak-badge">薄弱</span>' : "";
      const fluency = Number(r.fluency ?? 0);
      const ratio = Number(r.fluency_ratio ?? 0);
      const wrongRate = Number(r.wrong_rate ?? 0);
      const ratioCls = ratio > 1 ? " fluency-warn" : "";
      const barPct = Math.min(100, Math.round(Math.max(0, fluency) * 100));
      const fluencyBar = `<div class="fluency-bar${ratioCls}" title="fluency ${fluency.toFixed(2)}"><div class="fluency-fill" style="width:${barPct}%"></div></div>`;

      const evAll = r.evidence ?? [];
      const evPreview = evAll
        .slice(0, 5)
        .map(
          (ev) =>
            `<li>题 ${escapeHtml((ev.question_id || "").split("__q__").pop() || "?")} · 作业 ${escapeHtml((ev.homework_id || "").slice(0, 8))}… · ${escapeHtml(ev.note || "")}</li>`,
        )
        .join("");
      const evMore =
        evAll.length > 5
          ? `<details class="mastery-evidence-more"><summary>全部证据 (${evAll.length})</summary><ul>${evAll
              .map(
                (ev) =>
                  `<li>题 ${escapeHtml((ev.question_id || "").split("__q__").pop() || "?")} · ${escapeHtml(ev.note || "")}</li>`,
              )
              .join("")}</ul></details>`
          : "";

      const optsHtml = ["unknown", "learning", "mastered"]
        .map(
          (m) =>
            `<option value="${m}"${r.mastery === m ? " selected" : ""}>${MASTERY_LABELS[m] ?? m}</option>`,
        )
        .join("");
      return `<article class="mastery-record-card" data-kp="${escapeHtml(r.kp_id)}">
        <header class="mastery-record-head">
          <span class="mastery-qid">${escapeHtml(r.label || r.kp_id)} ${weak}</span>
          <select class="mastery-select" data-kp="${escapeHtml(r.kp_id)}" aria-label="掌握度">
            ${optsHtml}
          </select>
        </header>
        <p class="mastery-kp-id">${escapeHtml(r.kp_id)}</p>
        <div class="mastery-fluency-row">
          ${fluencyBar}
          <span class="fluency-meta">熟练度 ${fluency.toFixed(2)} · 比率 ${ratio.toFixed(2)} · 错题率 ${(wrongRate * 100).toFixed(0)}%</span>
        </div>
        ${evPreview ? `<ul class="mastery-evidence">${evPreview}</ul>` : ""}
        ${evMore}
        <footer class="mastery-meta">置信 ${Math.round((r.confidence ?? 0) * 100)}% · 更新 ${escapeHtml(r.updated_at?.slice(0, 19) ?? "—")}</footer>
      </article>`;
    })
    .join("");

  return `${intro}<div class="mastery-records">${cards}</div>`;
}
