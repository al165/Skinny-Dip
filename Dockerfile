# dip-args API. The database + wiki (DIP_DATA_DIR) and the image files (DIPA_IMAGE_DIR) are mounted
# at run time (compose.yaml), not baked in.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /dipa

COPY requirements-prod.txt .
RUN pip install --no-cache-dir -r requirements-prod.txt

COPY dipa/ ./dipa/

# Data locations inside the container (see the volumes in compose.yaml)
ENV DIP_DATA_DIR=/dipa/data \
    DIPA_IMAGE_DIR=/dipa/images \
    DIPA_IMAGE_WIKI_DIR=/dipa/data/wiki/images

# Must name the models the build used: cached LLM answers and embeddings are keyed by model name.
ENV DIP_LLM_MODEL=qwen3:14b \
    DIP_EMBED_BACKEND=remote \
    DIP_EMBED_MODEL=google/embeddinggemma-2

# Defaults for a public demo: no models (run `python -m dipa.warm` on the build first), and one
# request at a time. compose.yaml can override them.
ENV DIP_OFFLINE=1 \
    DIP_LLM_PARALLEL=1 \
    DIP_EDIT_TOKEN=

# Not root. uid 1000 so the mounted database and wiki stay writable (wiki edits save files).
RUN useradd --uid 1000 --no-create-home dipa
USER 1000

EXPOSE 8000
CMD ["uvicorn", "dipa.api:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*", "--workers", "1"]
