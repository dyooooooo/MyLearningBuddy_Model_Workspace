import { corpus } from "../api/corpus";
import type { CorpusNode } from "../api/types";
import { renderContainerOutline } from "./ContainerOutline";
import { mountPageBlockEditor } from "./PageBlockEditor";
import {
  isTextbookReadable,
  mountTextbookScrollReader,
  textbookIdOf,
  type TextbookScrollHandle,
} from "./TextbookScrollReader";
import { attachWikilinkAutocomplete } from "./WikilinkAutocomplete";
import { store } from "../state/store";
import { debounce, el, renderMarkdown } from "../util/dom";
import { deleteNodeWithConfirm } from "../util/deleteNode";
import { isHtmlContent, sanitizeHtml, TEXTBOOK_CSS_HREF } from "../util/html-sanitize";
import { typesetTextbookMath } from "../util/math-render";

export function mountNoteEditor(rootEl: HTMLElement): void {
  let scrollHandle: TextbookScrollHandle | null = null;
  let scrollMountToken = 0;
  let scrollFocusId = "";

  // Only re-render when the open node / import workspace mode changes — not on importJob polls.
  store.subscribeSelector(
    (s) => `${s.currentNode?.id || ""}|${s.importMode}|${s.activeRibbon}|${s.importCenterView}`,
    () => render(),
  );
  render();

  function teardownScroll(): void {
    scrollHandle?.destroy();
    scrollHandle = null;
    scrollFocusId = "";
  }

  function wantsSinglePageEditor(node: CorpusNode): boolean {
    if (node.node_type !== "chunk") return false;
    const kind = String(node.meta?.chunk_kind || "");
    const sourcePage = Number(node.meta?.source_page || 0);
    // Explicit page edit (from「编辑当前页」); keep classic editor.
    return kind === "page_import" || (sourcePage > 0 && /:p\d{4}$/.test(node.id));
  }

  function render(): void {
    const node = store.currentNode;

    // ImportPipelinePanel is rendered above; hide NoteEditor when it is
    // the exclusive content (importMode=true and no node is open).
    if (store.isImportWorkspace() && !node) {
      teardownScroll();
      rootEl.innerHTML = "";
      rootEl.classList.add("hidden");
      return;
    }

    rootEl.classList.remove("hidden");

    if (!node) {
      teardownScroll();
      rootEl.innerHTML = "";
      rootEl.appendChild(el("p", "muted", "从左侧选择节点，或 Ctrl+K 搜索"));
      return;
    }

    // Continuous reader: reuse mount when staying in the same textbook.
    if (
      isTextbookReadable(node) &&
      !wantsSinglePageEditor(node) &&
      store.activeRibbon === "textbook"
    ) {
      const tbId = textbookIdOf(node);
      if (scrollHandle && scrollHandle.textbookId === tbId) {
        if (scrollFocusId !== node.id) {
          scrollFocusId = node.id;
          scrollHandle.scrollToFocus(node);
        }
        return;
      }

      teardownScroll();
      rootEl.innerHTML = "";
      const bc = el("nav", "breadcrumb");
      store.breadcrumb.forEach((c, i) => {
        if (i > 0) bc.appendChild(el("span", "bc-sep", " › "));
        const a = el("a", "bc-link", c.label);
        a.href = "#";
        a.onclick = (e) => {
          e.preventDefault();
          store.openNode(c.id);
        };
        bc.appendChild(a);
      });
      rootEl.appendChild(bc);

      const header = el("div", "note-header");
      header.appendChild(el("h2", "", node.label));
      const adv = el("details", "node-advanced");
      adv.appendChild(el("summary", "", "高级"));
      adv.appendChild(el("code", "node-id", node.id));
      header.appendChild(adv);
      rootEl.appendChild(header);

      if (node.node_type === "textbook" || node.node_type === "section") {
        renderContainerActions(rootEl, node);
      }

      const slot = el("div", "textbook-reader-slot");
      rootEl.appendChild(slot);
      const token = ++scrollMountToken;
      scrollFocusId = node.id;
      void mountTextbookScrollReader(slot, node).then((h) => {
        if (token !== scrollMountToken) {
          h?.destroy();
          return;
        }
        scrollHandle = h;
      });
      return;
    }

    teardownScroll();
    rootEl.innerHTML = "";

    const bc = el("nav", "breadcrumb");
    store.breadcrumb.forEach((c, i) => {
      if (i > 0) bc.appendChild(el("span", "bc-sep", " › "));
      const a = el("a", "bc-link", c.label);
      a.href = "#";
      a.onclick = (e) => {
        e.preventDefault();
        store.openNode(c.id);
      };
      bc.appendChild(a);
    });
    rootEl.appendChild(bc);

    const header = el("div", "note-header");
    header.appendChild(el("h2", "", node.label));
    const adv = el("details", "node-advanced");
    adv.appendChild(el("summary", "", "高级"));
    adv.appendChild(el("code", "node-id", node.id));
    header.appendChild(adv);
    rootEl.appendChild(header);

    if (node.node_type === "textbook" || node.node_type === "section") {
      renderContainerActions(rootEl, node);
      renderContainerSummary(rootEl, node);
      const outlineSlot = el("div", "container-outline");
      rootEl.appendChild(outlineSlot);
      renderContainerOutline(outlineSlot, node.id, node.node_type).catch(console.error);
      return;
    }

    renderByType(rootEl, node);
  }
}

