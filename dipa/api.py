"""FastAPI server for dip-args: search and walk arguments by how they relate.

    uvicorn dipa.api:app --port 8001          (API docs at /docs)

Same endpoint names and result shape as dip-memory (so skinny-dip works with small changes),
on arguments instead of passages. Node ids are strings: "a…" argument, "i…" issue, "t…" theme,
"p<n>" passage, "h:<slug>" holder.

Every result carries `provenance`: "llm" (a summary written by the model), "manual" (edited by a
person) or "verbatim" (a passage, exactly as in the source). Arguments also carry the holder,
how each document treats the view (`reception`) and their `sources` (document, url, quote, passage).

Moves (GET /{move}?node_id=…&k=…):
  neighbours      similar arguments, spread across holders and documents (MMR)
  counter         outgoing CONTRADICTS, other documents first
  evidence        incoming SUPPORTS and EXAMPLE_OF
  zoom-in         incoming ELABORATES / EXAMPLE_OF; issue -> its arguments; theme -> its issues
  zoom-out        outgoing ELABORATES / EXAMPLE_OF, then the issue, then the theme
  shared-with     the same view held by other holders (SHARED_WITH)
  sources         the passages an argument was extracted from (verbatim, quote offsets)
  arguments-here  the arguments extracted from a passage
  holder          the holder's other arguments
"""
import collections
import hashlib
import json
import re
import secrets
from pathlib import Path

import numpy as np
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from dipa import config, db, images, llm, wiki
from dipa.ingest.s6_summarise import reception
from dipa.ingest.s7_tree import holder_free

app = FastAPI(title="dip-args", version="0.1",
              description="Argument-level corpus memory: search arguments, walk counters, evidence and zoom.")
app.add_middleware(CORSMiddleware, allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?",
                   allow_methods=["*"], allow_headers=["*"])

app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="ui")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse("ui/editor.html")


def _check_database():
    """Fail at start-up, not on the first request, when DIP_DATA_DIR has no built database: a
    mis-mounted volume would otherwise give an API that silently creates an empty one."""
    p = config.DB_PATH
    if not p.exists() or p.stat().st_size == 0:
        raise RuntimeError(f"no database at {p}: mount a built DIP_DATA_DIR (see compose.yaml)")
    import sqlite3
    with sqlite3.connect(f"file:{p}?mode=ro", uri=True) as c:
        try:
            n = c.execute("SELECT count(*) FROM nodes WHERE kind='argument'").fetchone()[0]
        except sqlite3.DatabaseError as e:
            raise RuntimeError(f"{p} is not a dip-args database: {e}") from e
    if not n:
        raise RuntimeError(f"{p} has no arguments: run the ingest first")


app.router.on_startup.append(_check_database)  # at server start, not on import (dipa.warm imports this module)

DEFAULT_K = 8
PER_HOLDER = 2
KINDS = ("argument", "issue", "theme", "holder", "passage")


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


class NotFound(Exception):
    pass


# --- node rows -> results ------------------------------------------------------------------

def _sources(conn, arg_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT m.quote, m.quote_ok, m.quote_start, m.quote_end, m.voice, coalesce(m.quote_passage, m.passage_id) pid, "
        "coalesce(i.display_title, i.title) doc, coalesce(i.display_source, i.source) url "
        "FROM argument_mentions am JOIN mentions m ON m.id=am.mention_id "
        "JOIN items i ON i.id=m.item_id WHERE am.argument_id=? ORDER BY i.id, pid", (arg_id,)).fetchall()
    return [{"document": r["doc"], "url": r["url"], "quote": r["quote"], "quote_verified": bool(r["quote_ok"]),
             "passage_id": f"p{r['pid']}" if r["pid"] else None, "quote_start": r["quote_start"],
             "quote_end": r["quote_end"], "voice": r["voice"]} for r in rows]


def node_out(conn, row, score: float | None = None, why: str | None = None) -> dict:
    """Any node (argument, issue, theme) as a result dict."""
    out = {"type": "node", "id": row["id"], "kind": row["kind"], "level": row["level"], "title": row["title"],
           "text": row["summary"], "abstraction": row["abstraction"], "parent_id": row["parent_id"],
           "provenance": "manual" if row["origin"] == "manual" else "llm", "model": row["model"],
           "score": score or 0.0, "why": why or ""}
    if row["kind"] == "argument":
        h = conn.execute("SELECT * FROM holders WHERE id=?", (row["holder_id"],)).fetchone()
        srcs = _sources(conn, row["id"])
        docs = list(dict.fromkeys(s["document"] for s in srcs))
        out |= {"role": row["kind_of_view"],
                "holder": {"id": f"h:{h['slug']}", "name": h["name"], "type": h["type"]} if h else None,
                "reception": reception(conn, row["id"]), "sources": srcs,
                "document": docs[0] if len(docs) == 1 else f"{len(docs)} documents",
                "source_url": srcs[0]["url"] if srcs else None}
    else:
        out |= {"document": None, "source_url": None,
                "children": conn.execute("SELECT count(*) FROM nodes WHERE parent_id=?", (row["id"],)).fetchone()[0]}
    return out


def passage_out(conn, pid: int, score=None, why=None, highlight: dict | None = None) -> dict:
    r = conn.execute("SELECT p.*, coalesce(i.display_title, i.title) doc, coalesce(i.display_source, i.source) url "
                     "FROM passages p JOIN items i ON i.id=p.item_id "
                     "WHERE p.id=? AND p.hidden=0", (pid,)).fetchone()
    if r is None:
        raise NotFound(f"passage p{pid} not found (or hidden)")
    out = {"type": "node", "id": f"p{pid}", "kind": "passage", "level": 0, "title": r["section"] or r["doc"],
           "text": r["text"], "provenance": "verbatim", "document": r["doc"], "source_url": r["url"],
           "abstraction": None, "score": score or 0.0, "why": why or ""}
    if highlight:
        out["highlight"] = highlight
    return out


