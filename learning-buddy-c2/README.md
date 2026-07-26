# Learning Buddy C2 — Spark Dual Docker

vLLM Qwen3.6-35B-NVFP4 (`:8088` internal) + C2 Gateway (`:8000` tablet entry).

## Topology

```text
平板 → http://192.168.1.10:8000 (c2-gateway)
         → http://vllm-qwen36-35b:8088/v1 (vLLM, Docker network)
              → /models/Qwen3.6-35B-A3B-NVFP4

WSL WTR → http://192.168.1.10:8000/v1  (OpenAI LAN proxy, Bearer VLLM_LAN_TOKEN)
         → 同上 vLLM（章节/全文转化；透传 response_format / guided_json）
```

**WTR 不在 Spark 运行**：控制台/API 在 Windows WSL；Spark 仅提供上述 `:8000/v1` 35B 入口。见 `webtraingingrecord/README.md`。

**Deprecated on Spark:** `:8765`, glm_ocr_sdk / pp_ocr / mineru_ocr, Windows/WSL C2 runtime, WTR `:8300`。

## Prerequisites

1. China mirrors: `tools/setup-china-mirrors.sh` (+ sudo system script if needed)
2. Model at `~/opt/models/Qwen3.6-35B-A3B-NVFP4` (~15–25 GB)
3. Optional: rsync real `exam-ocr-server` from WSL — see [RSYNC_FROM_WSL.md](RSYNC_FROM_WSL.md)

### Sudo (one-time, if using `/opt/models`)

```bash
sudo mkdir -p /opt/models
```

```bash
sudo chown -R $USER:$USER /opt/models
```

```bash
sudo ln -sf /home/mylearningbuddy/opt/models/Qwen3.6-35B-A3B-NVFP4 /opt/models/Qwen3.6-35B-A3B-NVFP4
```

Then set `MODELS_HOST_PATH=/opt/models` in `.env`.

## Quick start

```bash
cd ~/MyLearningBuddy_Model_Workspace/learning-buddy-c2
```

```bash
cp .env.example .env
```

```bash
docker pull swr.cn-north-4.myhuaweicloud.com/ddn-k8s/ghcr.io/spark-arena/dgx-vllm-eugr-nightly:latest-linuxarm64
```

```bash
docker tag swr.cn-north-4.myhuaweicloud.com/ddn-k8s/ghcr.io/spark-arena/dgx-vllm-eugr-nightly:latest-linuxarm64 ghcr.io/spark-arena/dgx-vllm-eugr-nightly:latest
```

Image: `ghcr.io/spark-arena/dgx-vllm-eugr-nightly:latest` (Spark SM121 / NVFP4 optimized). Set `VLLM_TEST_FORCE_FP8_MARLIN=0` in `.env`.

```bash
docker compose up -d vllm
```

Wait for vLLM (cold start 5–15 min):

```bash
curl -s http://127.0.0.1:8088/v1/models
```

```bash
docker compose up -d c2-gateway
```

```bash
curl -s http://127.0.0.1:8000/api/v1/system/health
```

```bash
curl -s http://192.168.1.10:8000/api/v1/system/health
```

## Operations

```bash
docker compose logs -f vllm
```

```bash
docker compose logs -f c2-gateway
```

```bash
docker compose restart c2-gateway
```

```bash
docker compose down
```

## OCR Test UI

Browser lab for upload + handwriting/print split + bbox overlay:

```text
http://192.168.1.10:8000/test/
```

Root `/` redirects to `/test/`.

## Smoke test

```bash
./scripts/smoke-c2.sh
```

## Acceptance (Spark)

```bash
C2_BASE_URL=http://192.168.1.10:8000 ./scripts/acceptance-c2.sh
```

平板与 Windows 切流见 [CUTOVER.md](CUTOVER.md).

## Environment

| Variable | Default | Purpose |
|----------|---------|---------|
| `MODELS_HOST_PATH` | `~/opt/models` | Host model directory mount |
| `VLLM_BASE_URL` | `http://vllm-qwen36-35b:8088/v1` | Gateway → vLLM |
| `VLLM_MODEL` | `qwen36-35b-nvfp4` | Served model name |
| `C2_BASE_URL` | `http://192.168.1.10:8000` | Tablet config |

## Layer boundary

Both containers are **C2/C1 inference** only. No A⁺, LangGraph, or Buddy business logic.
