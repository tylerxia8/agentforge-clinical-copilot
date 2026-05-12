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

# evals/ holds the W2 eval-suite cases AND the W3 platform's
# redteam_runs/ + adversarial_findings/ data. The /adversarial
# visibility page reads these at runtime. Placed AFTER pip install
# so the install-time discovery (setuptools find_packages under
# src/) isn't distracted by the eval-suite tree.
COPY evals/ ./evals/

EXPOSE 8000

# Container-level liveness probe. Railway's restart policy uses this to
# kill a stuck process and reschedule it. The /healthz endpoint is a
# trivial 200 returning {"status":"ok"} — if uvicorn is wedged, curl
# fails and the container exits non-zero, triggering ON_FAILURE.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4).status == 200 else 1)" || exit 1

CMD ["uvicorn", "copilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