function renderByType(rootEl: HTMLElement, node: CorpusNode): void {
  switch (node.node_type) {
    case "textbook":
    case "section":
      renderContainerSummary(rootEl, node);
      renderContainerActions(rootEl, node);
      break;
    case "chunk":
      renderChunkEditor(rootEl, node);
      break;
    case "kp":
      renderKpTemplate(rootEl, node);
      break;
    case "question":
      renderQuestionTemplate(rootEl, node);
      break;
    case "method":
    case "shortcut":
      renderMethodTemplate(rootEl, node);
      break;
    default:
      renderChunkEditor(rootEl, node);
  }
}

function renderContainerSummary(rootEl: HTMLElement, node: CorpusNode): void {
  const summaryTa = document.createElement("textarea");
  summaryTa.className = "field-summary";
  summaryTa.placeholder = "教材/章节摘要";
  summaryTa.value = node.summary || "";
  rootEl.appendChild(el("label", "", "摘要"));
  rootEl.appendChild(summaryTa);
  const save = debounce(async () => {
    await corpus.patchNode(node.id, { summary: summaryTa.value } as Partial<CorpusNode>);
  }, 800);
  summaryTa.oninput = save;
}

function renderContainerActions(rootEl: HTMLElement, node: CorpusNode): void {
  const actions = el("div", "note-actions");
  const pubSub = el("button", "btn-primary", "发布整棵子树");
  pubSub.type = "button";
  pubSub.onclick = async () => {
    const res = await corpus.publishSubtree(node.id);
    alert(`已发布 ${res.published_count} 个节点`);
    store.openNode(node.id);
  };
  const delTree = el("button", "btn-danger", "删除整棵子树");
  delTree.type = "button";
  delTree.onclick = async () => {
    await deleteNodeWithConfirm(node, { cascade: true });
  };
  actions.append(pubSub, delTree);
  rootEl.appendChild(actions);
}

