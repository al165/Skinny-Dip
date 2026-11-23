"""Stage 5 (corpus-wide): mentions -> holders -> arguments.

Holders. Every mention's holder name becomes a row in `holders`:
  - the document's author (author pass, or a mention whose holder is the item's author): one
    holder per real author name across documents; a placeholder author ("the author of this
    article on techpolicy.press") stays per document, since two articles on one site can have
    different authors;
  - unspecified holders ("unspecified", "some people") stay per document: they are not one voice;
  - named holders merge across documents when their names match (fuzzy ratio >= NAME_MATCH
    after dropping "the" and punctuation), so "Emily M. Bender" / "Emily Bender" are one holder.
    Holders stay separate otherwise ("the AI industry" and "Gebru" never merge; SHARED_WITH
    edges in stage 8 link them when they hold the same view).

Arguments. Within one holder, mentions that make the same point merge into one argument,
across documents too. Candidate pairs: same holder and either a shared quote or embedding
cosine >= PAIR_COSINE (calibrated on the test run: real duplicates score 0.86-0.95, 99% of
other same-holder pairs score below 0.82). The LLM then judges each candidate pair, strictly.
(Asking it to group 30 statements at once made topic clusters, not duplicates.) Concessions (voice conceded) never merge
with the holder's other claims. An argument's id is a hash of its mentions' (document, quote),
so it survives holder renames and rebuilds.

Rebuilt from scratch on every run; the LLM grouping calls are cached, so a rerun is free."""
import collections
import hashlib
import re

import numpy as np
from rapidfuzz import fuzz

from dipa import config, db, parse
from dipa.ingest.common import ask_all, fmt_secs, log
from dipa.llm import LLMError

NAME_MATCH = 92
PAIR_COSINE = 0.85
PAIRS_PER_CALL = 10
UNSPECIFIED = {"unspecified", "some people", "some", "others", "people", "many", "some argue", "it"}

PAIR_SCHEMA = {"type": "object", "properties": {"verdicts": {"type": "array", "items": {
    "type": "object", "properties": {"pair": {"type": "integer"}, "same": {"type": "boolean"}},
    "required": ["pair", "same"]}}}, "required": ["verdicts"]}

PAIR_PROMPT = """Each numbered pair below holds two statements attributed to the same holder: {holder}{desc}.
For each pair decide: do A and B make the SAME claim, so that a reader who has read one learns nothing new from the other?

same = true only if they assert the same thing, perhaps in different words.
same = false if they are only on the same topic; if one is broader or narrower; if they give different reasons, examples, figures, dates or events; or if one is a question or definition and the other a claim. When unsure, false.

Return one verdict for EVERY pair, in order ({n} pairs).

PAIRS:
{pairs}"""


def norm_name(name: str) -> str:
    n = re.sub(r"[’'`\"“”.,]", "", name.strip().lower())
    n = re.sub(r"^(the|a|an)\s+", "", n)
    return re.sub(r"\s+", " ", n)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "holder"


def mention_key(sha: str, m) -> str:
    """Stable across stage-5 rebuilds and holder renames. Includes the pass and statement: the
    author and voices passes often quote the same sentence (a view and the author's rejection
    of it), and those must stay two arguments."""
    q = re.sub(r"\s+", " ", m["quote"].strip().lower())
    st = re.sub(r"\s+", " ", m["statement"].strip().lower())
    return f"{sha[:16]}:{m['pass']}:{q}:{st}"


# --- holders -------------------------------------------------------------------------------

def _file(item) -> str:
    return parse.source_name(item["path"])


