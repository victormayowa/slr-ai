FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY --from=ghcr.io/astral-sh/uv:0.8.2 /uv /usr/local/bin/uv

# curl and bzip2 fetch micromamba for the analysis tools; ca-certificates for outbound HTTPS.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl bzip2 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 app \
    && install -d -o app /app /opt/omnireview /data/documents /data/benchmarks
WORKDIR /app
USER app

# Statistics (R with metafor, meta, netmeta, ...) and manuscript export (Pandoc, Tectonic, rsvg-convert), installed from
# conda-forge without compilers. They add about 2 GB; build with --build-arg WITH_ANALYSIS_TOOLS=false for a smaller
# image when analyses and PDF export aren't needed (the app then says they're unavailable).
ARG WITH_ANALYSIS_TOOLS=true
COPY --chown=app scripts/setup_r_env.sh scripts/setup_publishing_tools.sh /app/scripts/
COPY --chown=app stats/environment.yml /app/stats/environment.yml
RUN if [ "$WITH_ANALYSIS_TOOLS" = "true" ]; then \
        OMNIREVIEW_R_PREFIX=/opt/omnireview/r /app/scripts/setup_r_env.sh \
        && OMNIREVIEW_TOOLS_PREFIX=/opt/omnireview/tools /app/scripts/setup_publishing_tools.sh \
        && rm -rf /home/app/.cache /home/app/micromamba/pkgs; \
    fi
ENV RSCRIPT_PATH=/opt/omnireview/r/bin/Rscript \
    PANDOC_PATH=/opt/omnireview/tools/bin/pandoc \
    TECTONIC_PATH=/opt/omnireview/tools/bin/tectonic \
    RSVG_CONVERT_PATH=/opt/omnireview/tools/bin/rsvg-convert \
    DOCUMENT_STORAGE_DIR=/data/documents \
    BENCHMARK_DATASETS_DIR=/data/benchmarks

COPY --chown=app pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY --chown=app . .

EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
