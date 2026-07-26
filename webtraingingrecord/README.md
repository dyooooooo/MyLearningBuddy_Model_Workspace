# WTR — 已迁出 Spark

**Web Training Record（培训复习）运行在 Windows 本机 WSL**，本目录不再包含 Spark 侧服务或前台代码。

## Spark 上还保留什么

WTR 只通过 C2 网关调用 **35B**，做章节目录与全文转化：

```text
WSL WTR → http://192.168.1.10:8000/v1  (Bearer VLLM_LAN_TOKEN)
         → vLLM Qwen3.6-35B-NVFP4
```

实现：`exam-ocr-server/app/openai_proxy.py`（挂在 `c2-gateway`）。

## 不要做的事

- 不要在 Spark 上再起 `:8300` WTR API / 控制台
- 不要把本目录当成可部署源码树（源码与前台在 WSL）
