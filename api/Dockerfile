# DentisAI ML/AI API — slim, non-root, no secrets baked in.
#
# The screening pipeline reuses the Python REFERENCE engines in sibling folders
# (uv path dependencies ../triage and ../recommender, plus ml/detection/quality_gate.py),
# so the build context is the backend/ ROOT, not backend/api:
#
#   docker build -f backend/api/Dockerfile -t dentisai-api backend
#   docker run --rm -p 8000:8000 --env-file backend/.env dentisai-api
#
# Context filtering: backend/api/Dockerfile.dockerignore (BuildKit, Docker >= 23) admits
# only the files listed there. All configuration comes from environment variables at RUN
# time; nothing from backend/.env is copied.

FROM ghcr.io/astral-sh/uv:0.12.7 AS uv

FROM python:3.12-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/srv/backend/api/.venv
WORKDIR /srv/backend
# Reference engines + the rule artefacts they read at run time (same layout as the repo,
# because both packages locate their files relative to their source folder).
COPY triage/pyproject.toml triage/rules.yaml triage/schema.json triage/
COPY triage/src triage/src
COPY triage/dist triage/dist
COPY triage/tests/cases.json triage/tests/cases.json
COPY recommender recommender
COPY ml/detection/quality_gate.py ml/detection/quality_gate.yaml ml/detection/
COPY docs/style/forbidden_phrases.md docs/style/forbidden_phrases.md
COPY api/pyproject.toml api/uv.lock api/
WORKDIR /srv/backend/api
# `vision` extra = numpy + Pillow for the quality-gate stage of /v1/screening/analyze.
RUN uv sync --frozen --no-dev --extra vision
COPY api/app app

FROM python:3.12-slim AS runtime
ENV PATH="/srv/backend/api/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app
# Everything root-owned and read-only for the app user.
COPY --from=build --chown=root:root /srv/backend /srv/backend
WORKDIR /srv/backend/api
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
# --no-access-log: uvicorn's access log prints raw paths/query strings; the app
# logs route templates instead. --no-server-header: no version disclosure.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--no-server-header"]
