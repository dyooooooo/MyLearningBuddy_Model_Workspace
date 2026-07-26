# `/test/` 前端定位裁决

- **现状**：`exam-ocr-server/static` + `frontend/` Vite 补充，学生侧伴学试验台。
- **裁决**：Android 薄客户端能力迁移完成后，`/test/` **降级为纯验收 harness**（smoke / acceptance），不再作为第三套「半正式」产品前端生长。
- **正式运营后台**：`/studio/`（studio-ui）— 教研/导入/Edition/Copilot。
- **消费级产品 UI**（家长审核、学习看板等）：独立立项，React/Next，复用 `packages/buddy-client` 契约。
