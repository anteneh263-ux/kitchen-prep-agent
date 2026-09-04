# Cloud Run image for Kitchen Prep Agent.
#
# Serves the existing FastAPI app (kitchen_prep/server.py) with Uvicorn on the
# port Cloud Run supplies via $PORT. No application behaviour is changed here.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Dependencies first so the layer is cached across source-only changes.
COPY requirements.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

# Copy only what the service needs. Everything else (.env, .git, .venv, out/,
# caches, tests, local secrets) is excluded both by these explicit paths and by
# .dockerignore.
COPY kitchen_prep/ ./kitchen_prep/
COPY scripts/ ./scripts/

# The deterministic fallback forecast reads data/sales_history.csv, which is
# gitignored and regenerated from a fixed seed (see scripts/generate_sales_history.py).
# Generating it at build time keeps the image self-contained and reproducible.
RUN python scripts/generate_sales_history.py \
    && python scripts/verify_data.py

# Run unprivileged. /app must stay writable so the local JSON store can create
# out/ when KP_STORE is not set to "firestore".
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8080

# sh -c so Cloud Run's $PORT expands; exec so uvicorn is PID 1 and gets SIGTERM.
CMD ["sh", "-c", "exec uvicorn kitchen_prep.server:app --host 0.0.0.0 --port ${PORT:-8080}"]
