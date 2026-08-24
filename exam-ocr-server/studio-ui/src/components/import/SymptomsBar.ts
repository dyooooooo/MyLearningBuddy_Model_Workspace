// 导入页的命名症状两栏：已知症状（有成功路径）/ 未知疑点（需诊断）。
//
// 与「低置信 N 页」并列显示，不合并：低置信是数值兜底，命名症状是有名字、
// 有处置办法的问题。不开 AI 也能看见——检测和映射都在 worker 里做完了。

import {
  corpus,
  type CheckLoadError,
  type JobSymptoms,
  type SymptomFinding,
} from "../../api/corpus";
import { el } from "../../util/dom";

const SEVERITY_LABEL: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

const KIND_LABEL: Record<string, string> = {
  rule: "可自动修（rule）",
  playbook: "按手册修（playbook）",
};

export interface SymptomsBarHandle {
  el: HTMLElement;
  /** 载入某次导入的症状；jobId 为空则清空隐藏。 */
  load(jobId: string): Promise<void>;
  clear(): void;
}

function findingKey(f: SymptomFinding): string {
  return `${f.suspect_type}|${f.page_no || 0}|${f.block_id || ""}`;
}

function describe(f: SymptomFinding): string {
  const bits = [`第 ${f.page_no || "?"} 页`];
  if (f.block_id) bits.push(f.block_id);
  if (f.severity) bits.push(`严重度 ${SEVERITY_LABEL[f.severity] || f.severity}`);
  if (f.match === "exact") bits.push("同指纹命中历史修正");
  if (f.false_positive_hits) bits.push(`疑似历史误报 ×${f.false_positive_hits}`);
  return bits.join(" · ");
}

/**
 * @param onFix 点「按手册修」：把这条症状的手册与位置交给上层（填进 AI 对话）。
 */
