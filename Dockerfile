FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    g++ \
  && rm -rf /var/lib/apt/lists/*

# Copy project metadata for better caching
COPY backend/pyproject.toml backend/uv.lock* ./

# Install runtime dependencies only, honoring the lockfile
RUN uv sync --no-dev --frozen

# Expose the project venv on PATH
ENV VIRTUAL_ENV=/app/.venv
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

# Copy the backend application
COPY backend/ .

# Create non-root user and prepare directories
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/logs /app/data/databases && \
    : > /app/logs/rag_api.log && \
    chown -R appuser:appuser /app && \
    chmod -R 755 /app/logs && \
    chmod -R 755 /app/data

USER appuser

ENV PORT=8080

EXPOSE 8080

CMD uv run --no-sync uvicorn main:app --host 0.0.0.0 --port 8080 --workers 1 --log-level info
