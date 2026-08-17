# The `api` service. Same image would serve `worker`; only the start command
# differs, which is why the CMD lives here and not in the build.
#
# Python 3.12 because pyproject pins >=3.12,<3.13 — the generated agents and the
# runtime they import are the same interpreter.
FROM python:3.12-slim

# uv, copied from its own image rather than pip-installed: it is a static binary
# and this is the documented way to get it into a container.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Dependencies before source, so editing a module does not re-resolve the lock.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src/ src/
COPY db/ db/
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# Shell form on purpose: Railway supplies $PORT at run time and the exec form
# would pass the literal string. The fallback keeps `docker run` working locally.
CMD uvicorn meridian.api.main:app --host 0.0.0.0 --port ${PORT:-8000}
