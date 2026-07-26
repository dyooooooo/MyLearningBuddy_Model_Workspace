const LS_LOCALE = "lb_locale";

export function getLocale() {
  const v = (localStorage.getItem(LS_LOCALE) || "zh").toLowerCase();
  return v.startsWith("en") ? "en" : "zh";
}

export function setLocale(locale) {
  localStorage.setItem(LS_LOCALE, locale.startsWith("en") ? "en" : "zh");
}

export const MASTERY_LABELS = {
  zh: { unknown: "未知", learning: "学习中", mastered: "已掌握" },
  en: { unknown: "Unknown", learning: "Learning", mastered: "Mastered" },
};

export const UI = {
  zh: {
    wrongBookTitle: "错题本",
    wrongConfirmed: "已确认错题（Agent + 人工）",
    wrongCandidates: "待复核 / 可能漏判",
    addToWrongBook: "加入错题本",
    removeFromWrongBook: "移出错题本",
    restoreAgent: "恢复 Agent 判定",
    evalPending: "评测中…",
    evalDone: "Agent 评测完成",
    deleteHomework: "删除作业",
    editHomework: "编辑备注",
    reEval: "重新评测",
    addKp: "添加知识点",
    deleteKp: "删除",
    recompute: "重新聚合",
    goWrongBook: "在错题本改判",
    agentWrong: "Agent 错题",
    agentCorrect: "Agent 正确",
    manualWrong: "人工错题",
    uncertain: "待判定",
  },
  en: {
    wrongBookTitle: "Wrong book",
    wrongConfirmed: "Confirmed wrong (Agent + manual)",
    wrongCandidates: "Review / possible misses",
    addToWrongBook: "Add to wrong book",
    removeFromWrongBook: "Remove",
    restoreAgent: "Restore Agent",
    evalPending: "Evaluating…",
    evalDone: "Agent eval complete",
    deleteHomework: "Delete homework",
    editHomework: "Edit note",
    reEval: "Re-eval",
    addKp: "Add KP",
    deleteKp: "Delete",
    recompute: "Recompute",
    goWrongBook: "Edit in wrong book",
    agentWrong: "Agent wrong",
    agentCorrect: "Agent correct",
    manualWrong: "Manual wrong",
    uncertain: "Pending",
  },
};

export function t(key) {
  const loc = getLocale();
  return UI[loc]?.[key] ?? UI.zh[key] ?? key;
}

export function kpDisplayLabel(tagOrRecord) {
  if (!tagOrRecord) return "";
  const loc = getLocale();
  const i18n = tagOrRecord.label_i18n;
  if (i18n && typeof i18n === "object") {
    return loc === "en" ? i18n.en || i18n.zh : i18n.zh || i18n.en;
  }
  return tagOrRecord.label || tagOrRecord.kp_id || "";
}