def holder_out(conn, h, score=None, why=None) -> dict:
    n = conn.execute("SELECT count(*) FROM nodes WHERE holder_id=?", (h["id"],)).fetchone()[0]
    return {"type": "node", "id": f"h:{h['slug']}", "kind": "holder", "level": 0, "title": h["name"],
            "text": h["description"] or f"{h['type']} · {n} argument(s)", "holder_type": h["type"],
            "arguments": n, "provenance": "llm", "document": None, "source_url": None, "abstraction": None,
            "score": score or 0.0, "why": why or ""}


def anchor_of(conn, node_id: str):
    """(kind, row) for any node id."""
    if node_id.startswith("p") and node_id[1:].isdigit():
        r = conn.execute("SELECT * FROM passages WHERE id=? AND hidden=0", (int(node_id[1:]),)).fetchone()
        if r:
            return "passage", r
    elif node_id.startswith("h:"):
        r = conn.execute("SELECT * FROM holders WHERE slug=?", (node_id[2:],)).fetchone()
        if r:
            return "holder", r
    else:
        r = conn.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if r:
            return r["kind"], r
    raise NotFound(f"node {node_id} not found")


def _out(conn, kind, row, score=None, why=None):
    if kind == "passage":
        return passage_out(conn, row["id"], score, why)
    if kind == "holder":
        return holder_out(conn, row, score, why)
    return node_out(conn, row, score, why)


def envelope(conn, action: str, kind: str, row, results: list[dict], **meta) -> dict:
    a = _out(conn, kind, row)
    return {"action": action, "anchor": {"id": a["id"], "kind": a["kind"], "title": a["title"], "text": a["text"]},
            "results": results, "meta": meta}


# --- vectors and search --------------------------------------------------------------------

def _arg_rows(conn):
    return conn.execute("SELECT * FROM nodes WHERE kind='argument' AND summary IS NOT NULL ORDER BY id").fetchall()


def _search_text(r) -> str:
    return f"{r['title']}. {r['summary']}"


def _qvec(text: str):
    v = np.asarray(llm.embed([text], query=True)[0], dtype=np.float32)
    return v / (np.linalg.norm(v) or 1.0)


def _ensure_fts(conn):
    conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS fts_nodes USING fts5(id UNINDEXED, title, summary)")
    n_nodes = conn.execute("SELECT count(*) FROM nodes WHERE summary IS NOT NULL").fetchone()[0]
    if conn.execute("SELECT count(*) FROM fts_nodes").fetchone()[0] != n_nodes:
        conn.execute("DELETE FROM fts_nodes")
        conn.execute("INSERT INTO fts_nodes(id, title, summary) SELECT id, title, summary FROM nodes WHERE summary IS NOT NULL")
        conn.commit()


def _bm25(conn, q: str, k: int) -> list[str]:
    _ensure_fts(conn)
    terms = [t for t in re.findall(r"\w+", q.lower()) if len(t) > 2]
    if not terms:
        return []
    expr = " OR ".join(f'"{t}"' for t in terms)
    return [r[0] for r in conn.execute("SELECT id FROM fts_nodes WHERE fts_nodes MATCH ? ORDER BY bm25(fts_nodes) LIMIT ?",
                                       (expr, k))]


def search(conn, q: str, k: int, kinds: tuple[str, ...]):
    rows = conn.execute("SELECT * FROM nodes WHERE summary IS NOT NULL AND kind IN (%s)" % ",".join("?" * len(kinds)),
                        kinds).fetchall()
    if not rows:
        return []
    by_id = {r["id"]: r for r in rows}
    ranks: dict[str, float] = {}
    why = "hybrid: meaning + keywords (rank fusion)"
    try:
        vecs = db.embed_cached(conn, [_search_text(r) for r in rows])
        sims = vecs @ _qvec(q)
        for rank, j in enumerate(np.argsort(-sims)[:k * 3]):
            ranks[rows[j]["id"]] = ranks.get(rows[j]["id"], 0) + 1 / (60 + rank)
    except llm.LLMError:
        why = "keywords only: no embedding model reachable"
    for rank, nid in enumerate(i for i in _bm25(conn, q, k * 3) if i in by_id):
        ranks[nid] = ranks.get(nid, 0) + 1 / (60 + rank)
    ranked = sorted(ranks.items(), key=lambda kv: -kv[1])
    # diversity: at most PER_HOLDER results per holder before the rest (the corpus is mostly
    # authors' own arguments, which would otherwise crowd out the other voices)
    first, rest, seen = [], [], {}
    for i, sc in ranked:
        h = by_id[i]["holder_id"] if by_id[i]["kind"] == "argument" else i
        seen[h] = seen.get(h, 0) + 1
        (first if seen[h] <= PER_HOLDER else rest).append((i, sc))
    best = (first + rest)[:k]
    return [node_out(conn, by_id[i], round(s, 4), why) for i, s in best]


# --- moves ---------------------------------------------------------------------------------

def _docs(conn, arg_id) -> set:
    return {r[0] for r in conn.execute("SELECT DISTINCT m.item_id FROM argument_mentions am JOIN mentions m "
                                       "ON m.id=am.mention_id WHERE am.argument_id=?", (arg_id,))}


def _edges(conn, sql, args, why_of) -> list[tuple]:
    return [(r, why_of(r)) for r in conn.execute(sql, args).fetchall()]


