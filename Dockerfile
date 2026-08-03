# syntax=docker/dockerfile:1

# Pin uv's own image alongside the local dev version (see `uv --version`) so
# builds stay reproducible; bump both together.
FROM ghcr.io/astral-sh/uv:0.12.1 AS uv

#####################################################################
# Stage 1: resolve & install Python deps into a venv (no OS packages
# needed here — this stage never runs Playwright, only `uv sync`).
#####################################################################
FROM python:3.14-slim AS builder

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Install dependencies in a layer keyed only on the lockfile, so editing
# application code below doesn't invalidate/re-download the whole venv.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project

COPY . /app
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen

#####################################################################
# Stage 2: runtime image
#####################################################################
FROM python:3.14-slim AS runtime

COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN groupadd --system app \
    && useradd --system --gid app --home-dir /app --no-create-home app

WORKDIR /app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app . /app

# Chromium's OS libraries must be installed here (not in the builder) because
# this installs real system packages, not files copyable between stages. Only
# the headless-shell build is fetched (not full Chromium/firefox/webkit) since
# the app only ever launches headless(); chown happens in this same layer so
# it doesn't force a copy-up of these files into a separate layer later.
RUN /app/.venv/bin/python -m playwright install --with-deps --only-shell chromium \
    && rm -rf /var/lib/apt/lists/* \
    && chown -R app:app /ms-playwright

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/docs', timeout=3)" || exit 1

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8000"]