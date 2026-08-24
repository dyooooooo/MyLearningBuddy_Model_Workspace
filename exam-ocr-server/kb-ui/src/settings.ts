import { kb, type KbMineruOut } from "./api";
import { el } from "./util/dom";

function field(label: string, control: HTMLElement): HTMLElement {
  const wrap = el("label", "kb-field");
  wrap.append(el("span", "kb-field-label", label), control);
  return wrap;
}

export function mountKbSettings(host: HTMLElement): void {
  const box = el("section", "kb-settings");
  box.appendChild(el("p", "muted", "只改知识库这一份，和语料台互不影响。推荐 vlm + 中文 + 公式 + 表。"));

  const token = document.createElement("input");
  token.type = "password";
  token.placeholder = "在 mineru.net API 管理页创建";
  token.autocomplete = "off";
  const base = document.createElement("input");
  base.placeholder = "https://mineru.net";
  const model = document.createElement("select");
  for (const [v, text] of [
    ["vlm", "vlm（推荐）"],
    ["pipeline", "pipeline"],
  ] as const) {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = text;
    model.appendChild(o);
  }
  const language = document.createElement("select");
  for (const [v, text] of [
    ["ch", "中英文"],
    ["ch_server", "繁体 / 手写"],
    ["en", "英文"],
  ] as const) {
    const o = document.createElement("option");
    o.value = v;
    o.textContent = text;
    language.appendChild(o);
  }
  const timeout = document.createElement("input");
  timeout.type = "number";
  timeout.min = "60";
  timeout.max = "3600";
  timeout.step = "60";

  const formula = document.createElement("input");
  formula.type = "checkbox";
  const table = document.createElement("input");
  table.type = "checkbox";
  const ocr = document.createElement("input");
  ocr.type = "checkbox";
  const formulaRow = el("label", "kb-check");
  formulaRow.append(formula, document.createTextNode(" 公式识别"));
  const tableRow = el("label", "kb-check");
  tableRow.append(table, document.createTextNode(" 表格识别"));
  const ocrRow = el("label", "kb-check");
  ocrRow.append(ocr, document.createTextNode(" 扫描版 OCR"));
  const checks = el("div", "kb-check-row");
  checks.append(formulaRow, tableRow, ocrRow);

  const grid = el("div", "kb-import-grid");
  grid.append(
    field("API Token", token),
    field("接口地址", base),
    field("识别引擎", model),
    field("语言", language),
    field("超时（秒）", timeout),
  );

  const saveBtn = el("button", "btn", "保存设置") as HTMLButtonElement;
  const presetBtn = el("button", "btn btn-secondary", "应用教材推荐") as HTMLButtonElement;
  const testBtn = el("button", "btn btn-secondary", "测试连通") as HTMLButtonElement;
  saveBtn.type = "button";
  presetBtn.type = "button";
  testBtn.type = "button";
  const status = el("p", "muted kb-msg", "");
  const actions = el("div", "kb-import-actions");
  actions.append(saveBtn, presetBtn, testBtn);

  box.append(grid, checks, actions, status);
  host.appendChild(box);

  function apply(out: KbMineruOut): void {
    token.value = out.api_token || "";
    base.value = out.settings.api_base_url;
    model.value = out.settings.model_version;
    language.value = out.settings.language;
    timeout.value = String(out.settings.timeout_s);
    formula.checked = out.settings.enable_formula;
    table.checked = out.settings.enable_table;
    ocr.checked = out.settings.is_ocr;
    status.textContent = out.api_token_set ? "Token 已保存" : "尚未配置 Token";
  }

  function payload(extra: Record<string, unknown> = {}): Record<string, unknown> {
    return {
      api_token: token.value.trim() || undefined,
      api_base_url: base.value.trim() || undefined,
      model_version: model.value,
      language: language.value,
      enable_formula: formula.checked,
      enable_table: table.checked,
      is_ocr: ocr.checked,
      timeout_s: Number(timeout.value || 600),
      ...extra,
    };
  }

  saveBtn.onclick = async () => {
    status.textContent = "保存中…";
    try {
      apply(await kb.mineruSave(payload()));
      status.textContent = "已保存";
    } catch (e) {
      status.textContent = (e as Error).message;
    }
  };
  presetBtn.onclick = async () => {
    status.textContent = "写入推荐档…";
    try {
      apply(
        await kb.mineruSave({
          apply_textbook_preset: true,
          api_token: token.value.trim() || undefined,
        }),
      );
      status.textContent = "已应用教材推荐（vlm + 公式 + 表）";
    } catch (e) {
      status.textContent = (e as Error).message;
    }
  };
  testBtn.onclick = async () => {
    status.textContent = "测试中…";
    try {
      status.textContent = (await kb.mineruTest(payload())).message;
    } catch (e) {
      status.textContent = (e as Error).message;
    }
  };

  void kb.mineruGet().then(apply).catch((e) => {
    status.textContent = (e as Error).message;
  });
}