def _judge_why(typ: str, ev: str | None) -> str:
    try:
        p = {int(k): v for k, v in json.loads(ev or "{}").items()}
    except ValueError:
        p = {}
    labels = {1: "opposed", 2: "qualifies", 5: "supports", 6: "same view", 7: "specific case"}
    parts = [f"{labels[d]} {p[d]:.0%}" for d in (1, 2, 5, 6, 7) if p.get(d, 0) >= 0.1]
    return f"{typ.lower().replace('_', ' ')} · LLM judge: " + ", ".join(parts) if parts else typ.lower()


def move_neighbours(conn, kind, row, k):
    if kind == "passage":
        return move_arguments_here(conn, kind, row, k)
    if kind == "holder":
        raise ValueError("neighbours works on arguments, issues, themes and passages")
    rows = conn.execute("SELECT * FROM nodes WHERE kind=? AND summary IS NOT NULL ORDER BY id", (row["kind"],)).fetchall()
    texts = [holder_free(r["summary_tpl"], r["title"]) if r["kind"] == "argument" else _search_text(r) for r in rows]
    vecs = db.embed_cached(conn, texts)
    me = next(j for j, r in enumerate(rows) if r["id"] == row["id"])
    sims = vecs @ vecs[me]
    # MMR: relevance minus redundancy, with a nudge away from the same holder and document
    chosen, cand = [], [j for j in np.argsort(-sims) if j != me][:60]
    my_docs = _docs(conn, row["id"]) if row["kind"] == "argument" else set()
    while cand and len(chosen) < k:
        def score(j):
            red = max((float(vecs[j] @ vecs[c]) for c in chosen), default=0.0)
            pen = 0.0
            if rows[j]["kind"] == "argument":
                pen += 0.05 * (rows[j]["holder_id"] == row["holder_id"])
                pen += 0.03 * bool(_docs(conn, rows[j]["id"]) & my_docs)
            return 0.7 * float(sims[j]) - 0.3 * red - pen
        best = max(cand, key=score)
        chosen.append(best)
        cand.remove(best)
    return [node_out(conn, rows[j], round(float(sims[j]), 3), "similar meaning, spread across holders and sources (MMR)")
            for j in chosen]


def move_counter(conn, kind, row, k):
    if kind != "argument":
        raise ValueError("counter works on arguments")
    mine = _docs(conn, row["id"])
    rows = conn.execute("SELECT n.*, e.weight w, e.evidence_json ev FROM edges e JOIN nodes n ON n.id=e.dst "
                        "WHERE e.src=? AND e.type='CONTRADICTS' AND e.method!='rejected'", (row["id"],)).fetchall()
    ranked = sorted(rows, key=lambda r: (bool(_docs(conn, r["id"]) & mine), -r["w"]))
    out = []
    for r in ranked[:k]:
        same = bool(_docs(conn, r["id"]) & mine)
        out.append(node_out(conn, r, round(r["w"], 3),
                            _judge_why("CONTRADICTS", r["ev"]) + (" · same document" if same else " · another document")))
    return out


def _incoming(conn, row, types, k, order_by_abstraction=False):
    rows = conn.execute("SELECT n.*, e.weight w, e.type t, e.evidence_json ev FROM edges e JOIN nodes n ON n.id=e.src "
                        "WHERE e.dst=? AND e.type IN (%s) AND e.method!='rejected'" % ",".join("?" * len(types)),
                        (row["id"], *types)).fetchall()
    key = (lambda r: ((r["abstraction"] or 3), -r["w"])) if order_by_abstraction else (lambda r: -r["w"])
    return [node_out(conn, r, round(r["w"], 3), _judge_why(r["t"], r["ev"])) for r in sorted(rows, key=key)[:k]]


def move_evidence(conn, kind, row, k):
    if kind != "argument":
        raise ValueError("evidence works on arguments")
    return _incoming(conn, row, ("SUPPORTS", "EXAMPLE_OF"), k)


def move_zoom_in(conn, kind, row, k):
    if kind == "argument":
        return _incoming(conn, row, ("ELABORATES", "EXAMPLE_OF"), k, order_by_abstraction=True)
    if kind in ("issue", "theme"):
        kids = conn.execute("SELECT * FROM nodes WHERE parent_id=? ORDER BY abstraction DESC, id", (row["id"],)).fetchall()
        if kind == "issue":  # one argument per holder first, so the debate's sides show up early
            seen, first, rest = set(), [], []
            for r in kids:
                (rest if r["holder_id"] in seen else first).append(r)
                seen.add(r["holder_id"])
            kids = first + rest
        return [node_out(conn, r, None, f"part of this {kind}") for r in kids[:k]]
    if kind == "holder":
        return move_holder(conn, kind, row, k)
    raise ValueError("zoom-in works on arguments, issues, themes and holders")


def move_zoom_out(conn, kind, row, k):
    if kind == "passage":
        return move_arguments_here(conn, kind, row, k)
    if kind not in ("argument", "issue"):
        raise ValueError("zoom-out works on arguments, issues and passages")
    out = []
    if kind == "argument":
        for r in conn.execute("SELECT n.*, e.weight w, e.type t, e.evidence_json ev FROM edges e JOIN nodes n ON n.id=e.dst "
                              "WHERE e.src=? AND e.type IN ('ELABORATES','EXAMPLE_OF') AND e.method!='rejected' "
                              "ORDER BY e.weight DESC", (row["id"],)).fetchall():
            out.append(node_out(conn, r, round(r["w"], 3), _judge_why(r["t"], r["ev"])))
    parent = conn.execute("SELECT * FROM nodes WHERE id=?", (row["parent_id"],)).fetchone() if row["parent_id"] else None
    while parent is not None:
        out.append(node_out(conn, parent, None, f"the {parent['kind']} this belongs to"))
        parent = conn.execute("SELECT * FROM nodes WHERE id=?", (parent["parent_id"],)).fetchone() if parent["parent_id"] else None
    return out[:k]


