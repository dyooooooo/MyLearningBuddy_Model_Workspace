import { populateSearchResults, type SearchTab } from "../util/corpusSearch";
import { store } from "../state/store";
import { el } from "../util/dom";

export function mountSearchPanel(container: HTMLElement): void {
  container.innerHTML = "";

  let activeTab: SearchTab = "nodes";

  const tabs = el("div", "palette-tabs search-panel-tabs");
  const tabNodes = el("button", "palette-tab active", "按名称");
  const tabVec = el("button", "palette-tab", "试搜效果");
  tabNodes.type = "button";
  tabVec.type = "button";
  tabNodes.title = "按标题/正文关键词找节点";
  tabVec.title = "模拟学生检索：看已发布内容能否被搜到";
  tabs.append(tabNodes, tabVec);

  const todoHint = el("p", "muted search-todo-hint", "");
  const todoLink = el("button", "btn-link", "打开待办清单") as HTMLButtonElement;
  todoLink.type = "button";
  todoLink.onclick = () => store.setActiveRibbon("todo");
  todoHint.append(document.createTextNode("审核/发布请到 "), todoLink);

  const input = document.createElement("input");
  input.placeholder = "输入关键词…（Ctrl+K）";
  input.className = "palette-input search-panel-input";

  const results = el("ul", "palette-results search-panel-results");
  container.append(tabs, todoHint, input, results);

  const setTab = (t: SearchTab): void => {
    activeTab = t === "queue" ? "nodes" : t;
    tabNodes.classList.toggle("active", activeTab === "nodes");
    tabVec.classList.toggle("active", activeTab === "vector");
    void runSearch();
  };
  tabNodes.onclick = () => setTab("nodes");
  tabVec.onclick = () => setTab("vector");

  async function runSearch(): Promise<void> {
    const q = input.value.trim();
    if (!q && activeTab === "nodes") {
      results.innerHTML = "";
      results.appendChild(el("li", "muted", "输入关键词搜索教材节点"));
      return;
    }
    await populateSearchResults(results, activeTab, q);
  }

  input.oninput = () => void runSearch();
  void runSearch();

  document.addEventListener("studio:focus-search", () => {
    if (!container.isConnected) return;
    input.focus();
    input.select();
  });
}

export function focusSearchPanel(): void {
  document.dispatchEvent(new CustomEvent("studio:focus-search"));
}
