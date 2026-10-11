# Huawei Health MCP — read-only MCP server for Huawei Health (protocol credits: NOTICE). Dependencies are pinned by uv.lock.
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
WORKDIR /src
COPY pyproject.toml uv.lock ./
RUN UV_PROJECT_ENVIRONMENT=/venv UV_PYTHON_DOWNLOADS=never \
    uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN UV_PROJECT_ENVIRONMENT=/venv UV_PYTHON_DOWNLOADS=never \
    uv sync --frozen --no-dev --no-editable --compile-bytecode

# `docker build --target test .` runs the test suite (this stage is not part of the runtime image)
FROM build AS test
COPY tests ./tests
RUN UV_PROJECT_ENVIRONMENT=/venv UV_PYTHON_DOWNLOADS=never uv sync --frozen \
 && /venv/bin/pytest -q

FROM python:3.12-slim AS runtime
COPY --from=build /venv /venv
RUN useradd -u 10001 -m app && mkdir /data && chown app:app /data
USER app
ENV PATH=/venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    HOME=/data \
    HUAWEI_TOKEN_FILE=/data/tokens.json \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000
EXPOSE 8000
ENTRYPOINT ["huawei-mcp"]