def move_shared_with(conn, kind, row, k):
    if kind != "argument":
        raise ValueError("shared-with works on arguments")
    rows = conn.execute("SELECT n.*, e.weight w, e.evidence_json ev FROM edges e JOIN nodes n ON n.id=e.dst "
                        "WHERE e.src=? AND e.type='SHARED_WITH' AND e.method!='rejected' ORDER BY e.weight DESC",
                        (row["id"],)).fetchall()
    return [node_out(conn, r, round(r["w"], 3), "the same view, held by someone else") for r in rows[:k]]


def move_sources(conn, kind, row, k):
    if kind != "argument":
        raise ValueError("sources works on arguments")
    out, seen = [], set()
    for s in _sources(conn, row["id"]):
        pid = s["passage_id"]
        if not pid or pid in seen:
            continue
        seen.add(pid)
        try:
            out.append(passage_out(conn, int(pid[1:]), None, f"verbatim source · {s['voice']}",
                                   highlight={"quote": s["quote"], "start": s["quote_start"], "end": s["quote_end"],
                                              "verified": s["quote_verified"]}))
        except NotFound:
            continue
    return out[:k]


def move_arguments_here(conn, kind, row, k):
    if kind != "passage":
        raise ValueError("arguments-here works on passages")
    rows = conn.execute(
        "SELECT DISTINCT n.* FROM mentions m JOIN argument_mentions am ON am.mention_id=m.id JOIN nodes n ON n.id=am.argument_id "
        "WHERE coalesce(m.quote_passage, m.passage_id)=? ORDER BY n.abstraction DESC", (row["id"],)).fetchall()
    return [node_out(conn, r, None, "extracted from this passage") for r in rows[:k]]


def move_holder(conn, kind, row, k):
    hid = row["id"] if kind == "holder" else row["holder_id"]
    if hid is None:
        raise ValueError("holder works on arguments and holders")
    rows = conn.execute("SELECT * FROM nodes WHERE holder_id=? AND id!=? ORDER BY abstraction DESC, id",
                        (hid, row["id"] if kind != "holder" else "")).fetchall()
    return [node_out(conn, r, None, "held by the same holder") for r in rows[:k]]


def _anchor_text(kind, row) -> str:
    if kind == "argument":
        return holder_free(row["summary_tpl"], row["title"])
    if kind in ("issue", "theme"):
        return _search_text(row)
    if kind == "passage":
        return row["text"]
    raise ValueError("images works on arguments, issues, themes and passages")


def _arg_texts(conn) -> list[str]:
    return [holder_free(r["summary_tpl"], r["title"]) for r in _arg_rows(conn)]


def move_images(conn, kind, row, k):
    """Pictures for a node: by subject (what they show) and by metaphor (what they can stand for)."""
    text = _anchor_text(kind, row)
    vec = db.embed_cached(conn, [text])[0]
    concept_text = row["summary"] if kind == "argument" and row["summary"] else text
    results, meta = images.find(conn, concept_text, vec, images.corpus_centroid(conn, lambda: _arg_texts(conn)), k)
    _image_meta.update(meta)
    return results


_image_meta: dict = {}

MOVES = {"images": move_images, "neighbours": move_neighbours, "counter": move_counter, "evidence": move_evidence,
         "zoom-in": move_zoom_in, "zoom-out": move_zoom_out, "shared-with": move_shared_with,
         "sources": move_sources, "arguments-here": move_arguments_here, "holder": move_holder}


def _run_move(conn, name, node_id, text, k):
    try:
        if node_id is None:
            if not text:
                raise ValueError("give node_id or text")
            hits = search(conn, text, 1, ("argument",))
            if not hits:
                raise NotFound("nothing matches that text")
            node_id = hits[0]["id"]
        kind, row = anchor_of(conn, node_id)
        _image_meta.clear()
        results = MOVES[name](conn, kind, row, k)
        return envelope(conn, name, kind, row, results, **_image_meta)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except llm.LLMError as e:
        raise HTTPException(503, f"local model unavailable: {e}") from e


def _endpoint(name):
    def endpoint(node_id: str | None = Query(None), text: str | None = Query(None),
                 k: int = Query(DEFAULT_K, ge=1, le=50), conn=Depends(get_conn)):
        return _run_move(conn, name, node_id, text, k)
    endpoint.__name__ = name.replace("-", "_")
    endpoint.__doc__ = MOVES[name].__doc__ or name
    return endpoint


for _name in MOVES:
    app.get(f"/{_name}", summary=_name)(_endpoint(_name))


# --- question (RAG over the wiki tree, arguments and passages) ------------------------------
# POST /question {question, node_id?, selection?, k?}. Material, labelled for the prompt:
#   the card asked about (and what it links to), issues/themes (the wiki tree, with manual edits),
#   arguments (hybrid search, spread across holders), passages (sources of those arguments, then
#   the nearest passages by embedding). Labels are short ([A1], [P2], [I1], [T1]), never row ids;
#   the answer's labels are checked against the material and mapped back to node ids.

