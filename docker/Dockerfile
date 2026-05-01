# --- Stage 1: build ---
FROM python:3.12-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY src/ src/

RUN pip install --no-cache-dir --target=/deps .

# --- Stage 2: runtime ---
FROM python:3.12-slim AS runtime

# Cloud Run sets PORT env var
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Copy installed packages
COPY --from=builder /deps /usr/local/lib/python3.12/site-packages

# Copy source
COPY src/ src/

# Copy model artifacts (add to .dockerignore if using GCS)
COPY artifacts/ artifacts/

# Copy pre-computed data (optional -- can mount via GCS FUSE)
COPY data/processed/ data/processed/

EXPOSE 8000

# Cloud Run injects $PORT; default to 8000
CMD ["python", "-m", "uvicorn", \
     "hotel_curator.api.app:app", \
     "--host", "0.0.0.0", \
     "--port", "8000"]
