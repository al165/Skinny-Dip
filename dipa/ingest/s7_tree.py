"""Stage 7 (corpus-wide): the zoom-out tree. Arguments -> issues (questions) -> themes.

Arguments are clustered by k-means on embeddings of their title and holder-free summary (so they
group by idea, not by who holds them or which document they come from). Each issue is named
as the question its arguments answer, with a state-of-the-debate summary that says who holds
what. Issues are clustered again into themes.

Append-only: once the tree exists, a run keeps its issues and themes (and every text people
edited in the wiki). Arguments without a placement (new, or re-extracted) join the issue whose
centroid is nearest, if they are about as close to it as its own members usually are (the 5th
percentile of member-to-centroid similarity); otherwise they go to unsorted (wiki/unsorted.md).
No LLM calls. --recluster rebuilds the tree from scratch and drops manual placements.
Placements live in the `placements` table, keyed by argument id, so they survive stage 5."""
import collections
import hashlib
import math
import re

import numpy as np

from dipa import config, db
from dipa.ingest.common import ask_all, fmt_secs, log

ABSTRACTION = """abstraction: 1-5, the scope of what this issue or theme covers.
  3 = a point about one particular topic, 4 = a general question across many cases, 5 = a sweeping question about technology, society or the world"""

ISSUE_SCHEMA = {"type": "object", "properties": {
    "question": {"type": "string"}, "summary": {"type": "string"},
    "abstraction": {"type": "integer", "minimum": 1, "maximum": 5}},
    "required": ["question", "summary", "abstraction"]}

ISSUE_PROMPT = """These {n} views were grouped together because they address related questions. Each line: who holds it, then the view.

{body}

Return JSON with:
- question: the one open question these views answer or take sides on, as a question of at most 12 words ("Is AGI a useful goal for AI research?", "Who should decide what data AI is trained on?"). Specific, not generic ("What about AI?" is too vague).
- summary: 2-4 sentences on the state of the debate: the main positions, who holds each (use the holders' names as given), and where they disagree. If everyone agrees, say so. Only use what the views say.
- {abstraction}"""

THEME_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "summary": {"type": "string"},
    "abstraction": {"type": "integer", "minimum": 1, "maximum": 5}},
    "required": ["title", "summary", "abstraction"]}

THEME_PROMPT = """These {n} questions are debated in a research corpus about AI and design. They were grouped together because they are related.

{body}

Return JSON with:
- title: a 2-6 word name for what they share (specific, not "AI discussion").
- summary: 2-4 sentences on the theme and its main tensions.
- {abstraction}"""


def holder_free(tpl: str | None, title: str | None) -> str:
    """Title + summary without the holder's name, for clustering by idea."""
    body = re.sub(r"\{holder\}\s+\w+\s+(that\s+)?", "", tpl or "")
    return f"{title or ''}. {body}".strip()


def _kmeans(mat: np.ndarray, k: int) -> np.ndarray:
    from sklearn.cluster import KMeans  # here, not at the top: the API imports this module and has no sklearn
    k = max(1, min(k, len(mat)))
    if k == 1:
        return np.zeros(len(mat), dtype=int)
    return KMeans(n_clusters=k, n_init=10, random_state=0).fit_predict(mat)


def _node_id(prefix: str, members: list[str]) -> str:
    return prefix + hashlib.sha1("|".join(sorted(members)).encode()).hexdigest()[:10]


def run(conn, recluster: bool = False):
    from dipa import wiki
    if not recluster and conn.execute("SELECT count(*) FROM nodes WHERE kind='issue'").fetchone()[0]:
        place_new(conn)
        wiki.apply_overrides(conn)
        return
    build(conn)
    wiki.apply_overrides(conn)


def place_new(conn):
    """Keep the tree; put arguments without a placement into the nearest issue or unsorted."""
    args = conn.execute("SELECT * FROM nodes WHERE kind='argument' AND summary IS NOT NULL ORDER BY id").fetchall()
    issues = {r["id"] for r in conn.execute("SELECT id FROM nodes WHERE kind='issue'")}
    placed = {r["argument_id"]: r for r in conn.execute("SELECT * FROM placements")}
    if not placed:  # a tree built before placements existed: adopt it
        conn.executemany("INSERT INTO placements VALUES (?,?, 'auto')",
                         [(a["id"], a["parent_id"]) for a in args if a["parent_id"] in issues])
        conn.commit()
        placed = {r["argument_id"]: r for r in conn.execute("SELECT * FROM placements")}
    vecs = db.embed_cached(conn, [holder_free(a["summary_tpl"], a["title"]) for a in args])
    vec_of = {a["id"]: v for a, v in zip(args, vecs)}
    members = collections.defaultdict(list)
    for aid, p in placed.items():
        if p["issue_id"] in issues and aid in vec_of:
            members[p["issue_id"]].append(vec_of[aid])
    ids = sorted(members)
    if not ids:
        log("  tree: no argument is placed in an existing issue; building the tree from scratch")
        build(conn)
        return
    cents = np.stack([np.mean(members[i], axis=0) for i in ids])
    cents /= np.linalg.norm(cents, axis=1, keepdims=True)
    own = [float(v @ cents[k]) for k, i in enumerate(ids) for v in members[i]]
    floor = float(np.percentile(own, 5)) if own else 0.0
    new_in, new_out = 0, 0
    with db.tx(conn):
        for a in args:
            p = placed.get(a["id"])
            if p is not None and (p["issue_id"] is None or p["issue_id"] in issues):
                continue  # placed already (or deliberately unsorted)
            sims = cents @ vec_of[a["id"]]
            best = int(np.argmax(sims))
            issue = ids[best] if sims[best] >= floor else None
            conn.execute("INSERT OR REPLACE INTO placements VALUES (?,?, 'auto')", (a["id"], issue))
            new_in += issue is not None
            new_out += issue is None
    unsorted = conn.execute("SELECT count(*) FROM placements WHERE issue_id IS NULL").fetchone()[0]
    log(f"  tree: kept {len(issues)} issues; placed {new_in} new argument(s), {new_out} to unsorted "
        f"(similarity floor {floor:.2f}); {unsorted} unsorted in all · no LLM calls (--recluster rebuilds)")


