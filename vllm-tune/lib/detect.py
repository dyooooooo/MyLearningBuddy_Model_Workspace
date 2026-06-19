#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────
# lib/detect.py — Centralized model architecture and shape detection
# ─────────────────────────────────────────────────────────────────────
#
# Single source of truth for detecting:
#   - MoE vs dense architecture (num_local_experts, n_routed_experts, num_experts)
#   - FP8 GEMM (N,K) shapes from model config
#
# Runs inside a Docker container via:
#   docker exec $CONTAINER $INIT_WRAPPER python3 -c \
#       "$(cat lib/detect.py)" MODEL [--tp N] [--mode arch|shapes|all]
#
# Outputs a single JSON line to stdout. vLLM/HF log noise is filtered
# by the caller (grep '^{').
#
# Covers: QKV projections, attention output, linear attention (Mamba),
# shared experts, dense FFN, and MoE expert FFN (DeepSeek-V3/V4).
# ─────────────────────────────────────────────────────────────────────

import sys
import json
import argparse


def _get_text_config(model):
    """Load model config and resolve nested text_config."""
    from vllm.transformers_utils.config import get_config
    config = get_config(model=model, trust_remote_code=True)
    return getattr(config, 'text_config', config)


def detect_arch(model):
    """Return 'moe' or 'dense' based on expert count attributes."""
    tc = _get_text_config(model)
    E = (getattr(tc, 'num_local_experts', 0)
         or getattr(tc, 'n_routed_experts', 0)
         or getattr(tc, 'num_experts', 0))
    return 'moe' if E and int(E) > 0 else 'dense'


def detect_shapes(model, tp):
    """Auto-detect FP8 GEMM (N,K) shapes from model architecture.

    Returns a sorted list of (N, K) tuples covering:
      - Full-attention QKV projection
      - Attention output projection
      - Linear attention projections (Mamba-style, if present)
      - Shared expert gate+up and down projections
      - Dense FFN projections
      - MoE expert FFN projections (DeepSeek-V3/V4)
    """
    tc = _get_text_config(model)

    hidden_size = getattr(tc, 'hidden_size', None)
    if not hidden_size:
        return []

    num_heads = getattr(tc, 'num_attention_heads', 16)
    num_kv_heads = getattr(tc, 'num_key_value_heads', num_heads)
    head_dim = getattr(tc, 'head_dim', hidden_size // num_heads)
    shared_expert_size = getattr(tc, 'shared_expert_intermediate_size', None)
    intermediate_size = getattr(tc, 'intermediate_size', None)
    moe_intermediate_size = getattr(tc, 'moe_intermediate_size', None)

    shapes = set()

    # Full-attention QKV projection: (Q_dim + K_dim + V_dim) / TP
    q_dim = num_heads * head_dim
    kv_dim = num_kv_heads * head_dim
    qkv_out = (q_dim + 2 * kv_dim) // tp
    shapes.add((qkv_out, hidden_size))

    # Attention output: hidden_size, (num_heads * head_dim) / TP
    shapes.add((hidden_size, q_dim // tp))

    # Linear attention projections (Mamba-style, if present)
    lin_key_heads = getattr(tc, 'linear_num_key_heads', None)
    lin_val_heads = getattr(tc, 'linear_num_value_heads', None)
    lin_key_dim = getattr(tc, 'linear_key_head_dim', None)
    lin_val_dim = getattr(tc, 'linear_value_head_dim', None)
    if all(v is not None for v in [lin_key_heads, lin_val_heads, lin_key_dim, lin_val_dim]):
        lin_out = (lin_key_heads * lin_key_dim + lin_val_heads * lin_val_dim +
                   lin_key_heads * lin_key_dim)
        shapes.add((lin_out // tp, hidden_size))

    # Shared expert gate+up and down projections
    if shared_expert_size:
        shapes.add((shared_expert_size, hidden_size))
        shapes.add((hidden_size, shared_expert_size // tp))

    # Dense FFN (if present, non-MoE layers)
    if intermediate_size:
        shapes.add((intermediate_size // tp, hidden_size))
        shapes.add((hidden_size, intermediate_size // tp))

    # MoE expert FFN (DeepSeek-V3/V4 use moe_intermediate_size)
    if moe_intermediate_size:
        shapes.add((moe_intermediate_size, hidden_size))
        shapes.add((hidden_size, moe_intermediate_size // tp))

    return sorted(shapes)


def main():
    parser = argparse.ArgumentParser(
        description='vLLM-Tune: detect model architecture and FP8 shapes')
    parser.add_argument('model', help='HuggingFace model ID')
    parser.add_argument('--tp', type=int, default=1,
                        help='Tensor parallelism degree')
    parser.add_argument('--mode', choices=['arch', 'shapes', 'all'],
                        default='all', help='What to detect')
    args = parser.parse_args()

    result = {}

    if args.mode in ('arch', 'all'):
        result['arch'] = detect_arch(args.model)

    if args.mode in ('shapes', 'all'):
        detected = detect_shapes(args.model, args.tp)
        result['shapes'] = [f'{n},{k}' for n, k in detected]

    print(json.dumps(result))


if __name__ == '__main__':
    main()
