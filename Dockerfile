# Drive Dedupe — web UI in a container.
#
#   docker build -t gdrive-dedupe .
#   docker run --rm -p 127.0.0.1:8765:8765 -p 127.0.0.1:8766:8766 \
#       -v gdrive-dedupe-data:/data gdrive-dedupe
#
# Then open the URL printed in the logs (it carries a one-time access token).
# Port 8766 receives Google's OAuth redirect; keep both ports bound to
# 127.0.0.1 on the host unless you know why you need otherwise.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /bin/
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --no-install-project
COPY gdrive_dedupe ./gdrive_dedupe
RUN uv sync --locked --no-dev

FROM python:3.12-slim
RUN useradd --create-home --uid 1000 dedupe \
 && mkdir -p /data && chown dedupe:dedupe /data
COPY --from=build --chown=dedupe:dedupe /app /app
USER dedupe
WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    GDRIVE_DEDUPE_HOME=/data \
    GDRIVE_DEDUPE_HOST=0.0.0.0 \
    GDRIVE_DEDUPE_OAUTH_PORT=8766 \
    GDRIVE_DEDUPE_OAUTH_BIND=0.0.0.0 \
    GDRIVE_DEDUPE_NO_BROWSER=1
VOLUME ["/data"]
EXPOSE 8765 8766
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8765/').status==200 else 1)"
ENTRYPOINT ["gdrive-dedupe"]
CMD ["web", "--no-browser"]
