"""Pre-compute everything the API's moves need, so a deployed demo works without models.

    DIP_DATA_DIR=data-full python -m dipa.warm        # needs Ollama + the embedding server, once

Every move works from the database's caches when the models can't be reached (DIP_OFFLINE=1, or a
server outside the tailnet): neighbours, counter, evidence, zoom, sources, holders, the wiki, and
the Images move (subject and metaphor). This fills those caches for every argument, issue, theme
and visible passage:
  - embeddings of their texts (search, neighbours, the Images subject route),
  - the Images move's abstract concepts (LLM) and their query embeddings (metaphor route),
  - the image library's description, metaphor and picture vectors.
Only free-text search needs a live model: offline it falls back to keyword matching.
The anchor texts come from dipa.api itself, so the cache keys are exactly the ones the API uses.
"""
import time
from concurrent.futures import ThreadPoolExecutor

from dipa import api, config, db, images
from dipa.ingest.common import fmt_secs, log


def main():
    conn = db.connect()
    t0 = time.time()
    args = api._arg_rows(conn)
    nodes = conn.execute("SELECT * FROM nodes WHERE kind IN ('issue','theme') AND summary IS NOT NULL").fetchall()
    passages = conn.execute("SELECT * FROM passages WHERE hidden=0").fetchall()

    # embeddings: search texts, holder-free texts, issue/theme texts, passages
    db.embed_cached(conn, [api._search_text(r) for r in args + nodes])
    db.embed_cached(conn, api._arg_texts(conn))
    db.embed_cached(conn, [p["text"] for p in passages])
    images.corpus_centroid(conn, lambda: api._arg_texts(conn))
    images._library(conn)  # description, metaphor and picture vectors
    log(f"warm: embeddings for {len(args)} arguments, {len(nodes)} issues/themes, {len(passages)} passages "
        f"({fmt_secs(time.time() - t0)})")

    # the Images move's concept text for each anchor, exactly as move_images builds it
    texts = [r["summary"] for r in args if r["summary"]]
    texts += [api._anchor_text(r["kind"], r) for r in nodes]
    texts += [p["text"] for p in passages]
    t1 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, config.LLM_PARALLEL)) as pool:
        found = list(pool.map(lambda t: images.concepts(conn, t), texts))
    concepts = sorted({c for cs, how in found if how == "llm" for c in cs})
    db.embed_cached(conn, concepts, query=True)
    fallback = sum(1 for _, how in found if how != "llm")
    log(f"warm: concepts for {len(texts)} anchors ({len(concepts)} distinct, {fallback} without the LLM) "
        f"in {fmt_secs(time.time() - t1)}")
    log(f"warm: done in {fmt_secs(time.time() - t0)}. The demo now runs with DIP_OFFLINE=1 "
        f"(free-text search falls back to keywords).")


if __name__ == "__main__":
    main()
