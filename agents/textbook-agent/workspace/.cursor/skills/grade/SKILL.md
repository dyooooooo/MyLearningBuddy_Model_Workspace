---
name: grade
description: 按教材 PDF/MD 判一道题，只输出 JSON。
---

# 按教材判题

1. 用 `textbook_hint` 和题目关键词在 `textbooks/**/*.md` 里搜索。
2. 打开命中页对应的原版 `.pdf` 核对定义、符号、例题编号。
3. 比较 `student_answer` 与书上讲法。
4. 只输出：

```json
{
  "request_id": "",
  "correct": false,
  "verdict": "错",
  "correct_solution": "",
  "textbook": {
    "title": "",
    "chapter": "",
    "section": "",
    "examples": []
  },
  "book_missing": false,
  "notes": ""
}
```

书上找不到依据时 `book_missing` 为 true，`correct` 为 null。
