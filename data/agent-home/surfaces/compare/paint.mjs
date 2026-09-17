function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pickMode(profile, payload) {
  const modes = Array.isArray(profile.modes) ? profile.modes : [];
  const id = String(payload.mode || profile.default_mode || "");
  return modes.find((row) => row && row.id === id) || null;
}

export function paint(payload = {}, ctx = {}) {
  const profile = ctx.profile && typeof ctx.profile === "object" ? ctx.profile : {};
  const kind = String(payload.kind || "legend");
  const mode = payload.mode ? pickMode(profile, payload) : null;
  const left = payload.legend_left || (mode && mode.left_label) || profile.legend_left || "MinerU 文本";
  const right = payload.legend_right || (mode && mode.right_label) || profile.legend_right || "原始页图";
  const imageFirst = String(profile.image_side || "right") === "left";
  if (kind === "legend") {
    const a = `<div class="kb-compare-legend-cell">${escapeHtml(left)}</div>`;
    const b = `<div class="kb-compare-legend-cell">${escapeHtml(right)}</div>`;
    return imageFirst ? `${b}${a}` : `${a}${b}`;
  }
  if (kind === "image-tag") {
    const page = Number(payload.pageNo || 0);
    const tmpl = String(profile.image_tag || "第 {page} 页 · 原图");
    return escapeHtml(tmpl.replace("{page}", String(page)));
  }
  return "";
}