QUESTION_PROMPT = """You answer a reader's question about a corpus of documents, using ONLY the labelled material below.

Rules:
- Back every statement with the label of the material it comes from, in square brackets: [A2], or [A2][P4] for several.
- Each argument says who holds it ("Held by ..."). For every view you report, name that holder exactly as written on that argument, and never a holder from a different item; never state a view as fact. If the holder is "unspecified", say the source does not name one. Where holders disagree, say so.
- Quote source text word for word, in quotation marks, and only from [P...] passages.
- Use no outside knowledge. If the material does not answer the question, say so briefly and set found to false.
- Answer in 2-6 sentences of plain prose.
- The answer is read as it is, by the reader: write only the answer itself. Do not restate the question, describe the material, plan, or explain how you checked it; no notes about the found field.
{focus}
ISSUES AND THEMES (questions the corpus debates, with a summary of the debate):
{issues}

ARGUMENTS (views, each with who holds it):
{arguments}

PASSAGES (verbatim source text):
{passages}

QUESTION: {question}{retry}"""

# qwen3 sometimes writes its reasoning into the answer field ("The reader is asking about... Let me go
# through the material..."), occasionally for thousands of words. Answers are capped, checked for that
# (and for a bare "No"), and asked once more with RETRY_NOTE if they fail; the retry is a different
# prompt, so it's cached apart. (qwen3's "/no_think" switch was tried: it made the 4B model reply "No".)
QUESTION_MAX_TOKENS = 700  # 2-6 cited sentences fit easily
QUESTION_MAX_CHARS = 2000
QUESTION_MIN_CHARS = 30
RETRY_NOTE = ("\n\nIMPORTANT: a previous answer to this described its reasoning instead of answering. "
              "Reply with the final answer only: 2-6 sentences addressed to the reader.")
REASONING = re.compile(
    r"^\W*(the (reader|user|question)\b[^.]{0,40}\b(ask|want|look)|i (need|will|'ll|should|must)\b|let me\b|"
    r"let's\b|okay\b|ok\b|alright\b|first,|hmm|wait\b|so,)"
    r"|\b(set|setting) (the )?found\b|\bfound (should|must) be\b|\banswer should be set\b",
    re.I)


FOUND_NOTE = re.compile(r"\s*\bfound\s*[:=]\s*(true|false)\b\.?\s*$", re.I)  # "… Found: false." echoed into the text


def _looks_like_reasoning(answer: str) -> bool:
    """True for an answer to ask again: the model's reasoning, too long, or too short to say anything."""
    n = len(answer.strip())
    return n > QUESTION_MAX_CHARS or n < QUESTION_MIN_CHARS or bool(REASONING.search(answer))


def _ask_answer(conn, ask, prompt_for) -> tuple[dict, float, bool]:
    """Ask the question; if the answer runs over the cap or reads like the model's reasoning, ask
    once more with RETRY_NOTE. A second failure is returned as it is (or raises, at the cap)."""
    try:
        out, secs, cached = ask(conn, "question", prompt_for(""), ANSWER_SCHEMA,
                                temperature=0.2, max_tokens=QUESTION_MAX_TOKENS)
        if not _looks_like_reasoning(out["answer"]):
            return out, secs, cached
    except llm.LLMError as e:
        if "token cap" not in str(e):
            raise
        secs = 0.0
    out, secs2, cached2 = ask(conn, "question", prompt_for(RETRY_NOTE), ANSWER_SCHEMA,
                              temperature=0.2, max_tokens=QUESTION_MAX_TOKENS)
    return out, secs + secs2, cached2

ANSWER_SCHEMA = {"type": "object", "properties": {"answer": {"type": "string"}, "found": {"type": "boolean"}},
                 "required": ["answer", "found"]}

QUESTION_BUDGET = 14000  # characters of material: with the prompt and answer, inside LLM_NUM_CTX=8192
QUESTION_PASSAGE_CHARS = 900
LABEL_PREFIX = {"argument": "A", "passage": "P", "issue": "I", "theme": "T", "holder": "H"}


class QuestionIn(BaseModel):
    question: str
    node_id: str | None = None
    selection: str | None = None
    k: int = 10


def _visible_passages(conn):
    return conn.execute("SELECT p.id, p.text FROM passages p WHERE p.hidden=0 ORDER BY p.id").fetchall()


def _nearest_passages(conn, query: str, k: int) -> list[int]:
    """Passage ids nearest to the query by embedding (vectors cached in embed_cache after the first call)."""
    rows = _visible_passages(conn)
    vecs = db.embed_cached(conn, [r["text"] for r in rows])
    sims = vecs @ _qvec(query)
    return [rows[j]["id"] for j in np.argsort(-sims)[:k]]


class _Material:
    """Labelled material for the prompt, within QUESTION_BUDGET characters."""

    def __init__(self):
        self.by_id: dict[str, dict] = {}  # node id -> {label, kind, line, result}
        self.counts = collections.Counter()
        self.size = 0

    def add(self, kind: str, node_id: str, line: str, result: dict) -> str | None:
        if node_id in self.by_id:
            return self.by_id[node_id]["label"]
        if self.size + len(line) > QUESTION_BUDGET:
            return None
        self.counts[kind] += 1
        label = f"{LABEL_PREFIX[kind]}{self.counts[kind]}"
        self.by_id[node_id] = {"label": label, "kind": kind, "line": f"[{label}] {line}", "result": result}
        self.size += len(line)
        return label

    def lines(self, *kinds) -> str:
        return "\n".join(m["line"] for m in self.by_id.values() if m["kind"] in kinds) or "(none)"


def _arg_line(conn, r: dict) -> str:
    rec = "; ".join(f"{VOICE_WORD.get(x['voice'], x['voice'])} \"{x['document']}\"" for x in r.get("reception") or [])
    holder = r["holder"]["name"] if r.get("holder") else "unspecified"
    return f"Held by {holder}{f' ({rec})' if rec else ''}. {r['title']}: {r['text']}"


VOICE_WORD = {"argued": "argued in", "conceded": "conceded in", "reported": "reported in",
              "endorsed": "endorsed by the author of", "rejected": "rejected by the author of"}


