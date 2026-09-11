const DEFAULT_TITLES = {
  header: "页眉",
  footnote: "页脚注",
  footer: "页脚",
  page: "页码",
};

function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function stripFrontmatter(md) {
  const match = String(md || "").match(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/);
  return match ? md.slice(match[0].length) : md;
}

function calloutTitle(type, explicit, profile) {
  const key = String(type || "").toLowerCase();
  const titles = profile.callout_titles && typeof profile.callout_titles === "object" ? profile.callout_titles : {};
  return explicit || titles[key] || DEFAULT_TITLES[key] || type;
}

function convertCallouts(md, profile) {
  return md.replace(
    /(^|\n)(?:>\s*\[!([A-Za-z0-9_-]+)\][^\n]*\n)(?:>[^\n]*(?:\n|$))+/g,
    (whole, lead, type) => {
      const lines = whole.trim().split("\n").map((line) => line.replace(/^>\s?/, ""));
      const head = lines[0].replace(/^\[![^\]]+\]\s*/, "").trim();
      const body = lines.slice(1).join("\n");
      const cls = String(type || "").toLowerCase().replace(/[^a-z0-9_-]/g, "");
      const title = calloutTitle(cls, head, profile);
      return (
        `${lead}<aside class="kb-callout kb-callout-${cls}">` +
        `<div class="kb-callout-title">${escapeHtml(title)}</div>` +
        `<div class="kb-callout-body">${escapeHtml(body).replace(/\n/g, "<br>")}</div>` +
        `</aside>\n`
      );
    },
  );
}

function stampAnchors(md) {
  return md.replace(/(^|[ \t])\^([A-Za-z0-9_-]+)[ \t]*$/gm, '$1<span id="$2" class="kb-anchor"></span>');
}

export function prepare(md, ctx = {}) {
  const profile = ctx.profile && typeof ctx.profile === "object" ? ctx.profile : {};
  let text = String(md || "").replace(/\r\n/g, "\n");
  text = stripFrontmatter(text);
  text = convertCallouts(text, profile);
  text = stampAnchors(text);
  return text.replace(/\n{3,}/g, "\n\n").replace(/^\n+/, "");
}
