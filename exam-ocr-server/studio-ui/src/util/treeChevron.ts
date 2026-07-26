/** Obsidian-style tree fold chevron (CSS icon, no unicode glyphs). */

export function createTreeChevron(expanded: boolean): HTMLButtonElement {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "tree-chevron";
  setTreeChevronExpanded(btn, expanded);
  return btn;
}

export function setTreeChevronExpanded(btn: HTMLButtonElement, open: boolean): void {
  btn.classList.toggle("is-expanded", open);
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  btn.setAttribute("aria-label", open ? "折叠" : "展开");
}