def _passage_line(r: dict) -> str:
    text = r["text"] if len(r["text"]) <= QUESTION_PASSAGE_CHARS else r["text"][:QUESTION_PASSAGE_CHARS] + " [...]"
    return f"From \"{r['document']}\": \"{text}\""


def _node_line(conn, r: dict) -> str:
    if r["kind"] == "argument":
        return _arg_line(conn, r)
    if r["kind"] == "passage":
        return _passage_line(r)
    if r["kind"] == "holder":
        return f"Holder {r['title']}: {r['text']}"
    return f"{r['title']} {r['text']}"


def _add_result(conn, mat: _Material, r: dict) -> str | None:
    return mat.add(r["kind"], r["id"], _node_line(conn, r), r)


def _focus(conn, mat: _Material, kind: str, row, selection: str | None) -> str:
    """The card asked about, and what it links to, at the head of the material."""
    me = _out(conn, kind, row, why="the card you asked about")
    label = _add_result(conn, mat, me)
    related = []
    if kind == "argument":
        for name in ("counter", "evidence", "shared-with", "zoom-out"):
            related += MOVES[name](conn, kind, row, 3)
        related += move_sources(conn, kind, row, 2)
    elif kind == "passage":
        related += move_arguments_here(conn, kind, row, 5)
    elif kind in ("issue", "theme"):
        related += move_zoom_in(conn, kind, row, 6)
        related += move_zoom_out(conn, kind, row, 2) if kind == "issue" else []
    elif kind == "holder":
        related += move_holder(conn, kind, row, 6)
    for r in related:
        r["why"] = f"linked to the card you asked about ({r['why']})" if r.get("why") else "linked to the card you asked about"
        _add_result(conn, mat, r)
    out = f"\nThe reader is asking about this {kind} [{label}]: {_node_line(conn, me)}\n"
    if selection:
        out += f"They selected this part of it: \"{selection}\"\n"
    return out


CITE = re.compile(r"\[([^\[\]]{1,60})\]")


def _clean_citations(answer: str, labels: dict[str, str]) -> tuple[str, list[str]]:
    """Normalise "[A2, P4]" to "[A2][P4]", drop labels not in the material; return the cited labels in order."""
    cited = []

    def sub(m):
        parts = [p.strip() for p in re.split(r"[,;\s]+", m.group(1)) if p.strip()]
        if not parts or not all(re.fullmatch(r"[A-Z]\d+", p) for p in parts):
            return m.group(0)  # not a citation, e.g. "[...]" in a quote
        good = list(dict.fromkeys(p for p in parts if p in labels))
        cited.extend(p for p in good if p not in cited)
        return "".join(f"[{p}]" for p in good)

    text = re.sub(r"[ \t]{2,}", " ", CITE.sub(sub, answer))
    return re.sub(r"\s+([.,;])", r"\1", text).strip(), cited


def answer_question(conn, question: str, node_id: str | None, selection: str | None, k: int) -> dict:
    from dipa.ingest.common import ask
    question = question.strip()
    if not question:
        raise ValueError("question is empty")
    selection = (selection or "").strip()[:2000] or None
    mat = _Material()
    focus, anchor = "", None
    if node_id:
        kind, row = anchor_of(conn, node_id)
        focus = _focus(conn, mat, kind, row, selection)
        a = _out(conn, kind, row)
        anchor = {"id": a["id"], "kind": a["kind"], "title": a["title"], "text": a["text"]}
    query = f"{question}\n{selection}" if selection else question
    for r in search(conn, query, 3, ("issue", "theme")):
        r["why"] = "issue or theme matching the question"
        _add_result(conn, mat, r)
    found_args = search(conn, query, k, ("argument",))
    for r in found_args:
        r["why"] = "argument matching the question"
        _add_result(conn, mat, r)
    pids = []
    for r in found_args[:5]:  # the source passage of the best arguments: where the view is in the text
        pid = next((s["passage_id"] for s in r.get("sources") or [] if s["passage_id"]), None)
        if pid:
            pids.append((int(pid[1:]), f"source passage of an argument matching the question"))
    try:
        pids += [(p, "passage matching the question") for p in _nearest_passages(conn, query, 6)]
    except llm.LLMError:
        pass  # no embeddings: the arguments' source passages still come through
    for pid, why in pids:
        try:
            _add_result(conn, mat, passage_out(conn, pid, None, why))
        except NotFound:
            continue

    material = dict(focus=focus, issues=mat.lines("issue", "theme"),
                    arguments=mat.lines("argument", "holder"), passages=mat.lines("passage"), question=question)
    out, secs, cached = _ask_answer(conn, ask, lambda retry: QUESTION_PROMPT.format(**material, retry=retry))
    labels = {m["label"]: nid for nid, m in mat.by_id.items()}
    answer, cited = _clean_citations(FOUND_NOTE.sub("", out["answer"]).strip(), labels)
    cited_ids = [labels[c] for c in cited]
    sources = []
    for nid, m in mat.by_id.items():
        r = dict(m["result"], label=m["label"], cited=nid in cited_ids)
        sources.append(r)
    sources.sort(key=lambda r: (not r["cited"], cited_ids.index(r["id"]) if r["cited"] else 0))
    return {"action": "question", "question": question, "selection": selection, "anchor": anchor,
            "answer": answer, "found": bool(out["found"]) and bool(cited), "cited": cited_ids, "labels": labels,
            "sources": sources, "meta": {"seconds": round(secs, 1), "cached": cached, "model": config.LLM_MODEL,
                                         "material": dict(mat.counts)}}


