FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Core is an IPP client only; cupsd and printer drivers remain on the print server.
RUN apt-get update \
    && apt-get install --no-install-recommends -y cups-client \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY alembic.ini ./
COPY migrations ./migrations

RUN uv sync --no-dev --frozen

EXPOSE 8000

CMD ["uv", "run", "fastapi", "run", "src/core/main.py", "--host", "0.0.0.0", "--port", "8000"]
