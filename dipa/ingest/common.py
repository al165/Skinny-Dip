"""Shared ingest helpers: logging and a cached, timed LLM call."""
import sys
import threading
import time

from dipa import config, db, llm

HOLDER_TYPES = ["author", "person", "org", "government", "industry", "field", "public", "work", "unspecified"]
KINDS = ["position", "fact", "example", "recommendation", "definition", "question"]


_db_lock = threading.Lock()  # ask() is called from worker threads; sqlite access is serialised


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def ask(conn, task: str, prompt: str, schema: dict, system: str | None = None,
        temperature: float = 0.1, max_tokens: int | None = None) -> tuple[dict, float, bool]:
    """LLM call with a JSON schema, cached by (task, prompt, system, schema, model, and max_tokens
    if given). `max_tokens` lowers the output cap. Returns (answer, seconds, cached)."""
    model = config.LLM_MODEL
    payload = {"prompt": prompt, "system": system, "schema": schema, "temperature": temperature}
    if max_tokens:  # only when set, so existing cache keys stay valid
        payload["max_tokens"] = max_tokens
    with _db_lock:
        h, hit = db.cache_get(conn, task, payload, model)
    if hit is not None:
        if isinstance(hit, dict) and hit.get("_overflow"):
            raise llm.LLMError(f"Model hit the token cap (cached: {hit['_overflow']})")
        return hit, 0.0, True
    t0 = time.time()
    try:
        out = llm.generate(prompt, schema=schema, system=system, temperature=temperature, max_tokens=max_tokens)
    except llm.LLMError as e:
        # an answer that overflows the output cap is cached too, so reruns go straight to the
        # caller's fallback (s3 splits the window) instead of re-asking, and stay deterministic
        if "token cap" in str(e):
            with _db_lock:
                db.cache_put(conn, task, h, model, {"_overflow": str(e)[:200]}, time.time() - t0)
        raise
    secs = time.time() - t0
    with _db_lock:
        db.cache_put(conn, task, h, model, out, secs)
    return out, secs, False


def fmt_secs(s: float) -> str:
    s = int(s)
    return f"{s // 3600}h{s % 3600 // 60:02d}m" if s >= 3600 else f"{s // 60}m{s % 60:02d}s"


def ask_all(conn, task: str, prompts: list[str], schema: dict, system: str | None = None) -> list[tuple]:
    """ask() for independent prompts, config.LLM_PARALLEL at a time; results in prompt order.
    Write to the database only after it returns (or under _db_lock)."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=max(1, config.LLM_PARALLEL)) as pool:
        return list(pool.map(lambda p: ask(conn, task, p, schema, system=system), prompts))

