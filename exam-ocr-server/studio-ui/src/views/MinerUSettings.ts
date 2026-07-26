import {
  mineruSettings,
  type MinerUSettingsPublic,
  type MinerUSettingsUpdate,
} from "../api/mineruSettings";
import { store } from "../state/store";
import { el } from "../util/dom";

/** 内嵌到导入工作台中间区域（非弹窗） */
export function mountMinerUSettingsPanel(container: HTMLElement): void {
  const card = el("div", "import-upload-card mineru-settings-inline");
  card.appendChild(el("h3", "", "MinerU 精准解析 API 设置"));
  card.appendChild(
    el(
      "p",
      "muted",
      "Token 在 MinerU「API 管理」页创建（精准解析 API v4，非 Agent 轻量 API）。保存后写入服务端，无需改 .env。",
    ),
  );

  const form = el("form", "mineru-settings-form");
  const status = el("div", "import-status");
  const testResult = el("div", "mineru-test-result");

  const tokenInput = document.createElement("input");
  tokenInput.type = "password";
  tokenInput.placeholder = "粘贴 MinerU API 管理页创建的 Token";
  tokenInput.autocomplete = "off";

  const tokenHint = el("p", "muted token-hint", "尚未配置 Token");

  const baseUrl = document.createElement("input");
  baseUrl.placeholder = "https://mineru.net";

  const modelVersion = document.createElement("select");
  for (const opt of [
    { v: "vlm", label: "vlm（推荐）" },
    { v: "pipeline", label: "pipeline" },
    { v: "MinerU-HTML", label: "MinerU-HTML（仅 HTML）" },
  ]) {
    const o = document.createElement("option");
    o.value = opt.v;
    o.textContent = opt.label;
    modelVersion.appendChild(o);
  }

  const language = document.createElement("select");
  for (const opt of [
    { v: "ch", label: "ch — 中英文（默认）" },
    { v: "ch_server", label: "ch_server — 繁体/手写" },
    { v: "en", label: "en — 纯英文" },
  ]) {
    const o = document.createElement("option");
    o.value = opt.v;
    o.textContent = opt.label;
    language.appendChild(o);
  }

  const timeout = document.createElement("input");
  timeout.type = "number";
  timeout.min = "60";
  timeout.max = "3600";
  timeout.step = "60";

  const enableFormula = document.createElement("input");
  enableFormula.type = "checkbox";
  enableFormula.checked = true;

  const enableTable = document.createElement("input");
  enableTable.type = "checkbox";
  enableTable.checked = true;

  const isOcr = document.createElement("input");
  isOcr.type = "checkbox";

  const formulaRow = el("label", "checkbox-row");
  formulaRow.append(enableFormula, document.createTextNode(" 开启公式识别 enable_formula"));
  const tableRow = el("label", "checkbox-row");
  tableRow.append(enableTable, document.createTextNode(" 开启表格识别 enable_table"));
  const ocrRow = el("label", "checkbox-row");
  ocrRow.append(isOcr, document.createTextNode(" 扫描版 PDF 开启 OCR is_ocr"));

  form.append(
    el("label", "", "API Token"),
    tokenInput,
    tokenHint,
    el("label", "", "API 地址"),
    baseUrl,
    el("label", "", "模型版本 model_version"),
    modelVersion,
    el("label", "", "语言 language"),
    language,
    el("label", "", "超时（秒）"),
    timeout,
    formulaRow,
    tableRow,
    ocrRow,
  );

  function applySettings(
    s: MinerUSettingsPublic,
    tokenSet: boolean,
    token: string = "",
  ): void {
    baseUrl.value = s.api_base_url || "https://mineru.net";
    modelVersion.value = s.model_version || "vlm";
    language.value = s.language || "ch";
    timeout.value = String(s.timeout_s || 600);
    enableFormula.checked = s.enable_formula;
    enableTable.checked = s.enable_table;
    isOcr.checked = s.is_ocr;
    tokenInput.value = token;
    tokenHint.textContent = tokenSet
      ? `Token 已保存 · 更新于 ${s.updated_at || "—"}`
      : "尚未配置 Token — 导入时选 MinerU 将失败";
    tokenHint.classList.toggle("warn", !tokenSet);
  }

  function draftPayload(): MinerUSettingsUpdate {
    const body: MinerUSettingsUpdate = {
      api_base_url: baseUrl.value.trim() || "https://mineru.net",
      model_version: modelVersion.value as MinerUSettingsPublic["model_version"],
      language: language.value,
      enable_formula: enableFormula.checked,
      enable_table: enableTable.checked,
      is_ocr: isOcr.checked,
      timeout_s: Number(timeout.value) || 600,
    };
    const token = tokenInput.value.trim();
    if (token) body.api_token = token;
    return body;
  }

  async function handleSave(): Promise<void> {
    status.textContent = "保存中…";
    try {
      const payload = draftPayload();
      if (!payload.api_token && !(await mineruSettings.get()).api_token_set) {
        status.textContent = "请先填写 API Token";
        return;
      }
      const saved = await mineruSettings.save(payload);
      applySettings(saved.settings, saved.api_token_set, saved.api_token || "");
      status.textContent = "已保存";
      document.dispatchEvent(new CustomEvent("studio:mineru-settings-changed"));
    } catch (err) {
      status.textContent = String(err);
    }
  }

  const testBtn = el("button", "btn-secondary", "测试连通") as HTMLButtonElement;
  testBtn.type = "button";
  testBtn.onclick = async () => {
    testResult.textContent = "测试中…";
    testResult.className = "mineru-test-result";
    try {
      const out = await mineruSettings.test(draftPayload());
      testResult.textContent = out.message;
      testResult.classList.toggle("ok", out.ok);
      testResult.classList.toggle("err", !out.ok);
    } catch (err) {
      testResult.textContent = String(err);
      testResult.classList.add("err");
    }
  };

  const saveBtn = el("button", "btn-primary", "保存") as HTMLButtonElement;
  saveBtn.type = "submit";

  form.onsubmit = async (e) => {
    e.preventDefault();
    await handleSave();
  };

  const actions = el("div", "import-form-actions");
  actions.append(testBtn, saveBtn);
  form.append(actions);

  card.append(form, testResult, status);
  container.appendChild(card);

  status.textContent = "加载中…";
  mineruSettings
    .get()
    .then((payload) => {
      applySettings(
        payload.settings,
        payload.api_token_set,
        payload.api_token || "",
      );
      status.textContent = "";
    })
    .catch((err) => {
      status.textContent = String(err);
    });
}

/** 兼容旧事件：统一走 store 内嵌面板 */
export function mountMinerUSettings(): void {
  document.addEventListener("studio:mineru-settings", () => store.showMinerUSettings());
}

export async function mineruTokenConfigured(): Promise<boolean> {
  try {
    const out = await mineruSettings.get();
    return out.api_token_set;
  } catch {
    return false;
  }
}
