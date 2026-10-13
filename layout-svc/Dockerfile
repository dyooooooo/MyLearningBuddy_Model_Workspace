# 版面服务 — reuses the buddy-api image for aarch64 torch + transformers.
# Only torchvision (aligned with the base torch) and opencv-headless are added.
FROM learning-buddy-c2-buddy-api:latest

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HOME=/tmp/hf \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    CUDA_VISIBLE_DEVICES="" \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt /tmp/layout-requirements.txt
RUN pip install --no-cache-dir --no-deps torchvision==0.27.1 \
    && pip install --no-cache-dir opencv-python-headless==5.0.0.93

COPY server.py /app/layout_server.py

EXPOSE 8504

HEALTHCHECK --interval=30s --timeout=10s --start-period=90s --retries=5 \
    CMD curl -sf http://127.0.0.1:8504/health || exit 1

CMD ["uvicorn", "layout_server:app", "--host", "0.0.0.0", "--port", "8504"]
