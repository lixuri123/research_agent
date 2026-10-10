FROM node:22-alpine AS frontend

WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS runtime

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH="/opt/playwright" \
    BROWSER_BACKEND="chromium"

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --extra browser --no-dev --no-install-project
RUN uv run --no-sync playwright install --with-deps chromium

COPY research_agent/ research_agent/
COPY scripts/ scripts/
COPY data/eval_dataset.json data/eval_dataset.json
COPY --from=frontend /build/frontend/dist frontend/dist/
RUN uv sync --frozen --extra browser --no-dev

EXPOSE 8000

CMD ["uvicorn", "research_agent.api:app", "--host", "0.0.0.0", "--port", "8000"]
