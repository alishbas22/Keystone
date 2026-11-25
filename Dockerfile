# Keystone API image (Python 3.12, dependencies installed with uv from uv.lock)
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.9 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 1) Install dependencies only (cached unless pyproject.toml / uv.lock change)
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project

# 2) Install Keystone itself
COPY README.md LICENSE ./
COPY src ./src
COPY ontology ./ontology
RUN uv sync --locked --no-dev

EXPOSE 8000
CMD ["uvicorn", "keystone.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
