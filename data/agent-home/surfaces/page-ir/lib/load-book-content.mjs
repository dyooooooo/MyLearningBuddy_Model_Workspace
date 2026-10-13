import fs from "node:fs";
import path from "node:path";

const cache = new Map();

function safeBookId(id) {
  const s = String(id || "");
  return /^[A-Za-z0-9_-]{1,80}$/.test(s) ? s : "";
}

function agentHome() {
  if (process.env.AGENT_HOME) return process.env.AGENT_HOME;
  let dir = process.cwd();
  for (let i = 0; i < 8; i++) {
    try {
      if (fs.existsSync(path.join(dir, "textbooks"))) return dir;
    } catch {
      break;
    }
    const parent = path.dirname(dir);
    if (parent === dir) break;
    dir = parent;
  }
  return "";
}

function bookDir(bookId) {
  const id = safeBookId(bookId);
  const home = agentHome();
  if (!id || !home) return "";
  return path.join(home, "textbooks", id);
}

export function loadBookPages(bookId) {
  const key = `pages:${bookId}`;
  if (cache.has(key)) return cache.get(key);
  const dir = bookDir(bookId);
  const pagesDir = dir ? path.join(dir, "ir", "pages") : "";
  const pages = [];
  if (pagesDir && fs.existsSync(pagesDir)) {
    const files = fs.readdirSync(pagesDir).filter((name) => /^\d+\.json$/.test(name)).sort();
    for (const name of files) {
      try {
        const data = JSON.parse(fs.readFileSync(path.join(pagesDir, name), "utf8"));
        if (data && typeof data === "object" && !Array.isArray(data)) pages.push(data);
      } catch {
        /* skip one broken page */
      }
    }
  }
  cache.set(key, pages);
  return pages;
}

export function loadPrintToc(bookId) {
  const key = `print-toc:${bookId}`;
  if (cache.has(key)) return cache.get(key);
  const dir = bookDir(bookId);
  const tocPath = dir ? path.join(dir, "ir", "print_toc.json") : "";
  let toc = null;
  if (tocPath && fs.existsSync(tocPath)) {
    try {
      const data = JSON.parse(fs.readFileSync(tocPath, "utf8"));
      if (data && typeof data === "object" && !Array.isArray(data)) toc = data;
    } catch {
      toc = null;
    }
  }
  cache.set(key, toc);
  return toc;
}

export function loadBookToc(bookId) {
  const key = `toc:${bookId}`;
  if (cache.has(key)) return cache.get(key);
  const dir = bookDir(bookId);
  const tocPath = dir ? path.join(dir, "ir", "toc.json") : "";
  let toc = null;
  if (tocPath && fs.existsSync(tocPath)) {
    try {
      const data = JSON.parse(fs.readFileSync(tocPath, "utf8"));
      if (data && typeof data === "object" && !Array.isArray(data)) toc = data;
    } catch {
      toc = null;
    }
  }
  cache.set(key, toc);
  return toc;
}
