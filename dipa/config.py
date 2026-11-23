"""Central configuration for dip-args. Every value can be overridden with an env var of the same
name (or a .env file in the project root). Names shared with dip-memory keep their DIP_ prefix so
the same .env lines work in both."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name, default, cast=str):
    val = os.getenv(name)
    return cast(val) if val is not None else default


# --- paths -----------------------------------------------------------------
DATA_DIR = Path(_env("DIP_DATA_DIR", str(ROOT / "data")))
DB_PATH = Path(_env("DIP_DB_PATH", str(DATA_DIR / "args.sqlite")))
WIKI_DIR = Path(_env("DIP_WIKI_DIR", str(DATA_DIR / "wiki")))
SOURCES_DIR = Path(
    _env("DIP_SOURCES_DIR", str(DATA_DIR / "sources"))
)  # snapshots of fetched web articles
IMAGE_DIR = Path(
    _env(
        "DIPA_IMAGE_DIR",
        str(
            ROOT.parent.parent / "00_Data" / "SD-Corpus-v1-small" / "ingest" / "images"
        ),
    )
)
IMAGE_WIKI_DIR = Path(
    _env("DIPA_IMAGE_WIKI_DIR", str(WIKI_DIR / "images"))
)  # one page per image (stage 10)
IMAGE_CONCEPTS = _env(
    "DIP_IMAGE_CONCEPTS", 4, int
)  # abstract concepts drawn from a node for metaphor search
REPORT_DIR = Path(_env("DIP_REPORT_DIR", str(DATA_DIR / "reports")))

# --- models (all local) ----------------------------------------------------
OFFLINE = _env(
    "DIP_OFFLINE", False, lambda v: v.strip().lower() in ("1", "true", "yes")
)
OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434")
LLM_MODEL = _env("DIP_LLM_MODEL", "qwen3:14b")
VLM_MODEL = _env("DIP_VLM_MODEL", "qwen2.5vl:3b")  # parse.py only, for image items
PDF_PARSER = _env("DIP_PDF_PARSER", "auto").strip().lower()
PDF_FALLBACK_RATIO = _env("DIP_PDF_FALLBACK_RATIO", 0.5, float)
EMBED_BACKEND = _env("DIP_EMBED_BACKEND", "remote")
EMBED_MULTIMODAL = EMBED_BACKEND in ("st", "remote")
EMBED_MODEL = _env(
    "DIP_EMBED_MODEL",
    "google/embeddinggemma-2" if EMBED_MULTIMODAL else "nomic-embed-text",
)
EMBED_URL = _env("DIP_EMBED_URL", "http://localhost:11500")
EMBED_TOKEN = _env("DIP_EMBED_TOKEN", "")
EMBED_DIM = _env("DIP_EMBED_DIM", 768, int)
EMBED_DEVICE = _env("DIP_EMBED_DEVICE", "cpu")
LLM_TIMEOUT = _env("DIP_LLM_TIMEOUT", 600.0, float)
LLM_NUM_CTX = _env("DIP_LLM_NUM_CTX", 8192, int)
EDIT_TOKEN = _env(
    "DIP_EDIT_TOKEN", ""
)  # if set, saving wiki pages needs it (X-Edit-Token header)
ISSUE_COUNT = _env("DIP_ISSUE_COUNT", 0, int)  # 0 = about 1.2 * sqrt(arguments)
THEME_COUNT = _env("DIP_THEME_COUNT", 0, int)  # 0 = about sqrt(issues)
# Concurrent LLM requests. Only faster if the Ollama server runs with OLLAMA_NUM_PARALLEL >= this.
LLM_PARALLEL = _env("DIP_LLM_PARALLEL", 1, int)
# Extraction answers are long lists: allow more output than dip-memory's 1500.
LLM_MAX_TOKENS = _env("DIP_LLM_MAX_TOKENS", 3000, int)

# --- passages ----------------------------------------------------------------
CHUNK_MIN_CHARS = _env("DIP_CHUNK_MIN", 300, int)
CHUNK_MAX_CHARS = _env("DIP_CHUNK_MAX", 800, int)

# --- extraction ----------------------------------------------------------------
# One extraction window = consecutive passages up to this many characters (~1,100 tokens).
# Input + cast list + output must fit LLM_NUM_CTX.
WINDOW_CHARS = _env("DIPA_WINDOW_CHARS", 4500, int)
# The cast-list stage reads the document in blocks of this size (map), then merges (reduce).
CAST_BLOCK_CHARS = _env("DIPA_CAST_BLOCK_CHARS", 14000, int)
# A quote counts as verified when its fuzzy match against the source scores at least this (0-100).
QUOTE_MIN_SCORE = _env("DIPA_QUOTE_MIN_SCORE", 90, float)


print(f"{DATA_DIR=}")
print(f"{DB_PATH=}")
print(f"{WIKI_DIR=}")
print(f"{SOURCES_DIR=}")
print(f"{IMAGE_DIR=}")
print(f"{IMAGE_WIKI_DIR=}")
print(f"{IMAGE_CONCEPTS=}")
print(f"{REPORT_DIR=}")
print(f"{OFFLINE=}")
print(f"{OLLAMA_URL=}")
print(f"{LLM_MODEL=}")
print(f"{VLM_MODEL=}")
print(f"{PDF_PARSER=}")
print(f"{PDF_FALLBACK_RATIO=}")
print(f"{EMBED_BACKEND=}")
print(f"{EMBED_MULTIMODAL=}")
print(f"{EMBED_MODEL=}")
print(f"{EMBED_URL=}")
print(f"{EMBED_TOKEN=}")
print(f"{EMBED_DIM=}")
print(f"{EMBED_DEVICE=}")
print(f"{LLM_TIMEOUT=}")
print(f"{LLM_NUM_CTX=}")
print(f"{EDIT_TOKEN=}")
print(f"{ISSUE_COUNT=}")
print(f"{THEME_COUNT=}")
print(f"{LLM_PARALLEL=}")
print(f"{LLM_MAX_TOKENS=}")
print(f"{CHUNK_MIN_CHARS=}")
print(f"{CHUNK_MAX_CHARS=}")
print(f"{WINDOW_CHARS=}")
print(f"{CAST_BLOCK_CHARS=}")
print(f"{QUOTE_MIN_SCORE=}")
