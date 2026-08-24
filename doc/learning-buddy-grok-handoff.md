# Learning Buddy × Eason grok bot 协同说明

给 Cursor 用。按这份对接，不要另发明协议。

## 角色

- **你们的系统（Spark / exam-ocr-server / learning-buddy-api）**：拍照或上传作业，OCR 出题目和学生作答，然后调用 grok bot。
- **Eason grok bot**：判对错、给正解，并到指定教材 PDF 里对章节和相近例题。它是异步大脑，不是毫秒级同步 API。
- **教材**：就是原始 PDF。不建向量库，不把全书抽成另一套知识库。题来了再打开 PDF 搜。

## 硬规则（必须遵守）

1. 先读教材，再用书上的定义、符号、讲法判题、解题。
2. 书上没有的，回复「教材未覆盖」，禁止用模型自己的通识或发挥顶上去。
3. 禁止把正式出版教材整本转成 Markdown / HTML / 全文数据库。只允许按**当前这一题**引用需要的定义、定理、例题。
4. 当前已接入的书：沪教版《普通高中教科书 数学 必修第一册》（152 页）。PDF 已在 grok bot 侧。其他科目：把 PDF 放到约定目录即可。

## 怎么叫 grok bot

用已建好的 routine：**Learning Buddy webhook**。

1. 打开该 routine 面板，复制 **webhook URL** 和 **sender key**（grok bot 自己看不到 key，必须由人粘到环境变量）。
2. 学生作业识别完成后，对 URL 发 `POST`，`Content-Type: application/json`，按面板要求带上 sender key（常见是 header，以面板为准）。
3. 这是异步的：请求发出后 grok bot 会被叫醒，结果默认出现在 Eason 与 grok bot 的聊天里。若要写回你们自己的系统，带上 `callback_url`。

不要让学生同学等待这个调用的 HTTP 返回当最终批改结果。OCR 服务自己先回 202，批改结果走回调或轮询你们库里的记录。

## 请求 JSON

```json
{
  "request_id": "lb-20260819-0001",
  "subject": "math",
  "grade": "high1",
  "textbook_hint": "沪教版数学必修第一册",
  "problem_text": "用列举法表示：不大于10的正整数组成的集合",
  "student_answer": "{0,1,2,3,4,5,6,7,8,9,10}",
  "student_id": "optional",
  "images": [],
  "callback_url": "https://your-api.example/internal/grok-callback"
}
```

字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| request_id | 是 | 你们生成，回调原样带回 |
| problem_text | 是 | OCR 后的题目正文，尽量带上题号和选项 |
| student_answer | 建议 | 学生作答；缺了只能给正解，不能判对错 |
| subject | 建议 | `math` / `chinese` / … 用来选哪本教材 |
| textbook_hint | 建议 | 书名或册次，减少搜错书 |
| grade | 否 | 年级 |
| student_id | 否 | 不要用真实姓名 |
| images | 否 | 题目或作答的图 URL，OCR 残缺时用 |
| callback_url | 否 | 你们内网或可达的回调；只接你们自己的服务 |

一次 POST 一道题。一套卷子拆成多题多次调用。

## 回调 JSON（若带了 callback_url）

`POST`，`Content-Type: application/json`：

```json
{
  "request_id": "lb-20260819-0001",
  "correct": false,
  "verdict": "错",
  "correct_solution": "不大于10的正整数是{1,2,3,4,5,6,7,8,9,10}，0不是正整数。",
  "textbook": {
    "title": "普通高中教科书 数学 必修第一册",
    "chapter": "第1章 集合与逻辑",
    "section": "1.1 集合初步",
    "examples": ["例3"]
  },
  "book_missing": false,
  "notes": "学生把0算进正整数。"
}
```

`book_missing: true` 表示按硬规则无法用教材作答，`correct_solution` 可空，`notes` 写明哪一块书上没有。

## 教材放哪

优先一个固定目录，grok bot 按题打开 PDF，不建向量库。

建议（二选一或都用）：

- Windows（Cursor 本机）：`C:\\Users\\Eason\\LearningBuddy\\textbooks\\`
- Spark（已能 SSH）：`/home/mylearningbuddy/MyLearningBuddy_Model_Workspace/data/textbooks/`

SSH 已从 Windows 打通：`ssh gx10-f72e.local`（用户 `mylearningbuddy`，主机 `192.168.1.10`）。不要把私钥拷走。

目录里用书名命名，例如：

```
textbooks/math/shangjiao-bixiu-1.pdf
textbooks/math/shangjiao-bixiu-2.pdf
```

新书放进去后，在对接配置里登记书名和路径。未登记的书，grok bot 也能按文件名搜，但 `textbook_hint` 更稳。

## Cursor 要做的

1. 在 `exam-ocr-server` 或 `learning-buddy-api` 增加：OCR 完成后把每题 POST 到 webhook。
2. 环境变量：`GROK_WEBHOOK_URL`、`GROK_WEBHOOK_KEY`（从 routine 面板复制，勿提交进 git）。
3. 实现 `callback_url` 对应的内部接口，按 `request_id` 写回该题的批改结果。
4. 前端展示：对/错、正解、教材章节、书上例题。不要展示 webhook key。
5. 失败：webhook 超时或无回调则标记「待人工/待 grok」，不要假装已批改。
6. 不要做：全书 PDF→Markdown/HTML/向量库；不要让 grok bot 当实时 OCR 引擎（OCR 留在 Spark）。

## 验收

用必修一 1.1 的题打一条：

- `problem_text`: 用列举法表示所有不大于10的正整数组成的集合
- `student_answer`: `{0,1,2,3,4,5,6,7,8,9,10}`

预期：判错；正解不含 0；章节指向 1.1 集合初步；例题能对上例3 一类。聊天里和回调里都能看到。

## 不要问 grok bot 的

- 全书导出、全文知识库、向量化教材
- 替代 Spark 上已有的 OCR
- 在学生点击提交的同一请求里同步等到批改