def build_holders(conn) -> dict[int, int]:
    """Fills `holders` and returns {mention_id: holder_id}.

    Each group of mentions gets holder keys (wiki.holder_key): "name:<normalised name>" for every
    spelling of a named holder, "author:<name>", "author-of:<file>" for a placeholder author,
    "unspecified-in:<file>". Holder pages in the wiki claim keys (holder_rules): groups whose
    keys a page claims become one holder with the page's name, type and description."""
    items = {r["id"]: r for r in conn.execute("SELECT * FROM items")}
    cast = collections.defaultdict(list)
    for c in conn.execute("SELECT * FROM cast"):
        cast[c["item_id"]].append(c)
    ms = conn.execute("SELECT * FROM mentions").fetchall()
    rules = {r["key"]: r for r in conn.execute("SELECT * FROM holder_rules")}

    groups: dict[tuple, dict] = {}
    named_keys: list[tuple[str, tuple]] = []

    def key_for(m) -> tuple:
        item = items[m["item_id"]]
        author = (item["author"] or "").strip()
        if m["holder"].strip().lower() == author.lower():
            if author.lower().startswith("the author of"):
                return ("author-of", _file(item))
            return ("author", norm_name(author))
        n = norm_name(m["holder"])
        if m["holder_type"] == "unspecified" or n in UNSPECIFIED:
            return ("unspecified-in", _file(item))
        if f"name:{n}" in rules:  # a holder page claims this exact spelling
            return ("named", n)
        for other, k in named_keys:
            if fuzz.ratio(n, other) >= NAME_MATCH:
                return k
        k = ("named", n)
        named_keys.append((n, k))
        return k

    for m in ms:
        k = key_for(m)
        g = groups.setdefault(k, {"names": collections.Counter(), "types": collections.Counter(),
                                  "items": set(), "mentions": [], "keys": set()})
        g["names"][m["holder"].strip()] += 1
        g["types"][m["holder_type"]] += 1
        g["items"].add(m["item_id"])
        g["mentions"].append(m["id"])
        # every spelling of a named holder is a key; other holders have one key each
        g["keys"].add(f"name:{norm_name(m['holder'])}" if k[0] == "named" else f"{k[0]}:{k[1]}")

    # holder pages: groups whose keys a page claims merge under that page
    final: dict[tuple, dict] = {}
    for k, g in groups.items():
        page = next((rules[x]["page"] for x in sorted(g["keys"]) if x in rules), None)
        fk = ("page", page) if page else k
        f = final.setdefault(fk, {"names": collections.Counter(), "types": collections.Counter(), "items": set(),
                                  "mentions": [], "keys": set(), "kinds": set()})
        f["names"].update(g["names"])
        f["types"].update(g["types"])
        f["items"] |= g["items"]
        f["mentions"] += g["mentions"]
        f["keys"] |= g["keys"]
        f["kinds"].add(k[0] if k[0] != "named" else "named")

    with db.tx(conn):
        conn.execute("DELETE FROM argument_mentions")
        conn.execute("DELETE FROM nodes WHERE kind='argument'")  # they point at holders; rebuilt next
        conn.execute("UPDATE mentions SET holder_id=NULL")
        conn.execute("DELETE FROM holders")
        slugs, ids, of_mention = set(), {}, {}
        for fk, g in final.items():
            rule = next((rules[x] for x in sorted(g["keys"]) if x in rules), None)
            iid = next(iter(g["items"]))
            if rule:
                name, htype, desc = rule["name"], rule["type"], rule["description"]
                slug_base = rule["page"].removesuffix(".md")
            elif "unspecified-in" in g["kinds"]:
                name, htype = "unspecified", "unspecified"
                slug_base = f"unspecified-in-{slugify(items[iid]['title'])[:40]}"
                desc = f"views the text of “{items[iid]['title']}” attributes to no one in particular"
            elif g["kinds"] & {"author", "author-of"}:
                name, htype = g["names"].most_common(1)[0][0], "author"
                slug_base = slugify(name if not name.lower().startswith("the author of")
                                    else f"author-of-{items[iid]['title']}")
                desc = "author of " + "; ".join(f"“{items[i]['title']}”" for i in sorted(g["items"]))
            else:
                top = max(g["names"].values())   # most frequent spelling; ties go to the longest
                name = max((n for n, c in g["names"].items() if c == top), key=len)
                htype = g["types"].most_common(1)[0][0]
                if htype == "author":  # a reassigned author-pass mention that names someone
                    htype = next((t for t, _ in g["types"].most_common() if t != "author"), "unspecified")
                slug_base = slugify(name)
                desc = _describe(name, [c for i in g["items"] for c in cast[i]])
            slug, n = slug_base, 2
            while slug in slugs:
                slug, n = f"{slug_base}-{n}", n + 1
            slugs.add(slug)
            per_doc = iid if len(g["items"]) == 1 and g["kinds"] <= {"unspecified-in", "author-of"} else None
            cur = conn.execute(
                "INSERT INTO holders(slug, name, type, description, aliases_json, item_id, keys_json, page) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (slug, name, htype, desc, _json(sorted(g["names"])), per_doc, _json(sorted(g["keys"])),
                 rule["page"] if rule else None))
            for mid in g["mentions"]:
                of_mention[mid] = cur.lastrowid
        for mid, hid in of_mention.items():
            conn.execute("UPDATE mentions SET holder_id=? WHERE id=?", (hid, mid))
    log(f"  holders: {len(final)} ({sum(1 for f in final if f[0] == 'page')} set by holder pages)")
    return of_mention


