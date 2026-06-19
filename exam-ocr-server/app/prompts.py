PAGE_ANALYZE_PROMPT = """你是试卷页面分析引擎。按页面从上到下的阅读顺序输出 parts 数组，完整保留所有可见文字，不要省略标题、页眉、大题说明。

每项 kind 含义：
- header：第一道题之前的印刷内容（试卷名、科目、姓名栏、注意事项、页眉页脚等）；通常无手写
- section：大题/题型标题（如「一、填空题」「二、计算题」），仅印刷，不是具体小题
- question：具体小题；print=印刷题干/选项，handwriting=学生手写答案

JSON 格式（不要 markdown）：
{
  "parts": [
    {
      "kind": "header",
      "print": "标题与页眉区域全部印刷文字",
      "handwriting": "",
      "bbox_print": [x1,y1,x2,y2]
    },
    {
      "kind": "section",
      "print": "一、填空题（每题2分）",
      "bbox_print": [x1,y1,x2,y2]
    },
    {
      "kind": "question",
      "id": "1",
      "print": "该题印刷文字",
      "handwriting": "该题手写文字，无则空字符串",
      "bbox_print": [x1,y1,x2,y2],
      "bbox_handwriting": [x1,y1,x2,y2]
    }
  ]
}

规则：
1. parts 顺序必须与页面从上到下一致
2. 不得跳过 header、section；没有则可省略该项，但不要把标题并入题目
3. 手写数字/公式归入 handwriting
4. bbox 使用 0-1 归一化坐标；无手写时 handwriting 为 ""，bbox_handwriting 可省略"""
