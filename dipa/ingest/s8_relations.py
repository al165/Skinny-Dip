"""Stage 8 (corpus-wide): typed relations between arguments.

Candidates (unordered pairs):
  - each argument and its NEIGHBOURS nearest arguments (holder-free embeddings, as in stage 7);
  - every pair of arguments with DIFFERENT holders inside the same issue: opposing views worded
    differently are rarely nearest neighbours (dip-memory counter research, 2026-10-07).
Each pair is oriented general -> specific (A = higher abstraction) and judged once by the LLM
on summary + verbatim quote, with the digit probabilities read from the logprobs (dip-memory's
graded judge, AUC 0.93 on passages, extended from 5 to 7 options so one call types every edge):

  1 opposed, 2 qualifies          -> P(1) + 0.5*P(2) >= COUNTER_THRESHOLD: CONTRADICTS, both directions
                                     (on the test run "qualifies" was the judge's top answer for 200 of
                                     2,573 pairs and most of those were compatible views, so it counts half)
  5 supports                      -> P >= TYPE_THRESHOLD: B SUPPORTS A
  6 same view                     -> different holders: SHARED_WITH both directions
  7 a specific case of A          -> B EXAMPLE_OF A (example/fact or abstraction <= 2), else B ELABORATES A
  3 different question, 4 compatible -> no edge

Judgements are cached by prompt + model. Edges from the wiki (method manual / rejected) are kept:
a rejected pair is never re-added."""
import collections
import json
import time
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from dipa import config, db, llm
from dipa.ingest.common import _db_lock, fmt_secs, log
from dipa.ingest.s7_tree import holder_free

NEIGHBOURS = 6
COUNTER_THRESHOLD = 0.5
TYPE_THRESHOLD = 0.5
MAX_ISSUE_PAIRS = 120   # per issue, cross-holder pairs beyond this are left to the neighbour candidates

SCHEMA = {"type": "object", "properties": {"relation": {"type": "integer", "minimum": 1, "maximum": 7}},
          "required": ["relation"]}
SYSTEM = "You judge argumentative relations between views. Be strict."
PROMPT = """View A, held by {ha}:
{sa}
Quote: "{qa}"

View B, held by {hb}:
{sb}
Quote: "{qb}"

How does view B relate to view A? Answer with one digit:
1 = opposed: B argues against A's position on the same question
2 = qualifies: B partly pushes back on A, adding a limit, exception or complication
3 = different question: B is about something else, even if the topic is related
4 = compatible: both could be held together, and neither backs the other up
5 = supports: B gives evidence or reasons for A
6 = same view: B states essentially the same claim as A
7 = specific case: B is an example, case or more detailed instance of A"""


def _judge(conn, prompt: str) -> dict[int, float]:
    model = config.LLM_MODEL
    payload = {"prompt": prompt, "system": SYSTEM, "schema": SCHEMA, "logprobs": True}
    with _db_lock:
        h, hit = db.cache_get(conn, "relation", payload, model)
    if hit is not None:
        return {int(k): v for k, v in hit.items()}
    t0 = time.time()
    res, lp = llm.generate(prompt, schema=SCHEMA, system=SYSTEM, temperature=0.0, logprobs=True)
    probs = llm.digit_probs(lp, "relation", 1, 7) or {int(res["relation"]): 1.0}
    with _db_lock:
        db.cache_put(conn, "relation", h, model, probs, time.time() - t0)
    return probs


def candidates(conn):
    """(args by id, oriented candidate pairs, their judge prompts, number from nearest neighbours).
    Also used by dipa.labelpack, so people label exactly the pairs the judge sees."""
    args = {r["id"]: r for r in conn.execute(
        "SELECT n.*, h.name AS holder, h.id AS hid FROM nodes n JOIN holders h ON h.id=n.holder_id "
        "WHERE n.kind='argument' AND n.summary IS NOT NULL")}
    if len(args) < 2:
        return args, [], [], 0
    ids = sorted(args)
    quote = {}
    for r in conn.execute("SELECT am.argument_id, m.quote FROM argument_mentions am JOIN mentions m "
                          "ON m.id=am.mention_id ORDER BY m.quote_ok DESC, length(m.quote) DESC"):
        quote.setdefault(r[0], r[1])
    vecs = db.embed_cached(conn, [holder_free(args[i]["summary_tpl"], args[i]["title"]) for i in ids])
    sims = vecs @ vecs.T

    pairs = set()
    for x in range(len(ids)):
        for y in np.argsort(-sims[x])[1:NEIGHBOURS + 1]:
            pairs.add(tuple(sorted((ids[x], ids[int(y)]))))
    n_knn = len(pairs)
    by_issue = collections.defaultdict(list)
    for i in ids:
        if args[i]["parent_id"]:
            by_issue[args[i]["parent_id"]].append(i)
    pos = {i: k for k, i in enumerate(ids)}
    for members in by_issue.values():
        cross = [(a, b) for k, a in enumerate(members) for b in members[k + 1:] if args[a]["hid"] != args[b]["hid"]]
        cross.sort(key=lambda p: -sims[pos[p[0]], pos[p[1]]])
        pairs.update(tuple(sorted(p)) for p in cross[:MAX_ISSUE_PAIRS])
    def orient(a, b):  # A = the more general view
        ka, kb = (args[a]["abstraction"] or 3, a), (args[b]["abstraction"] or 3, b)
        return (a, b) if ka >= kb else (b, a)

    jobs = [orient(a, b) for a, b in sorted(pairs)]
    prompts = [PROMPT.format(ha=args[a]["holder"], sa=args[a]["summary"], qa=quote.get(a, "")[:300],
                             hb=args[b]["holder"], sb=args[b]["summary"], qb=quote.get(b, "")[:300]) for a, b in jobs]
    return args, jobs, prompts, n_knn


