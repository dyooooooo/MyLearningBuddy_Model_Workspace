import "/test/vite/math.js?v=20260622evalfinish";

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

/** MathJax segment renderer; falls back to escaped plain text. */
export function mathHtml(str, opts = {}) {
  const rich = opts.rich;
  const segments = opts.segments || rich?.segments;
  const text = str ?? rich?.raw ?? "";
  if (!text && !segments?.length) return "";
  const format = window.BuddyMath?.formatMathHtml;
  if (!format) return escapeHtml(String(text));
  try {
    return format(String(text), { ...opts, rich, segments });
  } catch {
    return escapeHtml(String(text));
  }
}

function mathRich(content, emptyLabel = "（无）") {
  if (!content) return escapeHtml(emptyLabel);
  if (typeof content === "object" && Array.isArray(content.segments)) {
    return mathHtml(content.raw || "", { rich: content });
  }
  return content ? mathHtml(String(content)) : escapeHtml(emptyLabel);
}

function pickPrint(p) {
  if (!p) return "";
  return p.print ?? p.print_text ?? "";
}

function pickHw(p) {
  if (!p) return "";
  return String(p.handwriting ?? "");
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

export function agentVerdictBadge(q, locale = "zh") {
  const src = q.wrong_source;
  const agent = q.agent_is_wrong;
  const labels =
    locale === "en"
      ? { eval_wrong: "Agent wrong", eval_ok: "Agent OK", manual: "Manual", pending: "Pending", failed: "Eval failed" }
      : { eval_wrong: "Agent 错题", eval_ok: "Agent 正确", manual: "人工改判", pending: "待评测", failed: "评测失败" };
  if (src === "manual") return `<span class="badge source-manual">${labels.manual}</span>`;
  if (q.eval_status === "failed") return `<span class="badge source-failed">${labels.failed}</span>`;
  if (agent === true) return `<span class="badge source-agent-wrong">${labels.eval_wrong}</span>`;
  if (agent === false) return `<span class="badge source-agent-ok">${labels.eval_ok}</span>`;
  return `<span class="badge source-pending">${labels.pending}</span>`;
}

export function renderQuestionCard(q, opts = {}) {
  const {
    timing = null,
    mode = "homework",
    frameSelectorHtml = "",
    locale = "zh",
    editable = false,
  } = opts;

  const ordinal = q.ordinal ?? q.id ?? "?";
  const printText = q.print_text ?? pickPrint(q);
  const hwText = q.handwriting ?? "";
  const printRich = q.print_rich;
  const hwRich = q.handwriting_rich;
  const tags = (q.kp_tags ?? [])
    .map(
      (t) =>
        `<span class="kp-badge" title="score ${t.score ?? ""}">${escapeHtml(t.label || t.kp_id)}</span>`,
    )
    .join("");
  const unmapped = mode !== "draft" && !(q.kp_tags ?? []).length
    ? '<span class="kp-badge unmapped">未映射知识点</span>'
    : "";

  const isWrong = q.is_wrong === true;
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
    if (evalObj.error_type) bits.push(`${locale === "en" ? "Error" : "错因"}: ${evalObj.error_type}`);
    if (evalObj.reference_answer) {
      bits.push(`${locale === "en" ? "Standard" : "标准答"}: ${String(evalObj.reference_answer).slice(0, 120)}`);
    }
    const sol = evalObj.solution_steps;
    if (Array.isArray(sol) && sol.length) {
      bits.push(`${locale === "en" ? "Steps" : "解题"}: ${sol.slice(0, 3).join(" → ")}`.slice(0, 200));
    }
    const rs = evalObj.reasoning_steps;
    if (Array.isArray(rs) && rs.length) {
      bits.push(`${locale === "en" ? "Reasoning" : "推理"}: ${rs.join(" ")}`.slice(0, 200));
    } else if (evalObj.match_notes) {
      bits.push(String(evalObj.match_notes));
    }
    if (bits.length) {
      evalDetails = `<details class="q-details"><summary>${locale === "en" ? "Eval detail" : "评测详情"}</summary><div class="eval-detail-body">${bits.map((b) => `<p>${mathHtml(b)}</p>`).join("")}</div></details>`;
    }
    evalDetails += renderEvalTracePanel(evalObj, locale);
  }

  const ragItems = q.rag_evidence ?? [];
  const ragDetails = ragItems.length
    ? `<details class="q-details"><summary>RAG 证据 (${ragItems.length})</summary><ul class="rag-evidence-list">${ragItems
        .map(
          (ev) =>
            `<li><code>${escapeHtml(ev.kp_id || "")}</code> ${Math.round((ev.score ?? 0) * 100)}% · ${mathHtml((ev.text || "").slice(0, 120))}</li>`,
        )
        .join("")}</ul></details>`
    : "";

  const qidAttr = q.id ? ` data-qid="${escapeHtml(q.id)}"` : "";
  const ordinalAttr = mode === "draft" ? ` data-ordinal="${escapeHtml(String(ordinal))}"` : "";

  let footer = "";
  if (mode === "homework" && q.id && editable) {
    footer = `<footer class="hw-q-actions">
      <button type="button" class="btn ghost sm btn-edit-q" data-qid="${escapeHtml(q.id)}">${locale === "en" ? "Edit" : "编辑题干/手写"}</button>
      <button type="button" class="btn ghost sm btn-del-q" data-qid="${escapeHtml(q.id)}">${locale === "en" ? "Delete Q" : "删除题目"}</button>
      <a href="#" class="btn ghost sm btn-goto-wb" data-qid="${escapeHtml(q.id)}">${locale === "en" ? "Wrong book" : "错题本改判"}</a>
    </footer>`;
  } else if (mode === "homework" && q.id) {
    footer = `<footer class="hw-q-actions">
      <a href="#" class="btn ghost sm btn-goto-wb" data-qid="${escapeHtml(q.id)}">${locale === "en" ? "Wrong book" : "错题本改判"}</a>
    </footer>`;
  }

  return `<article class="question-card homework-q${wrongCls}"${qidAttr}${ordinalAttr} data-ordinal="${escapeHtml(String(ordinal))}">
    <header class="question-head">
      第 ${escapeHtml(String(ordinal))} 题 ${wrongBadge} ${agentVerdictBadge(q, locale)} ${timingBadge} ${evalConf}
      ${frameSelectorHtml}
      <span class="kp-tags">${tags}${unmapped}</span>
    </header>
    <div class="question-print">
      <span class="layer-tag print">印刷题干</span>
      <div class="layer-text math-block">${mathRich(printRich ?? printText)}</div>
    </div>
    <div class="question-handwriting">
      <span class="layer-tag handwriting">手写作答</span>
      <div class="layer-text math-block">${mathRich(hwRich ?? hwText)}</div>
    </div>
    ${ragDetails}
    ${evalDetails}
    ${footer}
  </article>`;
}

