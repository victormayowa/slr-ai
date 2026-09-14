FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.8.2 /uv /usr/local/bin/uv

RUN useradd --create-home --uid 10001 app
WORKDIR /app
RUN chown app /app
USER app

COPY --chown=app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY --chown=app . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
