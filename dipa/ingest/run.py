"""Ingest CLI.

    python -m dipa.ingest.run <files...>             # stages 1-4 per file, then 5-9 over the corpus
    python -m dipa.ingest.run https://... reading-list.urls folder/   # URLs, .urls lists, folders too
    python -m dipa.ingest.run <files...> --stages 3,4
    python -m dipa.ingest.run --stages 5,6,7,8,9     # corpus stages only (no files needed)
    python -m dipa.ingest.run --reset <files...>     # delete the database first
    python -m dipa.ingest.run --force <files...>     # re-parse unchanged files (new stage-1 rules)

Stages 1-4 (parse, cast, extract, verify) run per source. Stages 5-6 (merge into holders and
arguments, summarise) run once over the whole corpus, after the per-source stages.
"""
import argparse
import time
from pathlib import Path

from dipa import config, db, edits, images, llm, parse, wiki
from dipa.ingest import s1_parse, s2_cast, s3_extract, s4_verify, s5_merge, s6_summarise, s7_tree, s8_relations
from dipa.ingest.common import fmt_secs, log

PER_SOURCE = {1, 2, 3, 4}


def _read_url_list(path: Path) -> list[str]:
    """A `.urls` file: one URL per line; `#` starts a comment."""
    urls = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split(" #")[0].strip()
        if line and not line.startswith("#"):
            urls.append(line)
    return urls


def expand(sources: list[str]) -> list[str]:
    """Files, folders (recursively), http(s) URLs and `.urls` files (one URL per line)."""
    out = []
    for raw in sources:
        if parse.is_url(raw):
            out.append(raw)
            continue
        p = Path(raw)
        if p.suffix.lower() == ".urls" and p.is_file():
            out += _read_url_list(p)
        elif p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.suffix.lower() == ".urls":
                    out += _read_url_list(f)
                elif f.suffix.lower() in parse.LOADERS and not parse.is_source_file(f):
                    out.append(str(f))
        elif parse.is_source_file(p):
            continue  # metadata for the file next to it, read when that file is ingested
        elif p.exists():
            out.append(raw)
        else:
            raise SystemExit(f"not found: {p}")
    return list(dict.fromkeys(out))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sources", nargs="*")
    ap.add_argument("--stages", default="1,2,3,4,5,6,7,8,9,10")
    ap.add_argument("--recluster", action="store_true", help="stage 7: rebuild the issue/theme tree from scratch "
                                                              "(drops manual placements and edited issue texts)")
    ap.add_argument("--resummarise", action="store_true", help="stage 6: rewrite every argument summary "
                                                               "(default: only arguments without one)")
    ap.add_argument("--reset", action="store_true", help="delete the database (keeps nothing, not even the LLM cache)")
    ap.add_argument("--force", action="store_true", help="re-parse sources even if unchanged (after changing stage-1 rules); "
                                                           "unchanged passages still hit the LLM cache")
    a = ap.parse_args()
    stages = {int(s) for s in a.stages.split(",")}
    if stages & PER_SOURCE and not a.sources:
        ap.error(f"stages {sorted(stages & PER_SOURCE)} need source files")
    if a.reset and config.DB_PATH.exists():
        config.DB_PATH.unlink()
        log(f"deleted {config.DB_PATH}")
    status = llm.available()
    if not status.get("llm"):
        raise SystemExit(f"Ollama or {config.LLM_MODEL} not available at {config.OLLAMA_URL}: {status}")
    conn = db.connect()
    log(f"database {config.DB_PATH} · model {config.LLM_MODEL} at {config.OLLAMA_URL} · "
        f"{config.LLM_PARALLEL} parallel request(s)")
    t_all = time.time()
    sources = expand(a.sources) if stages & PER_SOURCE else []
    failed = []
    for src in sources:
        t0 = time.time()
        log(f"== {src}")
        if 1 in stages:
            try:
                item_id = s1_parse.ingest(conn, src, force=a.force)
            except (ValueError, OSError) as e:  # an unreachable page or bad file shouldn't stop the run
                log(f"  FAILED: {e}")
                failed.append(src)
                continue
        else:
            row = conn.execute("SELECT id FROM items WHERE path=?", (s1_parse.item_key(src),)).fetchone()
            item_id = row["id"] if row else None
        if item_id is None:
            log("  not in the database; run stage 1 first")
            continue
        if 2 in stages:
            s2_cast.run(conn, item_id)
            edits.apply(conn, item_id, ops=("author", "rename"))  # so extraction uses the corrected names
        if 3 in stages:
            s3_extract.run(conn, item_id)
            edits.apply(conn, item_id)
        if 4 in stages:
            s4_verify.run(conn, item_id)
        log(f"  done in {fmt_secs(time.time() - t0)}")
    if failed:
        log(f"{len(failed)} source(s) failed and were skipped: {', '.join(failed)}")
    if stages & {5, 6, 7, 8, 9, 10}:
        changed = wiki.sync(conn)  # people's wiki edits first, so the rebuild keeps them
        if changed:
            log(f"== wiki edits read: {changed}")
    if 5 in stages:
        t0 = time.time()
        log("== corpus: stage 5 (holders, arguments)")
        s5_merge.run(conn)
        log(f"  done in {fmt_secs(time.time() - t0)}")
    if 6 in stages:
        t0 = time.time()
        log("== corpus: stage 6 (summaries)")
        s6_summarise.run(conn, full=a.resummarise)
        log(f"  done in {fmt_secs(time.time() - t0)}")
    for n, label, fn in ((7, "tree: issues, themes", lambda c: s7_tree.run(c, recluster=a.recluster)),
                         (8, "relations", s8_relations.run), (9, "wiki", wiki.run), (10, "images", images.run)):
        if n in stages:
            t0 = time.time()
            log(f"== corpus: stage {n} ({label})")
            fn(conn)
            log(f"  done in {fmt_secs(time.time() - t0)}")
    log(f"all done in {fmt_secs(time.time() - t_all)}")


if __name__ == "__main__":
    main()