def build(conn):
    """The whole tree from scratch: cluster, name every issue and theme with the LLM."""
    args = conn.execute(
        "SELECT n.*, h.name AS holder FROM nodes n JOIN holders h ON h.id=n.holder_id "
        "WHERE n.kind='argument' AND n.summary IS NOT NULL ORDER BY n.id").fetchall()
    if len(args) < 4:
        log("  tree: too few summarised arguments (run stage 6 first)")
        return
    vecs = db.embed_cached(conn, [holder_free(a["summary_tpl"], a["title"]) for a in args])
    k = config.ISSUE_COUNT or round(1.2 * math.sqrt(len(args)))
    labels = _kmeans(vecs, k)
    groups = [[args[j] for j in np.where(labels == c)[0]] for c in sorted(set(labels))]

    prompts = []
    for g in groups:
        per = max(120, 7000 // len(g))
        body = "\n".join(f"- [{a['holder']}] {a['summary'][:per]}" for a in g)
        prompts.append(ISSUE_PROMPT.format(n=len(g), body=body, abstraction=ABSTRACTION))
    answers = ask_all(conn, "issue", prompts, ISSUE_SCHEMA)
    secs = sum(a[1] for a in answers)

    with db.tx(conn):
        conn.execute("UPDATE nodes SET parent_id=NULL WHERE kind='argument'")
        conn.execute("DELETE FROM nodes WHERE kind IN ('issue', 'theme')")
        n_manual = conn.execute("SELECT count(*) FROM placements WHERE method='manual'").fetchone()[0]
        if n_manual:
            log(f"  tree: rebuilding drops {n_manual} manual placement(s) and edited issue/theme texts")
        conn.execute("DELETE FROM placements")
        conn.execute("DELETE FROM overrides WHERE target LIKE 'i%' OR target LIKE 't%'")
        issues = []
        for g, (out, _, _) in zip(groups, answers):
            iid = _node_id("i", [a["id"] for a in g])
            conn.execute("INSERT INTO nodes(id, kind, level, title, summary, abstraction, origin, model) "
                         "VALUES (?,?,?,?,?,?,?,?)", (iid, "issue", 2, out["question"].strip(), out["summary"].strip(),
                                                      out["abstraction"], "synthesised", config.LLM_MODEL))
            conn.executemany("UPDATE nodes SET parent_id=? WHERE id=?", [(iid, a["id"]) for a in g])
            conn.executemany("INSERT OR REPLACE INTO placements VALUES (?,?, 'auto')", [(a["id"], iid) for a in g])
            issues.append((iid, out["question"].strip(), out["summary"].strip()))

    ivecs = db.embed_cached(conn, [f"{q} {s}" for _, q, s in issues])
    k2 = config.THEME_COUNT or max(2, round(math.sqrt(len(issues))))
    labels2 = _kmeans(ivecs, k2)
    tgroups = [[issues[j] for j in np.where(labels2 == t)[0]] for t in sorted(set(labels2))]
    tprompts = [THEME_PROMPT.format(n=len(g), abstraction=ABSTRACTION,
                                    body="\n".join(f"- {q} ({s[:300]})" for _, q, s in g)) for g in tgroups]
    tanswers = ask_all(conn, "theme", tprompts, THEME_SCHEMA)
    secs += sum(a[1] for a in tanswers)
    with db.tx(conn):
        for g, (out, _, _) in zip(tgroups, tanswers):
            tid = _node_id("t", [i for i, _, _ in g])
            conn.execute("INSERT INTO nodes(id, kind, level, title, summary, abstraction, origin, model) "
                         "VALUES (?,?,?,?,?,?,?,?)", (tid, "theme", 3, out["title"].strip(), out["summary"].strip(),
                                                      out["abstraction"], "synthesised", config.LLM_MODEL))
            conn.executemany("UPDATE nodes SET parent_id=? WHERE id=?", [(tid, i) for i, _, _ in g])
    sizes = collections.Counter(len(g) for g in groups)
    log(f"  tree: {len(args)} arguments -> {len(groups)} issues (sizes {min(sizes)}-{max(sizes)}) -> "
        f"{len(tgroups)} themes · LLM time {fmt_secs(secs)}")
