# MFR side service — reuses the buddy-api base image so we inherit a working
# aarch64 + CUDA torch build, pillow, numpy, fastapi and uvicorn without
# re-downloading them.  Only the ONNX-runtime stack + a pinned transformers
# (compatible with pix2text-mfr's sentencepiece tokenizer) are added on top.
FROM learning-buddy-c2-buddy-api:latest

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HOME=/root/.cache/huggingface \
    MFR_MODEL=breezedeus/pix2text-mfr \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /tmp/mfr-requirements.txt
RUN pip install --no-cache-dir -r /tmp/mfr-requirements.txt

COPY server.py /app/mfr_server.py

# Bake the model weights into the image so startup needs no network and the
# service is reproducible even if hf-mirror is briefly unreachable.
RUN python -c "from optimum.onnxruntime import ORTModelForVision2Seq; from transformers import AutoTokenizer; m='breezedeus/pix2text-mfr'; AutoTokenizer.from_pretrained(m); ORTModelForVision2Seq.from_pretrained(m, use_cache=False, use_io_binding=False)"

EXPOSE 8503

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -sf http://127.0.0.1:8503/health || exit 1

CMD ["uvicorn", "mfr_server:app", "--host", "0.0.0.0", "--port", "8503"]