def _describe(name: str, cast_rows) -> str | None:
    best = max(cast_rows, key=lambda c: fuzz.token_set_ratio(norm_name(name), norm_name(c["name"])), default=None)
    if best is not None and fuzz.token_set_ratio(norm_name(name), norm_name(best["name"])) >= 90:
        return best["description"]
    return None


def _json(x) -> str:
    import json
    return json.dumps(x, ensure_ascii=False)


# --- arguments -----------------------------------------------------------------------------

def _candidates(group, vec_of, items) -> list[tuple]:
    """Same-holder pairs worth asking about: a shared quote, or cosine >= PAIR_COSINE."""
    from dipa.ingest.s3_extract import _same_text
    out = []
    for a in range(len(group)):
        for b in range(a + 1, len(group)):
            ma, mb = group[a], group[b]
            if float(vec_of[ma["id"]] @ vec_of[mb["id"]]) >= PAIR_COSINE or _same_text(ma["quote"], mb["quote"]):
                out.append((ma, mb))
    return out


def build_arguments(conn):
    items = {r["id"]: r for r in conn.execute("SELECT * FROM items")}
    holders = {r["id"]: r for r in conn.execute("SELECT * FROM holders")}
    ms = conn.execute("SELECT * FROM mentions WHERE holder_id IS NOT NULL ORDER BY item_id, id").fetchall()
    parts = collections.defaultdict(list)
    for m in ms:
        parts[(m["holder_id"], m["voice"] == "conceded")].append(m)

    vecs = db.embed_cached(conn, [m["statement"] for m in ms])
    vec_of = {m["id"]: v for m, v in zip(ms, vecs)}

    jobs, prompts = [], []  # one prompt per PAIRS_PER_CALL candidate pairs of one holder
    for (hid, _), group in parts.items():
        cands = _candidates(group, vec_of, items)
        h = holders[hid]
        for i in range(0, len(cands), PAIRS_PER_CALL):
            batch = cands[i:i + PAIRS_PER_CALL]
            listed = "\n".join(f"{k + 1}. A: {a['statement']}\n   B: {b['statement']}" for k, (a, b) in enumerate(batch))
            prompts.append(PAIR_PROMPT.format(holder=h["name"], n=len(batch), pairs=listed,
                                              desc=f" ({h['description']})" if h["description"] else ""))
            jobs.append(batch)
    try:
        answers = ask_all(conn, "merge_pairs", prompts, PAIR_SCHEMA)
    except LLMError as e:  # e.g. a wiki save with Ollama offline: cached pairs only, the rest stay apart
        log(f"  arguments: LLM unavailable ({e}); pairs not in the cache are treated as different")
        answers = []
        for p in prompts:
            try:
                answers.append(ask_all(conn, "merge_pairs", [p], PAIR_SCHEMA)[0])
            except LLMError:
                answers.append(({"verdicts": []}, 0.0, False))
    secs = sum(a[1] for a in answers)

    parent = {m["id"]: m["id"] for m in ms}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    n_pairs = sum(len(b) for b in jobs)
    n_same = 0
    for batch, (out, _, _) in zip(jobs, answers):
        same = {v["pair"] for v in out["verdicts"] if v["same"]}
        for k, (a, b) in enumerate(batch):
            if k + 1 in same:
                parent[find(a["id"])] = find(b["id"])
                n_same += 1

    clusters = collections.defaultdict(list)
    for m in ms:
        clusters[find(m["id"])].append(m)

    kept = {r["id"]: r for r in conn.execute(
        "SELECT id, title, summary_tpl, abstraction, model, locked, origin FROM nodes WHERE kind='argument'")}
    kept |= {r["id"]: r for r in conn.execute("SELECT * FROM _kept_args")} if _has_kept(conn) else {}
    with db.tx(conn):
        conn.execute("DELETE FROM argument_mentions")
        conn.execute("DELETE FROM nodes WHERE kind='argument'")
        used = set()
        # deterministic order, so a collision suffix lands on the same argument every run
        for members in sorted(clusters.values(), key=lambda ms: min(m["id"] for m in ms)):
            keys = sorted(mention_key(items[m["item_id"]]["sha256"], m) for m in members)
            aid = base = "a" + hashlib.sha1("|".join(keys).encode()).hexdigest()[:10]
            n = 2
            while aid in used:  # the same sentence extracted twice with different holders/voices
                aid, n = f"{base}-{n}", n + 1
            used.add(aid)
            kind = collections.Counter(m["kind"] for m in members).most_common(1)[0][0]
            conn.execute("INSERT INTO nodes(id, kind, level, holder_id, kind_of_view) VALUES (?,?,?,?,?)",
                         (aid, "argument", 1, members[0]["holder_id"], kind))
            for m in members:
                conn.execute("INSERT OR IGNORE INTO argument_mentions VALUES (?,?)", (aid, m["id"]))
            if aid in kept and kept[aid]["summary_tpl"]:  # same mentions as before: keep its summary
                k = kept[aid]
                conn.execute("UPDATE nodes SET title=?, summary_tpl=?, abstraction=?, model=?, locked=?, origin=? "
                             "WHERE id=?", (k["title"], k["summary_tpl"], k["abstraction"], k["model"],
                                            k["locked"], k["origin"], aid))
    multi = sum(len(v) > 1 for v in clusters.values())
    cross = sum(len({m["item_id"] for m in v}) > 1 for v in clusters.values())
    log(f"  arguments: {len(clusters)} from {len(ms)} mentions · {n_same}/{n_pairs} candidate pairs judged the "
        f"same ({multi} arguments from 2+ mentions, {cross} across documents) · LLM time {fmt_secs(secs)}")


def _has_kept(conn) -> bool:
    return bool(conn.execute("SELECT 1 FROM sqlite_master WHERE name='_kept_args'").fetchone())


def run(conn):
    # build_holders deletes the argument nodes (they point at holders); keep their summaries
    # aside so build_arguments can restore them for arguments that survive the rebuild
    # a tree built before placements existed: record it now, before the argument nodes go
    if not conn.execute("SELECT 1 FROM placements LIMIT 1").fetchone():
        conn.execute("INSERT INTO placements SELECT a.id, a.parent_id, 'auto' FROM nodes a "
                     "JOIN nodes i ON i.id=a.parent_id AND i.kind='issue' WHERE a.kind='argument'")
    conn.execute("DROP TABLE IF EXISTS _kept_args")
    conn.execute("CREATE TABLE _kept_args AS SELECT id, title, summary_tpl, abstraction, model, locked, origin "
                 "FROM nodes WHERE kind='argument' AND summary_tpl IS NOT NULL")
    conn.commit()
    build_holders(conn)
    build_arguments(conn)
    conn.execute("DROP TABLE IF EXISTS _kept_args")
    conn.commit()
    from dipa.ingest.s6_summarise import rerender
    rerender(conn)  # kept summaries get the (possibly renamed) holder