def cached_judgement(conn, prompt: str) -> dict[int, float] | None:
    """The judge's digit probabilities for a prompt if they are cached, else None (no LLM call)."""
    payload = {"prompt": prompt, "system": SYSTEM, "schema": SCHEMA, "logprobs": True}
    _, hit = db.cache_get(conn, "relation", payload, config.LLM_MODEL)
    return {int(k): v for k, v in hit.items()} if hit is not None else None


def counter_score(probs: dict[int, float]) -> float:
    """How strongly the judge says the pair is a counter (the CONTRADICTS rule's score)."""
    return probs.get(1, 0.0) + 0.5 * probs.get(2, 0.0)


def run(conn):
    args, jobs, prompts, n_knn = candidates(conn)
    if len(args) < 2:
        log("  relations: too few arguments")
        return
    rejected = {(r[0], r[1]) for r in conn.execute("SELECT src, dst FROM edges WHERE method='rejected'")}
    log(f"  relations: {len(jobs)} candidate pairs ({n_knn} nearest-neighbour, "
        f"{len(jobs) - n_knn} more from shared issues) · {config.LLM_PARALLEL} at a time")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=config.LLM_PARALLEL) as pool:
        results = list(pool.map(lambda p: _safe(conn, p), prompts))
    log(f"  relations: judged in {fmt_secs(time.time() - t0)}")

    edges = {}

    def add(src, dst, typ, w, probs):
        if (src, dst) in rejected or (dst, src) in rejected:
            return
        if (src, dst, typ) not in edges or edges[(src, dst, typ)][0] < w:
            edges[(src, dst, typ)] = (w, probs)

    for (a, b), probs in zip(jobs, results):
        if not probs:
            continue
        counter = counter_score(probs)
        top, p_top = max(probs.items(), key=lambda kv: kv[1])
        if counter >= COUNTER_THRESHOLD:
            add(a, b, "CONTRADICTS", counter, probs)
            add(b, a, "CONTRADICTS", counter, probs)
        elif top == 5 and p_top >= TYPE_THRESHOLD:
            add(b, a, "SUPPORTS", p_top, probs)
        elif top == 6 and p_top >= TYPE_THRESHOLD and args[a]["hid"] != args[b]["hid"]:
            add(a, b, "SHARED_WITH", p_top, probs)
            add(b, a, "SHARED_WITH", p_top, probs)
        elif top == 7 and p_top >= TYPE_THRESHOLD:
            concrete = args[b]["kind_of_view"] in ("example", "fact") or (args[b]["abstraction"] or 3) <= 2
            add(b, a, "EXAMPLE_OF" if concrete else "ELABORATES", p_top, probs)

    with db.tx(conn):
        conn.execute("DELETE FROM edges WHERE method='graded'")
        conn.executemany(
            "INSERT OR REPLACE INTO edges(src, dst, type, weight, method, evidence_json) VALUES (?,?,?,?,?,?)",
            [(s, d, t, w, "graded", json.dumps({str(k): round(v, 3) for k, v in p.items()}))
             for (s, d, t), (w, p) in edges.items()
             if not conn.execute("SELECT 1 FROM edges WHERE src=? AND dst=? AND type=? AND method='manual'",
                                 (s, d, t)).fetchone()])
    counts = collections.Counter(t for (_, _, t) in edges)
    cross_doc = sum(1 for (s, d, t) in edges if t == "CONTRADICTS" and _docs(conn, s).isdisjoint(_docs(conn, d)))
    log(f"  relations: {dict(counts)} (directed edges; CONTRADICTS between different documents: {cross_doc})")


def _safe(conn, prompt):
    try:
        return _judge(conn, prompt)
    except llm.LLMError as e:
        log(f"    judgement skipped (retried next run): {e}")
        return None


def _docs(conn, arg_id: str) -> set:
    return {r[0] for r in conn.execute("SELECT DISTINCT m.item_id FROM argument_mentions am JOIN mentions m "
                                       "ON m.id=am.mention_id WHERE am.argument_id=?", (arg_id,))}
