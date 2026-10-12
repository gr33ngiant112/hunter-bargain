# uv, from its official image (Dependabot bumps the version).
FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

# The Python every stage below starts from. CI's test-python job runs the tests on newer Pythons
# by replacing this stage: docker build --build-context base=docker-image://python:3.13-slim
FROM python:3.12-slim AS base

# The app and the dependency versions in uv.lock, without the dev tools, in /app/.venv. The app is
# installed as a package, not as a link to the source.
FROM base AS build
COPY --from=uv /uv /usr/local/bin/uv
# Compile bytecode at install, as pip did. Use this image's Python, which the runtime stage has at
# the same path, and never download one.
ENV UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY README.md ./
COPY src/ src/
RUN uv sync --frozen --no-dev --no-editable

# The build stage plus the dev tools and the tests: CI's lint, type, test and migration checks
# run in this image (docker build --target test .). --locked fails the build when uv.lock is out
# of date with pyproject.toml.
FROM build AS test
RUN uv sync --locked --extra dev --no-editable
COPY alembic.ini CHANGELOG.md .env.example ./
COPY tests/ tests/
ENV PATH="/app/.venv/bin:$PATH"

# The tests on the lowest version pyproject.toml allows of each direct dependency, not uv.lock
# (CI's test-lowest-direct job), so the declared floors stay installable and tested.
FROM base AS test-lowest-direct
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml README.md CHANGELOG.md .env.example ./
COPY src/ src/
COPY tests/ tests/
RUN uv venv && uv pip install --resolution lowest-direct -e ".[dev]"
ENV PATH="/app/.venv/bin:$PATH"

# The app image. It comes last, so `docker build .` and `docker compose build` build it.
FROM base AS runtime
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Create data directory for SQLite
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["uvicorn", "hunter_bargain.main:app", "--host", "0.0.0.0", "--port", "8000"]
