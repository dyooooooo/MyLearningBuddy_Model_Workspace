import { renderLatexToHtml } from "./mathjax-core";

export function preprocessMarkdownMath(md: string): { text: string; blocks: string[] } {
  const blocks: string[] = [];
  let text = md || "";

  text = text.replace(/\$\$([\s\S]+?)\$\$/g, (_m, tex: string) => {
    const idx = blocks.length;
    const { html, error } = renderLatexToHtml(tex, true);
    blocks.push(error ? `<code>${tex}</code>` : html);
    return `%%MATH_BLOCK_${idx}%%`;
  });

  text = text.replace(/\\\[([\s\S]+?)\\\]/g, (_m, tex: string) => {
    const idx = blocks.length;
    const { html, error } = renderLatexToHtml(tex, true);
    blocks.push(error ? `<code>${tex}</code>` : html);
    return `%%MATH_BLOCK_${idx}%%`;
  });

  text = text.replace(/\\\(([\s\S]+?)\\\)/g, (_m, tex: string) => {
    const idx = blocks.length;
    const { html, error } = renderLatexToHtml(tex, false);
    blocks.push(error ? tex : html);
    return `%%MATH_INLINE_${idx}%%`;
  });

  text = text.replace(/\$([^$\n]+?)\$/g, (_m, tex: string) => {
    const idx = blocks.length;
    const { html, error } = renderLatexToHtml(tex, false);
    blocks.push(error ? tex : html);
    return `%%MATH_INLINE_${idx}%%`;
  });

  return { text, blocks };
}

export function restoreMathPlaceholders(html: string, blocks: string[]): string {
  let out = html;
  blocks.forEach((block, i) => {
    out = out.replace(`%%MATH_BLOCK_${i}%%`, `<div class="math-block">${block}</div>`);
    out = out.replace(`%%MATH_INLINE_${i}%%`, `<span class="math-inline">${block}</span>`);
  });
  return out;
}

function typesetOne(el: HTMLElement): void {
  const latex = el.getAttribute("data-latex") || "";
  if (!latex.trim()) return;
  const display =
    el.classList.contains("math--display") || el.closest(".lb-equation") !== null;
  const { html, error } = renderLatexToHtml(latex, display);
  if (!error && html) {
    el.innerHTML = html;
    el.classList.add("math-typeset");
    el.removeAttribute("data-latex");
  } else {
    el.classList.add("math-error");
    el.textContent = latex;
  }
}

/** Typeset server HTML placeholders (`data-latex`) inside textbook preview. */
export function typesetTextbookMath(root: ParentNode): void {
  root.querySelectorAll<HTMLElement>("[data-latex]").forEach(typesetOne);
}

/**
 * Idle-sliced typeset — never block the main thread for a whole book.
 * Prefer calling only on viewport-visible shells.
 */
export function typesetTextbookMathIdle(root: ParentNode, chunkSize = 8): void {
  const nodes = Array.from(root.querySelectorAll<HTMLElement>("[data-latex]"));
  let i = 0;
  const step = () => {
    const end = Math.min(i + chunkSize, nodes.length);
    for (; i < end; i++) typesetOne(nodes[i]!);
    if (i < nodes.length) {
      if (typeof requestIdleCallback === "function") {
        requestIdleCallback(() => step(), { timeout: 400 });
      } else {
        window.setTimeout(step, 0);
      }
    }
  };
  step();
}
