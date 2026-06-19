# Spark C2 切流清单（验收后手动）

Spark 侧验收已通过（`scripts/acceptance-c2.sh`）。以下在 **Windows / WSL / 平板** 执行。

## 1. 平板 MyLearningBuddy

将 C2 基址改为：

```text
http://192.168.3.200:8000
```

## 2. 停 Windows / WSL 旧 C2

在 Windows / WSL 停止：

- `exam-ocr-server` `:8000`
- `exam-ocr` Hub `:8765`

## 3.（可选）同步真实 exam-ocr-server

在 **WSL** 执行：

```bash
rsync -avz --exclude '.gradle' --exclude 'build' --exclude 'node_modules' \
  /mnt/d/01_coding/LearningStack/exam-ocr-server/ \
  mylearningbuddy@192.168.3.200:~/workspace/exam-ocr-server/
```

在 **Spark** 重建网关：

```bash
cd ~/workspace/learning-buddy-c2
```

```bash
docker compose build c2-gateway
```

```bash
docker compose up -d c2-gateway
```

当前运行的是 **FastAPI 脚手架**（已接 vLLM vision），可后续替换为真实 Java/Kotlin 网关。

## 4. 运维

```bash
cd ~/workspace/learning-buddy-c2
```

```bash
docker compose ps
```

```bash
docker compose logs -f vllm
```

```bash
docker compose restart c2-gateway
```