export function renderPartCard(part, timing) {
  const kind = part.kind ?? "question";
  const printText = pickPrint(part);
  const printRich = part.print_rich;
  const hwRich = part.handwriting_rich;

  if (kind === "header") {
    return `
      <article class="question-card header-card">
        <header class="question-head">${escapeHtml(PART_LABELS.header)}</header>
        <div class="question-print">
          <div class="layer-text math-block">${mathRich(printRich ?? printText)}</div>
        </div>
      </article>`;
  }

  if (kind === "section") {
    return `
      <article class="question-card section-card">
        <header class="question-head section-head">${mathHtml(printText || "（无大题标题）", { rich: printRich })}</header>
      </article>`;
  }

  return renderQuestionCard(
    {
      id: part.id,
      ordinal: part.id,
      print_text: printText,
      handwriting: part.handwriting ?? "",
      print_rich: printRich,
      handwriting_rich: hwRich,
      bbox_print: part.bbox_print,
      bbox_handwriting: part.bbox_handwriting,
    },
    { timing, mode: "draft" },
  );
}

export function renderPage(parts, questionTimings) {
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
            print_rich: p.print_rich,
            handwriting_rich: p.handwriting_rich,
          },
          {
            timing: byQ[p.id] ?? null,
            mode: "draft",
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

export function renderHomeworkQuestions(questions, opts = {}) {
  if (!questions?.length) {
    return '<p class="placeholder">无题目实体</p>';
  }
  const { frameSelectorFor = null, locale = "zh", editable = false } = opts;
  return `<div class="page-questions">${questions
    .map((q) => {
      const frameSelectorHtml =
        typeof frameSelectorFor === "function" ? frameSelectorFor(q) : "";
      return renderQuestionCard(q, { mode: "homework", frameSelectorHtml, locale, editable });
    })
    .join("")}</div>`;
}

const MASTERY_LABELS = {
  unknown: "未知",
  learning: "学习中",
  mastered: "已掌握",
};

const EVIDENCE_NOTE_LABELS = {
  wrong: "标为错题",
  handwriting: "有手写作答",
  seen: "仅 OCR 识别",
};

function formatEvidenceNote(ev) {
  const base = EVIDENCE_NOTE_LABELS[ev.note] || ev.note || "记录";
  const qn = (ev.question_id || "").split("__q__").pop() || "?";
  const dwell =
    ev.dwell_ms != null && ev.dwell_ms > 0 ? ` · 停留 ${formatDurationMs(ev.dwell_ms)}` : "";
  return `第 ${qn} 题 · ${base}${dwell}`;
}

export function renderMasteryPanel(records, opts = {}) {
  const { demo = false, tierInfo = null, locale = "zh" } = opts;
  const ML = locale === "en"
    ? { unknown: "Unknown", learning: "Learning", mastered: "Mastered" }
    : { unknown: "未知", learning: "学习中", mastered: "已掌握" };
  if (!records?.length) {
    return `<div class="mastery-empty placeholder">
      <p>${locale === "en" ? "No A+ mastery records" : "暂无 A⁺ 掌握度记录"}</p>
      <small>${locale === "en" ? "Finalize homework to aggregate mastery from syllabus/question-bank RAG" : "定稿作业后，考纲/题库 RAG 会映射知识点并聚合掌握度"}</small>
      <div class="mastery-toolbar">
        <button type="button" class="btn ghost sm btn-add-kp">${locale === "en" ? "+ Add KP" : "+ 添加知识点"}</button>
        <button type="button" class="btn ghost sm btn-recompute-mastery">${locale === "en" ? "Recompute" : "重新聚合"}</button>
      </div>
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

  const weakRecords = records.filter((r) => r.weak);
  const weakHint =
    weakRecords.length > 0
      ? `<p class="mastery-insight"><strong>薄弱 ${weakRecords.length} 项</strong>：${weakRecords
          .slice(0, 4)
          .map((r) => escapeHtml(r.label || r.kp_id))
          .join("、")}${weakRecords.length > 4 ? "…" : ""}</p>`
      : "";

  const intro = `<div class="mastery-intro">
    <p><strong>A⁺ ${locale === "en" ? "Mastery" : "掌握视图"}</strong>：${locale === "en" ? "Syllabus + question-bank RAG after finalize." : "定稿作业后经考纲/题库 RAG 映射知识点，结合手写与错题标记聚合。"}</p>
    <div class="mastery-toolbar">
      <button type="button" class="btn ghost sm btn-add-kp">${locale === "en" ? "+ Add KP" : "+ 添加知识点"}</button>
      <button type="button" class="btn ghost sm btn-recompute-mastery">${locale === "en" ? "Recompute" : "重新聚合"}</button>
    </div>
    <div class="mastery-stats">
      <span class="stat unknown">未知 ${counts.unknown ?? 0}</span>
      <span class="stat learning">学习中 ${counts.learning ?? 0}</span>
      <span class="stat mastered">已掌握 ${counts.mastered ?? 0}</span>
    </div>
    ${weakHint}
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
        .map((ev) => `<li>${escapeHtml(formatEvidenceNote(ev))}</li>`)
        .join("");
      const evMore =
        evAll.length > 5
          ? `<details class="mastery-evidence-more"><summary>全部证据 (${evAll.length})</summary><ul>${evAll
              .map((ev) => `<li>${escapeHtml(formatEvidenceNote(ev))}</li>`)
              .join("")}</ul></details>`
          : "";

      const optsHtml = ["unknown", "learning", "mastered"]
        .map(
          (m) =>
            `<option value="${m}"${r.mastery === m ? " selected" : ""}>${ML[m] ?? m}</option>`,
        )
        .join("");
      return `<article class="mastery-record-card" data-kp="${escapeHtml(r.kp_id)}">
        <header class="mastery-record-head">
          <span class="mastery-qid">${escapeHtml(r.label || r.kp_id)} ${weak}</span>
          <div class="mastery-head-actions">
            <select class="mastery-select" data-kp="${escapeHtml(r.kp_id)}" aria-label="掌握度">
              ${optsHtml}
            </select>
            <button type="button" class="btn ghost sm btn-del-kp" data-kp="${escapeHtml(r.kp_id)}">${locale === "en" ? "Delete" : "删除"}</button>
          </div>
        </header>
        <p class="mastery-kp-id muted">${escapeHtml(r.kp_id)}</p>
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

function _evalDetailBlock(q, locale) {
  const ev = q.eval_json && typeof q.eval_json === "object" ? q.eval_json : {};
  const solSteps = ev.solution_steps || [];
  const solRich = ev.solution_steps_rich || [];
  const reasoning = ev.reasoning_steps || [];
  const reasoningRich = ev.reasoning_steps_rich || [];
  const ref = q.reference_answer || ev.reference_answer || "";
  const refRich = ev.reference_answer_rich;
  const notes = q.match_notes || ev.match_notes || "";
  const notesRich = ev.match_notes_rich;
  const parts = [];
  if (ref) {
    parts.push(
      `<p class="wrong-ref"><strong>${locale === "en" ? "Standard answer" : "标准答案"}:</strong> ${mathHtml(String(ref), { rich: refRich })}</p>`,
    );
  }
  if (solSteps.length) {
    parts.push(
      `<details class="wrong-eval-detail" open><summary>${locale === "en" ? "Solution steps" : "解题过程"}</summary><ol>${solSteps
        .map((s, i) =>
          `<li>${mathHtml(String(s), { rich: solRich[i] })}</li>`,
        )
        .join("")}</ol></details>`,
    );
  }
  if (reasoning.length) {
    parts.push(
      `<details class="wrong-eval-detail"><summary>${locale === "en" ? "Judgment reasoning" : "判题推理"}</summary><ol>${reasoning
        .map((s, i) =>
          `<li>${mathHtml(String(s), { rich: reasoningRich[i] })}</li>`,
        )
        .join("")}</ol></details>`,
    );
  } else if (notes) {
    parts.push(`<p class="wrong-notes muted">${mathHtml(String(notes), { rich: notesRich })}</p>`);
  }
  return parts.length ? `<div class="wrong-eval-block">${parts.join("")}${renderEvalTracePanel(ev, locale)}</div>` : renderEvalTracePanel(ev, locale);
}

export function renderEvalTracePanel(evalJson, locale = "zh") {
  const trace = evalJson?.eval_trace;
  if (!trace || !Array.isArray(trace.stages) || !trace.stages.length) return "";
  const title = locale === "en" ? "Eval debug · eval_trace" : "评测调试 · eval_trace";
  const stages = trace.stages
    .map((s) => {
      const name = escapeHtml(s.stage || "?");
      const ms = s.ms != null ? `${s.ms}ms` : "";
      const think =
        s.stage === "solve_think" && s.input?.enable_thinking ? " · thinking" : "";
      const inp = s.input && Object.keys(s.input).length ? JSON.stringify(s.input, null, 2) : "";
      const out = s.output && Object.keys(s.output).length ? JSON.stringify(s.output, null, 2) : "";
      let body = "";
      if (s.stage === "solve_think" && s.output) {
        const rc = s.output.reasoning_content || "";
        const ct = s.output.content || "";
        if (rc) {
          body += `<div class="eval-trace-split"><strong>reasoning</strong><pre class="eval-trace-io">${escapeHtml(rc)}</pre></div>`;
        }
        if (ct) {
          body += `<div class="eval-trace-split"><strong>content</strong><pre class="eval-trace-io">${escapeHtml(ct)}</pre></div>`;
        }
      } else {
        if (inp) {
          body += `<details class="eval-trace-io-wrap"><summary>Input</summary><pre class="eval-trace-io">${escapeHtml(inp)}</pre></details>`;
        }
        if (out) {
          body += `<details class="eval-trace-io-wrap" open><summary>Output</summary><pre class="eval-trace-io">${escapeHtml(out)}</pre></details>`;
        }
      }
      return `<li class="eval-trace-stage"><header class="eval-trace-stage-head"><code>${name}</code> <span class="muted">${ms}${think}</span></header>${body}</li>`;
    })
    .join("");
  const meta = `<p class="muted eval-trace-meta">${escapeHtml(trace.prompt_version || "")} · ${trace.total_ms ?? "?"}ms · v${trace.trace_version ?? 1}</p>`;
  return `<details class="eval-trace-timeline"><summary>${title}</summary>${meta}<ol class="eval-trace-stages">${stages}</ol></details>`;
}

function _wrongItemHtml(q, locale, actions) {
  const kp = q.kp_tags?.[0]?.label || q.kp_tags?.[0]?.kp_id || (locale === "en" ? "Unmapped" : "未打标");
  const src = q.wrong_source === "manual"
    ? (locale === "en" ? "Manual" : "人工")
    : q.wrong_source === "eval"
      ? "Agent"
      : "";
  const hwTag = q.homework_id ? `<span class="muted hw-tag" title="${escapeHtml(q.homework_id)}">${locale === "en" ? "HW" : "作业"} ${escapeHtml(String(q.homework_id).slice(0, 8))}</span>` : "";
  return `<li class="wrong-item" data-qid="${escapeHtml(q.question_id)}" data-hwid="${escapeHtml(q.homework_id || "")}">
    <div class="wrong-item-head">
      <strong>#${escapeHtml(q.ordinal ?? "?")}</strong>
      ${hwTag}
      <span class="kp-badge">${escapeHtml(kp)}</span>
      ${src ? `<span class="badge">${escapeHtml(src)}</span>` : ""}
      ${agentVerdictBadge(q, locale)}
    </div>
    <p class="wrong-print math-block">${mathHtml((q.print_text || "").slice(0, 320), { rich: q.print_rich })}</p>
    <p class="wrong-hw muted math-block"><strong>${locale === "en" ? "Student" : "手写"}:</strong> ${mathHtml((q.handwriting || "").slice(0, 200), { rich: q.handwriting_rich })}</p>
    ${_evalDetailBlock(q, locale)}
    <footer class="wrong-actions">${actions}</footer>
  </li>`;
}

export function renderWrongBookPanel(data, locale = "zh") {
  const items = data.items ?? [];
  const candidates = data.candidates ?? [];
  const addLbl = locale === "en" ? "Add to wrong book" : "加入错题本";
  const rmLbl = locale === "en" ? "Remove" : "移出错题本";
  const restoreLbl = locale === "en" ? "Restore Agent" : "恢复 Agent 判定";

  const confirmed = items.length
    ? items.map((q) =>
        _wrongItemHtml(
          q,
          locale,
          `<button type="button" class="btn ghost sm btn-wb-remove" data-qid="${escapeHtml(q.question_id)}">${rmLbl}</button>
           <button type="button" class="btn ghost sm btn-wb-restore" data-qid="${escapeHtml(q.question_id)}">${restoreLbl}</button>`,
        ),
      ).join("")
    : `<p class="placeholder">${locale === "en" ? "Agent wrong answers appear here after eval." : "Eval 完成后 Agent 判错的题会自动出现在此"}</p>`;

  const review = candidates.length
    ? candidates.map((q) =>
        _wrongItemHtml(
          q,
          locale,
          `<button type="button" class="btn ghost sm btn-wb-add" data-qid="${escapeHtml(q.question_id)}">${addLbl}</button>
           <span class="muted">${mathHtml(q.candidate_reason || "")}</span>`,
        ),
      ).join("")
    : `<p class="placeholder muted">${locale === "en" ? "No review candidates" : "暂无待复核题目"}</p>`;

  const reEvalLbl = locale === "en" ? "Re-run Agent eval (all questions in related homework)" : "重新 Agent 评测（相关作业全部题目）";
  const reEvalAllLbl = locale === "en" ? "Re-eval all homework" : "重新评测全部作业";

  return `<div class="wrong-book-toolbar">
    <button type="button" class="btn secondary sm btn-wb-re-eval">${reEvalLbl}</button>
    <button type="button" class="btn ghost sm btn-wb-re-eval-all">${reEvalAllLbl}</button>
    <span class="muted wb-toolbar-hint">${locale === "en" ? "Uses SOLVE+JUDGE via vLLM; may take ~1 min per page" : "调用 vLLM SOLVE+JUDGE，约 1 分钟/页"}</span>
  </div>
  <div class="wrong-book-layout">
    <section class="wrong-section">
      <h3>${locale === "en" ? "Confirmed wrong" : "已确认错题"} (${items.length})</h3>
      <ul class="wrong-list">${confirmed}</ul>
    </section>
    <section class="wrong-section candidates">
      <h3>${locale === "en" ? "Review / possible misses" : "待复核 / 可能漏判"} (${candidates.length})</h3>
      <p class="muted">${locale === "en" ? "Add questions Agent missed or could not evaluate." : "以下题目 Agent 未判错或未能评测，如有错误请手动加入"}</p>
      <ul class="wrong-list">${review}</ul>
    </section>
  </div>`;
}

function partKey(p) {
  return `${p.kind || "question"}:${p.id ?? ""}`;
}

function partTitle(p) {
  const kind = PART_LABELS[p.kind] || p.kind || "段";
  if (p.kind === "question") return `${kind} ${p.id ?? "?"}`;
  return kind;
}

export function countOcrPrintChanges(visionParts, refineParts) {
  const vMap = new Map((visionParts ?? []).map((p) => [partKey(p), p]));
  const rMap = new Map((refineParts ?? []).map((p) => [partKey(p), p]));
  let changes = 0;
  for (const key of new Set([...vMap.keys(), ...rMap.keys()])) {
    const vPrint = pickPrint(vMap.get(key));
    const rPrint = pickPrint(rMap.get(key));
    if (vPrint !== rPrint) changes += 1;
  }
  return changes;
}

export function renderOcrComparePanel(visionParts, refineParts, meta = {}) {
  const vision = visionParts ?? [];
  const refined = refineParts ?? [];
  const hasVision = vision.length > 0;

  if (!hasVision && !refined.length) {
    return '<p class="placeholder">本帧尚无 OCR 结果</p>';
  }

  if (!hasVision) {
    return `<p class="placeholder">本帧无 Vision 草稿（旧会话数据）<br><small>请重新加帧后可对比 OCR 与 refine</small></p>`;
  }

  const vMap = new Map(vision.map((p) => [partKey(p), p]));
  const rMap = new Map(refined.map((p) => [partKey(p), p]));
  const keys = [...new Set([...vMap.keys(), ...rMap.keys()])];
  const printChanges = countOcrPrintChanges(vision, refined);
  const hwWarnings = keys.filter((k) => {
    const v = pickHw(vMap.get(k));
    const r = pickHw(rMap.get(k));
    return v !== r;
  }).length;

  const ocrSec = meta.ocr_ms ? (meta.ocr_ms / 1000).toFixed(1) : "—";
  const refineSec = meta.refine_ms ? (meta.refine_ms / 1000).toFixed(1) : "—";
  const delta =
    meta.n_refine_changes != null ? meta.n_refine_changes : printChanges;
  const verdict =
    printChanges === 0
      ? '<span class="compare-verdict same">印刷体无变化 · refine 未改字</span>'
      : `<span class="compare-verdict changed">印刷体 ${printChanges} 处变更 · 服务端 Δ=${delta}</span>`;

  const rows = keys
    .map((key) => {
      const vp = vMap.get(key);
      const rp = rMap.get(key) ?? vp;
      const sample = rp ?? vp;
      const vPrint = pickPrint(vp);
      const rPrint = pickPrint(rp);
      const changed = vPrint !== rPrint;
      const rowCls = changed ? " compare-row-changed" : " compare-row-same";
      const badge = changed
        ? '<span class="compare-badge changed">print 已改</span>'
        : '<span class="compare-badge same">相同</span>';
      const vHw = pickHw(vp);
      const rHw = pickHw(rp);
      const hwNote =
        vHw !== rHw
          ? `<p class="compare-hw-warn">⚠ 手写不一致（refine 应保留 vision）</p>`
          : vHw
            ? `<p class="compare-hw muted math-block">手写：${mathHtml(vHw, { rich: vp?.handwriting_rich })}</p>`
            : "";
      return `<article class="compare-row${rowCls}">
        <header class="compare-row-head">${escapeHtml(partTitle(sample))} ${badge}</header>
        <div class="compare-cols">
          <div class="compare-col vision">
            <h4>Vision OCR</h4>
            <p class="compare-print math-block">${mathHtml(vPrint || "—", { rich: vp?.print_rich })}</p>
          </div>
          <div class="compare-col refine">
            <h4>OCR-refine</h4>
            <p class="compare-print math-block">${mathHtml(rPrint || "—", { rich: rp?.print_rich })}</p>
          </div>
        </div>
        ${hwNote}
      </article>`;
    })
    .join("");

  const hwBlock =
    hwWarnings > 0
      ? `<p class="compare-hw-warn summary">共 ${hwWarnings} 段手写与 vision 不一致，应排查 refine prompt</p>`
      : "";

  return `<div class="ocr-compare">
    <header class="ocr-compare-summary">
      ${verdict}
      <span class="compare-meta">OCR ${ocrSec}s · refine ${refineSec}s</span>
      ${hwBlock}
      <p class="compare-hint muted">仅对比印刷体 print；手写应来自 Vision，refine 只校正题干。</p>
    </header>
    <div class="ocr-compare-rows">${rows}</div>
  </div>`;
}