@app.post("/question")
def question(body: QuestionIn, conn=Depends(get_conn)):
    """Ask the LLM a question, optionally about a card (node_id) and a part of its text (selection).
    The answer cites its material by label; `labels` maps each label to a node id."""
    try:
        return answer_question(conn, body.question, body.node_id, body.selection, max(1, min(body.k, 20)))
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except llm.LLMError as e:
        raise HTTPException(503, f"local model unavailable: {e}") from e


# --- listing, search, holders ---------------------------------------------------------------

@app.get("/health")
def health(conn=Depends(get_conn)):
    return {"db": str(config.DB_PATH), "status": "ok",
            "nodes": {r[0]: r[1] for r in conn.execute("SELECT kind, count(*) FROM nodes GROUP BY kind")},
            "holders": conn.execute("SELECT count(*) FROM holders").fetchone()[0],
            "passages": conn.execute("SELECT count(*) FROM passages WHERE hidden=0").fetchone()[0],
            "edges": {r[0]: r[1] for r in conn.execute("SELECT type, count(*) FROM edges GROUP BY type")},
            "models": {"llm": config.LLM_MODEL, "embed": config.EMBED_MODEL},
            "editing": "token" if config.EDIT_TOKEN else "open"}


@app.get("/items")
def items(conn=Depends(get_conn)):
    return [dict(r) for r in conn.execute(
        "SELECT id, coalesce(display_title, title) title, coalesce(display_source, source) source, "
        "coalesce(display_author, author) author, summary, author_stance, title AS original_title FROM items")]


