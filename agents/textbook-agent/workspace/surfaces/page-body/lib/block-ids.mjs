const TRAILING = / \^kb-[0-9a-fA-F]{6}[ \t]*$/gm;
const STANDALONE = /^\^kb-[0-9a-fA-F]{6}[ \t]*$/gm;

export function stripBlockIds(text) {
  const stripped = String(text || "")
    .replace(STANDALONE, "")
    .replace(TRAILING, "")
    .replace(/\n{3,}/g, "\n\n");
  return stripped;
}