export function mountSymptomsBar(opts: {
  onFix?: (finding: SymptomFinding) => void;
  onGotoPage?: (pageNo: number) => void;
}): SymptomsBarHandle {
  const root = el("div", "import-symptoms hidden");
  let jobId = "";
  let dismissed = new Set<string>();
  let loadErrors: CheckLoadError[] = [];

  /** 没加载成功的检测器 = 这次导入压根没查这些症状，必须先说。 */
  const renderLoadErrors = (): HTMLElement | null => {
    if (!loadErrors.length) return null;
    const box = el("div", "import-symptoms-alert");
    box.appendChild(
      el(
        "p",
        "import-symptoms-alert-head",
        `有 ${loadErrors.length} 份检测器声明没能加载，这些症状本次没有检查`,
      ),
    );
    const ul = el("ul", "import-symptoms-alert-list");
    for (const e of loadErrors.slice(0, 6)) {
      const li = el("li");
      li.appendChild(el("code", "", e.file));
      li.appendChild(el("span", "muted", `：${(e.errors || []).join("；")}`));
      ul.appendChild(li);
    }
    box.appendChild(ul);
    box.appendChild(
      el(
        "p",
        "muted",
        "改 learning-buddy-api/configs/checks/ 下对应文件后即时生效，无需重建镜像。",
      ),
    );
    return box;
  };

  const renderCard = (f: SymptomFinding, known: boolean): HTMLElement => {
    const card = el("div", "import-symptom-card");
    const head = el("div", "import-symptom-head");
    head.append(
      el("span", "import-symptom-type", f.suspect_type || "（未命名）"),
      el("span", "muted import-symptom-where", describe(f)),
    );
    card.appendChild(head);

    if (known) {
      const kind = KIND_LABEL[f.remediation_kind || ""] || "处置档位未定";
      const where = f.landing_target ? ` · 生效面 ${f.landing_target}` : "";
      card.appendChild(el("p", "muted import-symptom-kind", kind + where));
      const hist = (f.candidates || []).filter((c) => c.startsWith("历史修正"));
      if (hist.length) {
        card.appendChild(el("p", "import-symptom-history", hist[0]));
      }
    } else {
      card.appendChild(
        el("p", "muted import-symptom-kind", "还没有成功路径，需要先诊断"),
      );
    }

    const actions = el("div", "import-symptom-actions");
    if (f.page_no && opts.onGotoPage) {
      const goto = el("button", "btn-link", "看这一页") as HTMLButtonElement;
      goto.type = "button";
      goto.onclick = () => opts.onGotoPage?.(Number(f.page_no));
      actions.appendChild(goto);
    }
    if (known && opts.onFix) {
      const fix = el("button", "btn-secondary", "按手册修") as HTMLButtonElement;
      fix.type = "button";
      fix.title = f.playbook || "把这条症状与历史成功做法填进 AI 对话";
      fix.onclick = () => opts.onFix?.(f);
      actions.appendChild(fix);
    }
    const not = el("button", "btn-link", "这不是问题") as HTMLButtonElement;
    not.type = "button";
    not.title = "记一条误报：同指纹下次降一档严重度并标注，但仍然显示";
    not.onclick = async () => {
      not.disabled = true;
      try {
        await corpus.reportSymptomFalsePositive({
          suspect_type: f.suspect_type,
          fingerprint: f.fingerprint || "",
          job_id: jobId,
          page_no: Number(f.page_no || 0),
          block_id: f.block_id || "",
        });
        dismissed.add(findingKey(f));
        card.classList.add("is-dismissed");
        not.textContent = "已记为误报";
      } catch (e) {
        not.disabled = false;
        window.alert(`上报误报失败：${e}`);
      }
    };
    actions.appendChild(not);
    card.appendChild(actions);
    if (dismissed.has(findingKey(f))) card.classList.add("is-dismissed");
    return card;
  };

  const renderColumn = (
    title: string,
    lead: string,
    items: SymptomFinding[],
    known: boolean,
  ): HTMLElement => {
    const col = el("div", "import-symptom-col");
    col.append(
      el("h4", "import-symptom-col-title", `${title}（${items.length}）`),
      el("p", "muted import-symptom-col-lead", lead),
    );
    if (!items.length) {
      col.appendChild(el("p", "muted", "无"));
      return col;
    }
    for (const f of items.slice(0, 30)) col.appendChild(renderCard(f, known));
    if (items.length > 30) {
      col.appendChild(el("p", "muted", `… 另有 ${items.length - 30} 条`));
    }
    return col;
  };

  const paint = (data: JobSymptoms | null): void => {
    root.replaceChildren();
    const alert = renderLoadErrors();
    const known = data?.known || [];
    const unknown = data?.unknown || [];
    if (!known.length && !unknown.length) {
      // 没症状也要把加载失败顶出来：那是「没查」，不是「没问题」
      if (alert) {
        root.appendChild(alert);
        root.classList.remove("hidden");
      } else {
        root.classList.add("hidden");
      }
      return;
    }
    if (alert) root.appendChild(alert);
    const head = el(
      "p",
      "import-symptoms-head",
      `命名症状 ${known.length + unknown.length} 处`,
    );
    const lowN = (data?.low_confidence_pages || []).length;
    if (lowN) {
      head.appendChild(
        el(
          "span",
          "muted import-symptoms-low",
          ` · 另有低置信 ${lowN} 页（数值兜底，与命名症状并列）`,
        ),
      );
    }
    head.appendChild(
      el("span", "muted import-symptoms-nav-hint", " · 点左栏页码看该页"),
    );
    const cols = el("div", "import-symptom-cols");
    cols.append(
      renderColumn(
        "已知症状",
        "别的书上修好过，直接照成功路径改",
        known,
        true,
      ),
      renderColumn("未知疑点", "还没有成功路径，改好后记账才能沉淀成模式", unknown, false),
    );
    const body = el("div", "import-symptoms-body");
    body.appendChild(cols);
    root.append(head, body);
    root.classList.remove("hidden");
  };

  return {
    el: root,
    async load(id: string): Promise<void> {
      jobId = String(id || "").trim();
      if (!jobId) {
        this.clear();
        return;
      }
      const [symptoms, overview] = await Promise.all([
        // 老任务没有症状字段：静默当空，别在导入页上堆报错
        corpus.jobSymptoms(jobId).catch(() => null),
        corpus.checksOverview().catch(() => null),
      ]);
      loadErrors = overview?.load_errors || [];
      paint(symptoms);
    },
    clear(): void {
      jobId = "";
      dismissed = new Set();
      loadErrors = [];
      root.replaceChildren();
      root.classList.add("hidden");
    },
  };
}
