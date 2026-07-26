import { corpus } from "../api/corpus";
import { el } from "../util/dom";

let dropdown: HTMLElement | null = null;

export function attachWikilinkAutocomplete(textarea: HTMLTextAreaElement): void {
  textarea.addEventListener("input", () => {
    const val = textarea.value;
    const pos = textarea.selectionStart;
    const before = val.slice(0, pos);
    const match = before.match(/\[\[([^\]|]*)$/);
    if (!match) {
      hideDropdown();
      return;
    }
    const q = match[1];
    debouncedSearch(textarea, q, pos);
  });

  textarea.addEventListener("keydown", (e) => {
    if (!dropdown || dropdown.classList.contains("hidden")) return;
    const items = dropdown.querySelectorAll(".ac-item");
    const active = dropdown.querySelector(".ac-item.active");
    let idx = Array.from(items).indexOf(active as Element);
    if (e.key === "ArrowDown") {
      e.preventDefault();
      idx = Math.min(idx + 1, items.length - 1);
      items.forEach((it, i) => it.classList.toggle("active", i === idx));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      idx = Math.max(idx - 1, 0);
      items.forEach((it, i) => it.classList.toggle("active", i === idx));
    } else if (e.key === "Enter" && active) {
      e.preventDefault();
      (active as HTMLElement).click();
    } else if (e.key === "Escape") {
      hideDropdown();
    }
  });
}

let debounceTimer: ReturnType<typeof setTimeout>;

function debouncedSearch(textarea: HTMLTextAreaElement, q: string, pos: number): void {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(async () => {
    const nodes = await corpus.searchNodes(q || " ", "15");
    showDropdown(textarea, nodes, pos, q);
  }, 200);
}

function showDropdown(
  textarea: HTMLTextAreaElement,
  nodes: { id: string; label: string; node_type: string }[],
  pos: number,
  q: string,
): void {
  hideDropdown();
  dropdown = el("ul", "wikilink-ac");
  const rect = textarea.getBoundingClientRect();
  dropdown.style.top = `${rect.bottom + 4}px`;
  dropdown.style.left = `${rect.left}px`;

  nodes.forEach((n, i) => {
    const li = el("li", `ac-item${i === 0 ? " active" : ""}`, `${n.label} (${n.node_type})`);
    li.onclick = () => {
      const val = textarea.value;
      const before = val.slice(0, pos);
      const after = val.slice(pos);
      const start = before.lastIndexOf("[[");
      const insert = `[[${n.id}|${n.label}]]`;
      textarea.value = val.slice(0, start) + insert + after;
      textarea.focus();
      hideDropdown();
    };
    dropdown!.appendChild(li);
  });

  if (!nodes.length) {
    dropdown.appendChild(el("li", "ac-item muted", `无匹配 "${q}"`));
  }
  document.body.appendChild(dropdown);
}

function hideDropdown(): void {
  dropdown?.remove();
  dropdown = null;
}
