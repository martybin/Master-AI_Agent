FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN pip install --no-cache-dir uv && uv sync --frozen --no-dev \
    && useradd --create-home builder && mkdir -p /app/data && chown builder:builder /app/data
USER builder
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=3s CMD ["/app/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2)"]
CMD ["/app/.venv/bin/uvicorn", "master_agent.api:app", "--host", "127.0.0.1", "--port", "8000", "--workers", "1"]
