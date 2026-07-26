import { Editor } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import { corpus } from "../api/corpus";
import { debounce, el } from "../util/dom";

export type IrBlock = {
  id: string;
  type: string;
  page_no: number;
  text?: string | null;
  caption?: string | null;
  meta?: Record<string, unknown>;
};

const ZONE_LABELS: Record<string, string> = {
  main: "主栏",
  aside_left: "左栏",
  aside_right: "边注",
  full_bleed: "全宽",
  running: "页眉",
};

function zoneLabel(zone: string): string {
  return ZONE_LABELS[zone] || zone;
}

function blockTitle(block: IrBlock): string {
  const role = String(block.meta?.pedagogy_role || block.type);
  const preview = (block.text || block.caption || "").slice(0, 40);
  return `${role}${preview ? ` · ${preview}` : ""}`;
}

export async function mountPageBlockEditor(
  container: HTMLElement,
  opts: { jobId: string; pageNo: number; onRerender?: (html: string) => void },
): Promise<{ destroy: () => void } | void> {
  container.innerHTML = "";
  container.appendChild(el("p", "muted", "加载块列表…"));

  let data: { blocks: IrBlock[] };
  try {
    data = await corpus.importBlocks(opts.jobId);
  } catch (e) {
    container.innerHTML = "";
    container.appendChild(el("p", "muted", `无法加载 IR：${String(e)}`));
    return;
  }

  const pageBlocks = data.blocks.filter((b) => b.page_no === opts.pageNo);
  if (!pageBlocks.length) {
    container.innerHTML = "";
    container.appendChild(el("p", "muted", "本页无 IR 块"));
    return;
  }

  container.innerHTML = "";
  const byZone = new Map<string, IrBlock[]>();
  for (const b of pageBlocks) {
    const z = String(b.meta?.layout_zone || "main");
    if (b.meta?.skip_render) continue;
    const list = byZone.get(z) || [];
    list.push(b);
    byZone.set(z, list);
  }

  const editors: Editor[] = [];

  for (const zone of ["main", "full_bleed", "aside_left", "aside_right"]) {
    const blocks = byZone.get(zone);
    if (!blocks?.length) continue;
    const section = el("section", "block-zone");
    section.appendChild(el("h3", "block-zone-title", zoneLabel(zone)));
    container.appendChild(section);

    for (const block of blocks) {
      if (block.type === "figure" || block.meta?.figure_group_id) {
        const card = el("div", "block-card block-card--figure");
        card.appendChild(el("div", "block-card-label", blockTitle(block)));
        if (block.caption) card.appendChild(el("p", "", block.caption));
        section.appendChild(card);
        continue;
      }
      const editable = block.type !== "equation" && block.type !== "table";
      const card = el("div", "block-card");
      card.appendChild(el("div", "block-card-label", blockTitle(block)));

      if (!editable) {
        const pre = el("pre", "block-readonly", block.text || block.caption || "");
        section.appendChild(card);
        card.appendChild(pre);
        continue;
      }

      const editorHost = el("div", "block-editor-host");
      card.appendChild(editorHost);
      section.appendChild(card);

      const editor = new Editor({
        element: editorHost,
        extensions: [
          StarterKit.configure({ heading: { levels: [2, 3, 4] } }),
          Placeholder.configure({ placeholder: "编辑块正文…" }),
        ],
        content: `<p>${escapeHtml(block.text || "").replace(/\n\n/g, "</p><p>").replace(/\n/g, "<br>")}</p>`,
      });
      editors.push(editor);

      const save = debounce(async () => {
        const text = editor.getText().trim();
        await corpus.patchImportBlock(opts.jobId, block.id, { text });
        const res = await corpus.rerenderImportPage(opts.jobId, opts.pageNo);
        opts.onRerender?.(res.html);
      }, 900);
      editor.on("update", save);
    }
  }

  const destroy = () => {
    editors.forEach((ed) => {
      try {
        ed.destroy();
      } catch {
        /* ignore */
      }
    });
    editors.length = 0;
  };
  // DOMNodeRemoved is unreliable; callers should invoke destroy via returned handle.
  (container as HTMLElement & { __pageBlockDestroy?: () => void }).__pageBlockDestroy = destroy;
  return { destroy };
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
