# uv, from its official image (Dependabot bumps the version and the digest).
FROM ghcr.io/astral-sh/uv:0.13.0@sha256:cdc6093146eb3ff6a40107b38f008b789e050e77ad87865e381d9917da55a168 AS uv

# The Python every stage below starts from, pinned by digest so that every build gets the same
# image; Dependabot opens a pull request when python:3.12-slim is rebuilt (#7). CI's test-python
# job runs the tests on newer Pythons by replacing this stage:
# docker build --build-context base=docker-image://python:3.13-slim
FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1 AS base

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
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1

# The app runs as an unprivileged user, uid 10001, which owns the SQLite directory. A new volume
# mounted there takes that ownership; docker-compose.yml's data-permissions service hands a volume
# that an older, root-run image created over to it.
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin app \
    && mkdir -p /app/data \
    && chown 10001:10001 /app/data
USER 10001

EXPOSE 8000

# Unhealthy while /health fails or answers with an error status.
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5)"]

CMD ["uvicorn", "hunter_bargain.main:app", "--host", "0.0.0.0", "--port", "8000"]
