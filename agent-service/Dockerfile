# Slim Python image — agent service is I/O-bound, not CPU-bound.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# pyproject.toml's setuptools config does packages.find under src/, so
# the src tree has to exist BEFORE pip install -e . runs (otherwise
# setuptools errors with "egg_base 'src' does not exist").
COPY pyproject.toml ./
COPY src/ ./src/

RUN pip install --upgrade pip && pip install -e .

EXPOSE 8000

CMD ["uvicorn", "copilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