function renderChunkEditor(rootEl: HTMLElement, node: CorpusNode): void {
  const kpBadge = el("div", "kp-badge", "");
  const pk = String((node.links as Record<string, string>).primary_kp || node.meta.kp_id || "");
  kpBadge.textContent = pk ? `主考纲：${pk}` : "未绑定考纲";
  rootEl.appendChild(kpBadge);

  const importJobId = String(node.meta.import_job_id || "");
  const sourcePage = Number(node.meta.source_page || 0);
  const chunkKind = String(node.meta.chunk_kind || "");
  const sourcePages = Array.isArray(node.meta.source_pages)
    ? (node.meta.source_pages as number[]).map(Number).filter((n) => n > 0)
    : [];
  const isPageChunk = chunkKind === "page_import" || sourcePage > 0;
  const previewPage = sourcePage || sourcePages[0] || 0;
  const htmlMode =
    isHtmlContent(node.full_text || "", node.meta as Record<string, unknown>) ||
    isPageChunk ||
    Boolean(importJobId && previewPage > 0);

  if (htmlMode && chunkKind === "semantic" && importJobId && previewPage > 0) {
    rootEl.appendChild(
      el(
        "p",
        "muted import-preview-hint",
        `当前为 RAG 语义块。预览区显示第 ${previewPage} 页版式；左侧请选「第 N 页」节点编辑单页。`,
      ),
    );
  }

  const backBtn = el("button", "btn-secondary", "← 返回连续阅读");
  backBtn.type = "button";
  backBtn.onclick = () => {
    const tb = textbookIdOf(node);
    if (tb) void store.openNode(tb);
  };
  if (isPageChunk) rootEl.appendChild(backBtn);

  const modeBar = el("div", "mode-bar");
  const previewBtn = el("button", "mode-btn active", "预览");
  const editBtn = el("button", "mode-btn", htmlMode ? "块编辑" : "编辑");
  previewBtn.type = "button";
  editBtn.type = "button";
  modeBar.append(previewBtn, editBtn);
  rootEl.appendChild(modeBar);

  const preview = el("div", htmlMode ? "note-preview textbook-preview" : "note-preview prose");
  const editor = el("div", "note-editor hidden");

  const summaryTa = document.createElement("textarea");
  summaryTa.className = "field-summary";
  summaryTa.placeholder = "摘要";
  summaryTa.value = node.summary || "";

  let bodyHtml = node.full_text || "";
  let layoutHtml = "";

  const updatePreview = async () => {
    if (htmlMode) {
      ensureTextbookStyles();
      let html = bodyHtml || node.full_text || "";
      if (!isPageChunk && importJobId && previewPage > 0) {
        try {
          const res = await corpus.getImportPageHtml(importJobId, previewPage);
          layoutHtml = res.html;
          html = layoutHtml;
        } catch {
          layoutHtml = "";
        }
      }
      preview.innerHTML = sanitizeHtml(html);
      typesetTextbookMath(preview);
      return;
    }
    preview.innerHTML = renderMarkdown(bodyTa?.value || node.full_text || node.summary);
    preview.querySelectorAll(".wikilink").forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        const id = (a as HTMLElement).dataset.nodeId;
        if (id) store.openNode(id);
      });
    });
  };

  let bodyTa: HTMLTextAreaElement | null = null;
  if (!htmlMode) {
    bodyTa = document.createElement("textarea");
    bodyTa.className = "field-body";
    bodyTa.placeholder = "正文（Markdown，支持 $公式$ 与 [[维基链接]]）";
    bodyTa.value = node.full_text || "";
    editor.append(el("label", "", "摘要"), summaryTa, el("label", "", "正文"), bodyTa);
    attachWikilinkAutocomplete(bodyTa);
  } else {
    editor.append(el("label", "", "摘要"), summaryTa);
    const blockSlot = el("div", "page-block-editor");
    editor.append(el("label", "", "按版式块编辑（Tiptap）"), blockSlot);
    if (importJobId && sourcePage > 0) {
      mountPageBlockEditor(blockSlot, {
        jobId: importJobId,
        pageNo: sourcePage,
        onRerender: (html) => {
          bodyHtml = html;
          updatePreview().catch(console.error);
        },
      });
    } else {
      blockSlot.appendChild(el("p", "muted", "缺少 import_job_id / source_page，无法块编辑"));
    }
  }

  previewBtn.onclick = () => {
    previewBtn.classList.add("active");
    editBtn.classList.remove("active");
    preview.classList.remove("hidden");
    editor.classList.add("hidden");
    updatePreview().catch(console.error);
  };
  editBtn.onclick = () => {
    editBtn.classList.add("active");
    previewBtn.classList.remove("active");
    editor.classList.remove("hidden");
    preview.classList.add("hidden");
  };

  const save = debounce(async () => {
    const patch: Partial<CorpusNode> = { summary: summaryTa.value };
    if (bodyTa) patch.full_text = bodyTa.value;
    await corpus.patchNode(node.id, patch);
  }, 800);
  summaryTa.oninput = save;
  if (bodyTa) bodyTa.oninput = save;

  rootEl.append(preview, editor);
  void updatePreview();
}

function ensureTextbookStyles(): void {
  if (document.querySelector(`link[href="${TEXTBOOK_CSS_HREF}"]`)) return;
  const link = document.createElement("link");
  link.rel = "stylesheet";
  link.href = TEXTBOOK_CSS_HREF;
  document.head.appendChild(link);
}

function renderKpTemplate(rootEl: HTMLElement, node: CorpusNode): void {
  rootEl.appendChild(el("div", "prose", node.summary || node.full_text || ""));
  corpus.tree("method", `folder:kp:${node.id}`).then((children) => {
    if (!children.length) return;
    const ul = el("ul", "kp-methods");
    children.forEach((c) => {
      const li = el("li", "");
      const a = el("a", "", c.label);
      a.href = "#";
      a.onclick = (e) => {
        e.preventDefault();
        store.openNode(c.id);
      };
      li.appendChild(a);
      ul.appendChild(li);
    });
    rootEl.appendChild(ul);
  });
}

function renderQuestionTemplate(rootEl: HTMLElement, node: CorpusNode): void {
  rootEl.appendChild(el("div", "question-stem prose", node.full_text || node.summary));
  rootEl.appendChild(
    el("p", "meta-line", `难度: ${node.meta.difficulty || "—"} · 题型: ${node.meta.question_type || "—"}`),
  );
}

function renderMethodTemplate(rootEl: HTMLElement, node: CorpusNode): void {
  const formula = el("p", "formula", String(node.meta.formula || node.summary || ""));
  rootEl.appendChild(formula);
  const steps = el("pre", "method-steps", String(node.meta.steps || node.full_text || ""));
  rootEl.appendChild(steps);
  const pk = String((node.links as Record<string, string>).primary_kp || "");
  if (pk) rootEl.appendChild(el("p", "muted", `关联考纲：${pk}`));
}
