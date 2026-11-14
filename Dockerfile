FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY services ./services
RUN pip install --no-cache-dir ".[xlsx]" && python -m playwright install --with-deps chromium

ENV PYTHONUNBUFFERED=1 PYTHONUTF8=1
EXPOSE 8000
CMD ["python", "-m", "services.autospider_mcp.server"]
