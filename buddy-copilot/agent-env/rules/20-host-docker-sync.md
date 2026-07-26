# 宿主机 ↔ Docker 同步（摘自工作区 host-docker-sync 规则）

改宿主机源码后，容器**不会**自动生效。先判断代码是挂载还是打进镜像：

- **bind-mount 挂载** → 改宿主机即时生效（如 `learning-buddy-api/configs/editions/`）。
- **打进镜像** → 必须 `docker compose build` 重建再 `up -d`（如
  `learning-buddy-api/app/**`，包括 prompt 模板 `app/services/editions/prompts/*.md`）。

## learning-buddy-c2 现状

`buddy-api` / `buddy-worker` 的 app 代码打进镜像（volumes 只挂 logs/static/profiles/data/configs）。
导入流水线跑在 **buddy-worker**：prompt/normalize 改动必须**同时重建 api 和 worker**。

重建与重启由编排器执行（`docker.rebuild` 工具，用户确认后），你只在 Plan 里声明需要。
重建后验证容器内文件与宿主机一致（编排器自动做 `docker exec` 抽查）。
