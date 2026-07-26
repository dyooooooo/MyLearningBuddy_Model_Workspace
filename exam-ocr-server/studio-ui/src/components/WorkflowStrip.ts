import { store, type RibbonSection } from "../state/store";
import { el } from "../util/dom";

type StepId = "import" | "review" | "publish" | "search";

const STEPS: { id: StepId; label: string; ribbon: RibbonSection; hint: string }[] = [
  { id: "import", label: "1 导入", ribbon: "import", hint: "上传 PDF、页预览校对" },
  { id: "review", label: "2 校对", ribbon: "textbook", hint: "在教材树里改内容、提交审核" },
  { id: "publish", label: "3 发布", ribbon: "todo", hint: "待办里提交审核 / 正式发布" },
  { id: "search", label: "4 检索", ribbon: "search", hint: "试搜已发布内容" },
];

function activeStep(): StepId {
  const r = store.activeRibbon;
  if (r === "import" || r === "edition-ops") return "import";
  if (r === "todo") return "publish";
  if (r === "search") return "search";
  if (r === "textbook") {
    const st = store.currentNode?.status;
    if (st === "review" || st === "published") return "publish";
    return "review";
  }
  return "import";
}

export function mountWorkflowStrip(root: HTMLElement): void {
  root.classList.add("studio-workflow");
  root.setAttribute("aria-label", "今日流程");

  const render = (): void => {
    root.innerHTML = "";
    // 方案页不属于「今日导入流程」，避免面包屑错位
    if (store.activeRibbon === "edition-ops") {
      root.appendChild(el("span", "studio-workflow-label", "当前"));
      root.appendChild(el("span", "studio-workflow-step active", "方案配置"));
      return;
    }
    const cur = activeStep();
    const label = el("span", "studio-workflow-label", "今日流程");
    root.appendChild(label);
    for (const step of STEPS) {
      const btn = el("button", "studio-workflow-step", step.label) as HTMLButtonElement;
      btn.type = "button";
      btn.title = step.hint;
      btn.classList.toggle("active", step.id === cur);
      btn.onclick = () => {
        if (step.ribbon === "import") store.showImportHub();
        else store.setActiveRibbon(step.ribbon);
      };
      root.appendChild(btn);
      if (step.id !== "search") {
        root.appendChild(el("span", "studio-workflow-sep", "→"));
      }
    }
  };

  render();
  store.subscribe(render);
}
