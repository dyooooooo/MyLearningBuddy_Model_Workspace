FROM python:3.12-slim-bookworm

ENV PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn
ENV PYTHONUNBUFFERED=1
ENV HF_ENDPOINT=https://hf-mirror.com

WORKDIR /app

# Debian bookworm 国内镜像（加速 apt，尤其 nodejs/poppler 层）
RUN sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null \
    || sed -i 's|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list 2>/dev/null \
    || true

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl nodejs npm poppler-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt \
    && pip install 'setuptools>=69.0.0,<82'

COPY app/math/package.json app/math/plurimath_bridge.mjs ./app/math/
RUN cd app/math \
    && npm install --omit=dev --registry=https://registry.npmmirror.com

COPY app ./app
COPY configs ./configs
COPY data ./data
COPY scripts ./scripts

EXPOSE 8100

HEALTHCHECK --interval=30s --timeout=15s --start-period=120s --retries=5 \
    CMD curl -sf http://127.0.0.1:8100/api/v1/system/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8100"]
