# Slim Python image — agent service is I/O-bound, not CPU-bound.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so Docker can cache the layer.
COPY pyproject.toml ./
RUN pip install --upgrade pip && \
    pip install -e .

COPY src/ ./src/

EXPOSE 8000

CMD ["uvicorn", "copilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
