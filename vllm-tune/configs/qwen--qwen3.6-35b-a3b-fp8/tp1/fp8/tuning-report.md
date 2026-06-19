# FP8 Dense GEMM Tuning Report

Generated: 2026-06-15T14:47:33+00:00

## Configuration

| Parameter | Value |
|-----------|-------|
| Model | `Qwen/Qwen3.6-35B-A3B-FP8` |
| TP size | 1 |
| Container | vllm_tune_qwen--qwen3.6-35b-a3b-fp8 |
| Total time | 4m 55s |

## Results

| Shape (N,K) | Status | Time |
|--------------------|--------|------|
| 512,2048 | ❌ Failed | 4m 47s |
| 2048,512 | ❌ Failed | 2s |
| 2048,4096 | ❌ Failed | 2s |
| 5120,2048 | ❌ Failed | 2s |
| 8192,2048 | ❌ Failed | 2s |

## Summary

- **Succeeded:** 0/5
- **Failed:** 5/5 (512,2048 2048,512 2048,4096 5120,2048 8192,2048)

## Re-run failed

```bash
./tune-fp8.sh --shapes 512,2048 2048,512 2048,4096 5120,2048 8192,2048 --batch-size 1 2 4 8 16 24 32 48 64 96 128 256 512 1024 1536 2048 3072 4096
```
