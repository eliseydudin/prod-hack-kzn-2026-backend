FROM alpine:3.24 AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
  UV_LINK_MODE=copy \
  UV_NO_DEV=1 \
  UV_PYTHON_INSTALL_DIR=/python \
  UV_PYTHON_PREFERENCE=only-managed 

RUN uv python install 3.14

WORKDIR /app

COPY pyproject.toml uv.lock ./

RUN --mount=type=cache,target=/root/.cache/uv \
  --mount=type=bind,source=uv.lock,target=uv.lock \
  --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
  uv sync --locked --no-install-project

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
  uv sync --locked

FROM alpine:3.24 AS production

RUN --mount=type=cache,target=/var/cache/apk \
  apk add curl bash tzdata

RUN addgroup --system --gid 1001 nonroot
RUN adduser --system \ 
  -G nonroot \ 
  -u 1001 \
  -s /bin/bash \
  nonroot

COPY --from=builder /python /python
COPY --from=builder --chown=nonroot:nonroot /app /app

ENV PATH="/app/.venv/bin:$PATH" \
  PYTHONUNBUFFERED=1

USER nonroot

HEALTHCHECK --interval=1m30s --timeout=10s --start-period=40s \
  CMD curl -fsS http://localhost:8000/health || exit 1

WORKDIR /app

CMD ["fastapi", "run", "--host", "0.0.0.0", "--port", "8000", "app/main.py"]
