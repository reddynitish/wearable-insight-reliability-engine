# Reproducible demo image. Runs the API and the before/after demo page.
#
# Deliberately carries no credentials and no personal data: the engine makes no network
# call, and google_health/ (the OAuth client) and data exports are excluded by
# .dockerignore. Everything the container can decide, it decides from the request body.
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY engine/ ./engine/
COPY eval/ ./eval/
COPY tools/ ./tools/
COPY docs/ ./docs/
COPY data/DATASETS.md ./data/DATASETS.md
COPY README.md PROJECT_BRIEF.md ./

# Run as a non-root user: nothing here needs write access to anything.
RUN useradd --create-home --uid 10001 engine && chown -R engine:engine /app
USER engine

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s \
    CMD python -c "import urllib.request,sys; \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/v1/health').status==200 else 1)"

CMD ["uvicorn", "engine.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
