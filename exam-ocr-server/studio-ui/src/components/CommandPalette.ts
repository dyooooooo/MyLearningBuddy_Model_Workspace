import { populateSearchResults, type SearchTab } from "../util/corpusSearch";
import { el } from "../util/dom";

export function mountCommandPalette(): void {
  document.addEventListener("studio:command-palette", openPalette);
  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "k") {
      e.preventDefault();
      openPalette();
    }
  });
}

function openPalette(): void {
  const existing = document.querySelector(".command-palette");
  if (existing) existing.remove();

  let activeTab: SearchTab = "nodes";

  const overlay = el("div", "command-palette");
  const tabs = el("div", "palette-tabs");
  const tabNodes = el("button", "palette-tab active", "按名称");
  const tabVec = el("button", "palette-tab", "试搜效果");
  const tabQueue = el("button", "palette-tab", "待办");
  tabNodes.type = "button";
  tabVec.type = "button";
  tabQueue.type = "button";
  tabs.append(tabNodes, tabVec, tabQueue);

  const input = document.createElement("input");
  input.placeholder = "搜索…";
  input.className = "palette-input";
  const results = el("ul", "palette-results");
  overlay.append(tabs, input, results);
  document.body.appendChild(overlay);
  input.focus();

  const setTab = (t: SearchTab) => {
    activeTab = t;
    [tabNodes, tabVec, tabQueue].forEach((b) => b.classList.remove("active"));
    if (t === "nodes") tabNodes.classList.add("active");
    if (t === "vector") tabVec.classList.add("active");
    if (t === "queue") tabQueue.classList.add("active");
    void runSearch();
  };
  tabNodes.onclick = () => setTab("nodes");
  tabVec.onclick = () => setTab("vector");
  tabQueue.onclick = () => setTab("queue");

  const close = (e: MouseEvent) => {
    if (!(e.target as HTMLElement).closest(".command-palette")) {
      overlay.remove();
      document.removeEventListener("click", close);
    }
  };
  setTimeout(() => document.addEventListener("click", close), 100);

  async function runSearch(): Promise<void> {
    await populateSearchResults(results, activeTab, input.value.trim(), () => overlay.remove());
  }

  input.oninput = () => void runSearch();

  input.onkeydown = (e) => {
    if (e.key === "Escape") overlay.remove();
  };
}