@app.get("/nodes")
def list_nodes(kind: str = "argument", limit: int = 200, offset: int = 0, conn=Depends(get_conn)):
    if kind not in KINDS:
        raise HTTPException(400, f"kind must be one of {KINDS}")
    if kind == "holder":
        rows = conn.execute("SELECT h.* FROM holders h ORDER BY (SELECT count(*) FROM nodes n WHERE n.holder_id=h.id) DESC, "
                            "h.name LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        return [holder_out(conn, r) for r in rows]
    if kind == "passage":
        rows = conn.execute("SELECT id FROM passages WHERE hidden=0 ORDER BY item_id, ord LIMIT ? OFFSET ?",
                            (limit, offset)).fetchall()
        return [passage_out(conn, r[0]) for r in rows]
    rows = conn.execute("SELECT * FROM nodes WHERE kind=? AND summary IS NOT NULL ORDER BY abstraction DESC, id "
                        "LIMIT ? OFFSET ?", (kind, limit, offset)).fetchall()
    return [node_out(conn, r) for r in rows]


@app.get("/nodes/{node_id}")
def get_node(node_id: str, conn=Depends(get_conn)):
    try:
        kind, row = anchor_of(conn, node_id)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    out = _out(conn, kind, row)
    if kind in ("argument", "issue", "theme"):
        out["edges"] = [dict(r) for r in conn.execute(
            "SELECT src, dst, type, weight, method FROM edges WHERE src=? OR dst=?", (node_id, node_id))]
    return out


@app.get("/search")
def search_endpoint(q: str, k: int = 10, kind: str = "argument", conn=Depends(get_conn)):
    kinds = tuple(x for x in kind.split(",") if x in ("argument", "issue", "theme"))
    if not kinds:
        raise HTTPException(400, "kind: argument, issue and/or theme")
    try:
        return search(conn, q, k, kinds)
    except llm.LLMError as e:
        raise HTTPException(503, f"local model unavailable: {e}") from e


@app.get("/holders")
def holders(conn=Depends(get_conn)):
    return list_nodes(kind="holder", limit=1000, offset=0, conn=conn)


@app.get("/holders/{slug}")
def holder_page(slug: str, conn=Depends(get_conn)):
    h = conn.execute("SELECT * FROM holders WHERE slug=?", (slug,)).fetchone()
    if h is None:
        raise HTTPException(404, f"holder {slug} not found")
    args = conn.execute("SELECT * FROM nodes WHERE holder_id=? ORDER BY abstraction DESC, id", (h["id"],)).fetchall()
    ids = [a["id"] for a in args]
    against = []
    if ids:
        against = conn.execute(
            "SELECT DISTINCT hh.name, hh.slug FROM edges e JOIN nodes n ON n.id=e.dst JOIN holders hh ON hh.id=n.holder_id "
            "WHERE e.type='CONTRADICTS' AND e.src IN (%s) AND hh.id!=?" % ",".join("?" * len(ids)), (*ids, h["id"])).fetchall()
    return holder_out(conn, h) | {"aliases": json.loads(h["aliases_json"] or "[]"),
                                  "argument_list": [node_out(conn, a) for a in args],
                                  "disagrees_with": [{"id": f"h:{r['slug']}", "name": r["name"]} for r in against]}


# --- wiki editor (dipa/static/editor.html) ------------------------------------------------
# Pages: wiki/{themes,issues,holders}/*.md (editable) and index.md, unsorted.md, lint.md
# (kind "page"; unsorted is editable so lines can be cut from it, index and lint are generated).
# Saving writes the file, then wiki.apply: sync the edits, rebuild what depends on them without
# new LLM text, and rewrite the pages.

READ_ONLY = {"index.md", "lint.md"}
WIKI_KINDS = ("themes", "issues", "holders", "sources", "images", "page")


def _wiki_path(kind: str, name: str) -> Path:
    if kind not in WIKI_KINDS or not re.fullmatch(r"[\w.-]+\.md", name):
        raise HTTPException(404, f"no {kind} page {name!r}")
    path = wiki.wiki_dir() / (name if kind == "page" else f"{kind}/{name}")
    if not path.is_file():
        raise HTTPException(404, f"no {kind} page {name!r}")
    return path


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _set_locked(text: str, locked: bool) -> str:
    m = wiki.FRONT.match(text)
    if not m:
        return text
    head = m.group(0)
    if re.search(r"^locked:", head, flags=re.M):
        head = re.sub(r"^locked:.*$", f"locked: {str(locked).lower()}", head, flags=re.M)
    else:
        head = head.replace("\n---", f"\nlocked: {str(locked).lower()}\n---", 1)
    return head + text[m.end():]


@app.get("/wiki-files")
def wiki_files():
    out = []
    for name, title in (("index.md", "Debate map"), ("unsorted.md", "Unsorted arguments"), ("lint.md", "Things to check")):
        if (wiki.wiki_dir() / name).is_file():
            out.append({"kind": "page", "name": name, "title": title, "locked": False, "readonly": name in READ_ONLY})
    for kind in ("themes", "issues", "holders", "sources", "images"):
        for p in wiki._pages(kind):
            text = p.read_text(encoding="utf-8")
            # image pages use dip-memory's plain "key: value" front matter (titles may contain colons)
            f = images.parse_page(text) if kind == "images" else wiki.parse(text)["front"]
            if kind == "images":
                f["locked"] = f.get("locked") == "true"
            out.append({"kind": kind, "name": p.name, "id": f.get("id"), "readonly": False,
                        "title": str(f.get("title") or f.get("name") or p.stem), "locked": bool(f.get("locked"))})
    return out


@app.get("/wiki-files/{kind}/{name}")
def wiki_file(kind: str, name: str):
    text = _wiki_path(kind, name).read_text(encoding="utf-8")
    return {"kind": kind, "name": name, "text": text, "hash": _hash(text)}


class WikiEdit(BaseModel):
    text: str
    base_hash: str          # the hash the editor loaded; a mismatch means someone else saved first
    lock: bool | None = None


@app.put("/wiki-files/{kind}/{name}")
def save_wiki_file(kind: str, name: str, body: WikiEdit, conn=Depends(get_conn),
                   x_edit_token: str | None = Header(None)):
    if config.EDIT_TOKEN and not secrets.compare_digest(x_edit_token or "", config.EDIT_TOKEN):
        raise HTTPException(403, "editing needs the edit token")
    if name in READ_ONLY and kind == "page":
        raise HTTPException(400, f"{name} is generated; edit the pages it links to")
    path = _wiki_path(kind, name)
    if _hash(path.read_text(encoding="utf-8")) != body.base_hash:
        raise HTTPException(409, "this page changed since you opened it; reload it to see the other edit")
    text = body.text.replace("\r\n", "\n")
    if body.lock is not None:
        text = _set_locked(text, body.lock)
    try:
        images.parse_page(text) if kind == "images" else wiki.parse(text)
    except Exception as e:  # noqa: BLE001 - yaml errors in the front matter
        raise HTTPException(400, f"the front matter is not valid: {e}") from e
    tmp = path.with_suffix(".md.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    try:
        if kind == "images":  # the image library is rebuilt from its pages (needs the embedding model)
            images.run(conn)
            return {"kind": kind, "name": name, "locked": bool(body.lock), "changed": {}, "removed": False,
                    "note": "saved · image library reloaded and re-embedded"}
        res = wiki.apply(conn)
    except llm.LLMError as e:
        raise HTTPException(503, f"saved, but applying it needs the embedding model: {e}") from e
    changed = res["changed"]
    what = ", ".join(f"{v} {k}" for k, v in changed.items()) or "no change the database tracks (text kept on the page)"
    gone = not path.is_file()
    note = f"saved · applied: {what}" + (" · this page was merged into another and removed" if gone else "")
    return {"kind": kind, "name": name, "note": note, "locked": bool(body.lock), "changed": changed, "removed": gone}


# --- image library --------------------------------------------------------------------------

@app.get("/image-library")
def image_library(conn=Depends(get_conn)):
    return [{"id": r["id"], "slug": r["slug"], "title": r["title"], "description": r["description"],
             "subjects": json.loads(r["subjects_json"] or "[]"), "credit": r["credit"], "source": r["source"],
             "creator": r["creator"], "license": r["license"], "url": f"/image-file/{r['slug']}"}
            for r in conn.execute("SELECT * FROM images ORDER BY title COLLATE NOCASE")]


@app.get("/image-file/{slug}", response_class=FileResponse)
def image_file(slug: str, conn=Depends(get_conn)):
    r = conn.execute("SELECT path FROM images WHERE slug=?", (slug,)).fetchone()
    path = images.resolve_path(r["path"]) if r else None
    if path is None or not path.is_file():
        raise HTTPException(404, f"no image {slug}")
    return FileResponse(path)


@app.get("/image-passages/{slug}")
def image_arguments(slug: str, k: int = Query(DEFAULT_K, ge=1, le=50), conn=Depends(get_conn)):
    """The reverse of Images: arguments a picture fits, by subject and by metaphor. (Named like
    dip-memory's endpoint so skinny-dip's image node works unchanged; it returns arguments.)"""
    r = conn.execute("SELECT * FROM images WHERE slug=?", (slug,)).fetchone()
    if r is None:
        raise HTTPException(404, f"no image {slug}")
    rows = _arg_rows(conn)
    try:
        vecs = db.embed_cached(conn, _arg_texts(conn))
        hits = images.reverse(conn, slug, rows, vecs, k)
    except llm.LLMError as e:
        raise HTTPException(503, f"local model unavailable: {e}") from e
    by_id = {a["id"]: a for a in rows}
    results = [node_out(conn, by_id[a], round(s, 3), why) | {"match": match} for a, s, why, match in hits]
    return {"action": "image-passages", "anchor": {"id": slug, "kind": "image", "title": r["title"], "text": r["description"]},
            "results": results, "meta": {}}

