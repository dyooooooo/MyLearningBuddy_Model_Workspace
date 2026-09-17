export function prepare(md, _ctx = {}) {
  return String(md || "")
    .replace(/\r\n/g, "\n")
    .replace(/\n{3,}/g, "\n\n")
    .replace(/^\n+/, "");
}
