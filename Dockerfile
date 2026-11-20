# VeriHire: one container serves the API (/api/v1) and the static frontend. Runs on Render's free plan (512 MB:
# the memory settings below keep it under that) or any Docker host; the host's $PORT is used. Settings come from
# environment variables (see .env.example).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app/ml \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    VERIHIRE_EMBED_BATCH=4 \
    MALLOC_ARENA_MAX=2 \
    INGEST_CONCURRENCY=1 \
    DB_POOL_MAX=4 \
    PORT=7860

# libgomp: OpenMP runtime needed by xgboost
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/* \
    && useradd -m -u 1000 user
WORKDIR /app

COPY backend/requirements.txt /tmp/req/backend.txt
COPY ml/requirements-ingest.txt ml/requirements-rank.txt /tmp/req/
RUN pip install -r /tmp/req/backend.txt -r /tmp/req/requirements-ingest.txt -r /tmp/req/requirements-rank.txt

COPY --chown=user . /app
USER user

# bake the BGE-small embedding model into the image, then never touch the network for it again
RUN python -m docint.embed.embedder
ENV VERIHIRE_EMBED_OFFLINE=1

EXPOSE 7860
# migrate.py is idempotent: tables, search function, RLS lockdown and the private 'resumes' storage bucket
CMD ["sh", "-c", "python scripts/migrate.py --supabase-storage && exec uvicorn app.main:app --app-dir backend --loop app.eventloop:selector_loop --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*'"]
