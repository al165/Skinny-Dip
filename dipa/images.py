"""Images: an illustrative picture for any argument, issue, theme or passage (ported from
dip-memory's stage 7 and Images action).

    python -m dipa.ingest.run --stages 10     # draft missing pages, load them, embed
    GET /images?node_id=…                     # pictures for a node (subject + metaphor)
    GET /image-passages/{slug}                # the reverse: arguments a picture fits

Library: image files in IMAGE_DIR, one page each in wiki/images/<slug>.md (front matter: title,
file, source, creator, license, credit; sections Description, Subjects, Metaphors as
"- keyword: gloss"). The pages are the source of truth and editable in the wiki editor; a
missing page is drafted (the vision model describes the picture, the LLM proposes metaphors;
credit from <file>.source.txt next to the image).

Each image is embedded three ways: its description (+ subjects), the picture itself (gemma
puts text and pictures in one space; embedding server /embed-images) and each metaphor line.

Search, as in dip-memory:
  subject route   the node's vector vs description and picture vectors, rank-fused
  metaphor route  the node's abstract concepts (LLM, cached) vs metaphor lines; an image scores
                  its best (concept, metaphor) pair
  The two routes alternate. Every score is hubness-corrected: an image vector's similarity to
  the corpus centroid is subtracted, otherwise one vaguely-about-everything picture tops every list.
"""
import collections
import hashlib
import json
import re
import time
from pathlib import Path

import numpy as np

from dipa import config, db, llm
from dipa.ingest.common import ask, log

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
CREDIT_FIELDS = ("source", "creator", "license", "credit")
NEAR_DUPLICATE = 0.95
REVERSE_CENTRE = {"subject": 1.0, "metaphor": 0.25}   # dip-memory's tuned reverse-search correction

DESCRIBE_PROMPT = (
    "Describe this image for a research archive, as JSON: `title` (a short title), "
    "`description` (one paragraph describing what is shown, including any visible text) and "
    "`subjects` (3-8 short phrases naming the things, people, places and materials shown).{hint}")
DESCRIBE_FROM_TEXT_PROMPT = """An image in an archive comes with this catalogue text:

{text}

From it, write as JSON: `title` (a short title), `description` (one paragraph describing what
the image most likely shows, without inventing details) and `subjects` (3-8 short phrases
naming the things, people, places and materials shown)."""
DESCRIBE_SCHEMA = {"type": "object", "properties": {
    "title": {"type": "string"}, "description": {"type": "string"},
    "subjects": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "description", "subjects"]}
METAPHOR_PROMPT = """An image shows:

{title}. {description}
Subjects: {subjects}

A design researcher wants to use this image as a METAPHOR next to texts about technology,
society and culture. What ideas, relationships, structures or processes could it stand for?
Think about how its parts relate (connection, growth, hierarchy, exchange, decay, care, scale,
control, memory...), not about what it literally is.

Give `metaphors`: 4-6 items, each with `keyword` (1-3 words, e.g. "networks", "mutual aid")
and `gloss` (one short sentence saying what the image says about that idea, e.g. "many small
threads exchanging resources with no centre"). Do not just repeat the subjects."""
METAPHOR_SCHEMA = {"type": "object", "properties": {"metaphors": {"type": "array", "minItems": 3, "maxItems": 6, "items": {
    "type": "object", "properties": {"keyword": {"type": "string"}, "gloss": {"type": "string"}},
    "required": ["keyword", "gloss"]}}}, "required": ["metaphors"]}
CONCEPT_PROMPT = """Text:
{text}

What is this text about at the level of IDEAS, not objects? Give `concepts`: {n} short
phrases (4-10 words each) naming the abstract ideas, relationships, processes or tensions it
deals with, e.g. "many small parts acting together without a centre" or "knowledge handed down
between generations". Do not name the concrete things in the text (technologies,
institutions, places, people); say what kind of situation they are in."""
CONCEPT_SCHEMA = {"type": "object", "properties": {"concepts": {"type": "array", "items": {"type": "string"}}},
                  "required": ["concepts"]}


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "image"


# --- pages ---------------------------------------------------------------------------------

def read_source_file(img: Path) -> dict:
    """Credit (and an optional catalogue description) from `<file>.source.txt` next to the image."""
    p = img.with_name(img.name + ".source.txt")
    out = {}
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        m = re.match(r"^(source|creator|license|credit|description):\s*(.*)$", line, flags=re.I)
        if m:
            out[m.group(1).lower()] = m.group(2).strip()
        elif re.match(r"^https?://", line) and "source" not in out:
            out["source"] = line
        elif line and "credit" not in out:
            out["credit"] = line
    return out


def parse_page(text: str) -> dict:
    out = {"description": "", "subjects": [], "metaphors": []}
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, flags=re.S)
    head, body = (m.group(1), m.group(2)) if m else ("", text)
    for line in head.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    sections, cur = {}, None
    for line in body.splitlines():
        h = re.match(r"^##\s+(.*)$", line)
        if h:
            cur = h.group(1).strip().lower()
            sections[cur] = []
        elif cur:
            sections[cur].append(line)
    out["description"] = " ".join(" ".join(sections.get("description", [])).split())
    for line in sections.get("subjects", []):
        if line.strip().startswith(("-", "*")):
            out["subjects"] += [s.strip() for s in line.strip()[1:].split(",") if s.strip()]
    for line in sections.get("metaphors", []):
        if line.strip().startswith(("-", "*")):
            kw, _, gloss = line.strip()[1:].strip().partition(":")
            if kw.strip():
                out["metaphors"].append({"keyword": kw.strip(), "gloss": gloss.strip()})
    return out


def render_page(d: dict) -> str:
    head = [f"title: {d['title']}", f"file: {d['file']}"]
    head += [f"{k}: {d.get(k) or 'TODO'}" for k in CREDIT_FIELDS]
    head.append("locked: false")
    lines = ["---", *head, "---", f"# {d['title']}", "", "## Description", d["description"], "",
             "## Subjects", "- " + ", ".join(d["subjects"]), "", "## Metaphors"]
    lines += [f"- {m['keyword']}: {m['gloss']}" for m in d["metaphors"]]
    return "\n".join(lines) + "\n"


def draft(img: Path, rel: str) -> dict:
    """Describe the image (vision model; else the .source.txt description) and propose metaphors."""
    src = read_source_file(img)
    hint = f"\nCatalogue text for context: {src['description']}" if src.get("description") else ""
    try:
        d = llm.generate(DESCRIBE_PROMPT.format(hint=hint), schema=DESCRIBE_SCHEMA,
                         images=[img.read_bytes()], model=config.VLM_MODEL)
    except llm.LLMError as e:
        if not src.get("description"):
            raise
        log(f"    {img.name}: vision model unavailable ({e}); describing from .source.txt")
        d = llm.generate(DESCRIBE_FROM_TEXT_PROMPT.format(text=src["description"]), schema=DESCRIBE_SCHEMA)
    d = {"title": d["title"].strip() or img.stem, "description": " ".join(d["description"].split()),
         "subjects": [s.strip() for s in d["subjects"] if s.strip()]}
    if garbled(d["title"] + " " + d["description"]):
        raise llm.LLMError(f"the vision model returned garbled text for {img.name}; describe it by hand or retry")
    out = llm.generate(METAPHOR_PROMPT.format(title=d["title"], description=d["description"],
                                              subjects=", ".join(d["subjects"])), schema=METAPHOR_SCHEMA, temperature=0.5)
    d["metaphors"] = [{"keyword": m["keyword"].strip().lower(), "gloss": m["gloss"].strip()}
                      for m in out["metaphors"] if m["keyword"].strip()]
    return {**d, "file": rel, **{k: src.get(k, "") for k in CREDIT_FIELDS}}


def garbled(text: str) -> bool:
    """A vision-model draft that came out as token soup (seen on the Novum Organum scan): mostly
    non-Latin characters, or very short."""
    letters = [c for c in text if c.isalpha()]
    latin = sum(c.isascii() for c in letters)
    return len(text.strip()) < 15 or (bool(letters) and latin / len(letters) < 0.8)


def image_files() -> list[Path]:
    if not config.IMAGE_DIR.exists():
        return []
    return sorted(p for p in config.IMAGE_DIR.rglob("*") if p.suffix.lower() in IMAGE_EXTS)


def _page_for(files: list[Path]) -> dict[Path, Path]:
    """Each image's page: an existing page whose `file:` names it, else <slug>.md."""
    wiki = config.IMAGE_WIKI_DIR
    by_file = {}
    for p in sorted(wiki.glob("*.md")) if wiki.is_dir() else []:
        f = parse_page(p.read_text(encoding="utf-8")).get("file")
        if f:
            by_file[f] = p
    out, used = {}, set()
    for img in files:
        rel = str(img.relative_to(config.IMAGE_DIR))
        if rel in by_file:
            out[img] = by_file[rel]
            continue
        base = slugify(str(Path(rel).with_suffix("")))
        slug, n = base, 2
        while slug in used or (wiki / f"{slug}.md").exists():
            slug, n = f"{base}-{n}", n + 1
        used.add(slug)
        out[img] = wiki / f"{slug}.md"
    return out


def sync_pages() -> list[tuple[Path, Path]]:
    """Draft a page for every image that has none. Returns (image, page) pairs."""
    config.IMAGE_WIKI_DIR.mkdir(parents=True, exist_ok=True)
    pairs = _page_for(image_files())
    todo = [img for img, page in pairs.items() if not page.exists()]
    if todo:
        log(f"  images: drafting {len(todo)} page(s) with {config.VLM_MODEL} + {config.LLM_MODEL}")
    for img in todo:
        t0 = time.time()
        try:
            pairs[img].write_text(render_page(draft(img, str(img.relative_to(config.IMAGE_DIR)))), encoding="utf-8")
            log(f"    drafted {img.name} ({time.time() - t0:.0f}s)")
        except llm.LLMError as e:
            log(f"    FAILED {img.name}: {e}")
    return [(img, page) for img, page in pairs.items() if page.exists()]


def load_pages(conn, pairs) -> int:
    """Rebuild the images tables from the pages (unchanged rows are kept)."""
    rows = {r["slug"]: r for r in conn.execute("SELECT * FROM images")}
    keep, changed = set(), 0
    with db.tx(conn):
        for img, page in pairs:
            slug, text = page.stem, page.read_text(encoding="utf-8")
            file_sha, page_sha = _sha(img.read_bytes()), _sha(text.encode())
            keep.add(slug)
            old = rows.get(slug)
            if old and old["file_sha"] == file_sha and old["page_sha"] == page_sha:
                if old["path"] != str(img) or old["page"] != str(page):  # same files, new location
                    conn.execute("UPDATE images SET path=?, page=? WHERE id=?", (str(img), str(page), old["id"]))
                continue
            if old:
                conn.execute("DELETE FROM images WHERE id=?", (old["id"],))
            d = parse_page(text)
            iid = conn.execute(
                "INSERT INTO images(slug, path, page, title, description, subjects_json, source, creator, license, "
                "credit, file_sha, page_sha) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (slug, str(img), str(page), d.get("title") or slug, d["description"], json.dumps(d["subjects"]),
                 *[None if (d.get(k) or "TODO") == "TODO" else d[k] for k in CREDIT_FIELDS], file_sha, page_sha)).lastrowid
            conn.executemany("INSERT INTO image_metaphors(image_id, keyword, gloss) VALUES (?,?,?)",
                             [(iid, m["keyword"], m["gloss"]) for m in d["metaphors"]])
            changed += 1
        for slug, r in rows.items():
            if slug not in keep:
                conn.execute("DELETE FROM images WHERE id=?", (r["id"],))
    return changed


def desc_text(r) -> str:
    subjects = ", ".join(json.loads(r["subjects_json"] or "[]"))
    return f"{r['title']}. {r['description']}" + (f" Subjects: {subjects}." if subjects else "")


def metaphor_text(m) -> str:
    return f"{m['keyword']}: {m['gloss']}" if m["gloss"] else m["keyword"]


def resolve_path(stored: str) -> Path:
    """An image file as stored by the build, or the file of that name under IMAGE_DIR: a build made
    on one machine stores absolute paths that don't exist in a container or on another machine."""
    p = Path(stored)
    if p.exists():
        return p
    hits = [h for h in config.IMAGE_DIR.rglob(p.name)] if config.IMAGE_DIR.exists() else []
    return hits[0] if hits else p


def visual_vectors(conn, rows) -> dict[int, np.ndarray]:
    """Picture vectors, cached by file hash; images whose vector can't be made are left out."""
    model = config.EMBED_MODEL
    have = {r[0]: np.frombuffer(r[1], dtype=np.float32)
            for r in conn.execute("SELECT file_sha, vec FROM image_visual WHERE model=?", (model,))}
    todo = [r for r in rows if r["file_sha"] not in have and resolve_path(r["path"]).exists()]
    if todo:
        try:
            vecs = llm.embed_images([str(resolve_path(r["path"])) for r in todo])
            for r, v in zip(todo, vecs):
                v = np.asarray(v, dtype=np.float32)
                v /= np.linalg.norm(v) or 1.0
                have[r["file_sha"]] = v
                conn.execute("INSERT OR REPLACE INTO image_visual VALUES (?,?,?)", (r["file_sha"], model, v.tobytes()))
            conn.commit()
        except llm.LLMError as e:
            log(f"  images: no picture vectors ({e}); the subject route uses descriptions only")
    return {r["id"]: have[r["file_sha"]] for r in rows if r["file_sha"] in have}


def run(conn):
    """Stage 10."""
    pairs = sync_pages()
    changed = load_pages(conn, pairs)
    rows = conn.execute("SELECT * FROM images").fetchall()
    mets = conn.execute("SELECT * FROM image_metaphors").fetchall()
    db.embed_cached(conn, [desc_text(r) for r in rows])
    db.embed_cached(conn, [metaphor_text(m) for m in mets])
    vis = visual_vectors(conn, rows)
    log(f"  images: {len(rows)} in the library ({changed} new or changed page(s)), {len(mets)} metaphors, "
        f"{len(vis)} picture vectors · pages in {config.IMAGE_WIKI_DIR}")


# --- search --------------------------------------------------------------------------------

def _vectors(conn, texts: list[str]) -> list:
    """Embeddings for texts; without a model, the cached ones (None for the rest)."""
    if not texts:
        return []
    try:
        return list(db.embed_cached(conn, texts))
    except llm.LLMError:
        return db.cached_vectors(conn, texts)


def _library(conn):
    """The image library with its vectors. Images (and metaphor lines) without a description vector
    are left out, e.g. a page edited on an offline server: the rest of the library still works."""
    rows = conn.execute("SELECT * FROM images ORDER BY id").fetchall()
    mets = conn.execute("SELECT * FROM image_metaphors ORDER BY id").fetchall()
    dv = _vectors(conn, [desc_text(r) for r in rows])
    rows, D = [r for r, v in zip(rows, dv) if v is not None], [v for v in dv if v is not None]
    keep = {r["id"] for r in rows}
    mv = _vectors(conn, [metaphor_text(m) for m in mets])
    pairs = [(m, v) for m, v in zip(mets, mv) if v is not None and m["image_id"] in keep]
    mets, M = [m for m, _ in pairs], [v for _, v in pairs]
    D = np.stack(D) if D else np.zeros((0, config.EMBED_DIM), dtype=np.float32)
    M = np.stack(M) if M else np.zeros((0, config.EMBED_DIM), dtype=np.float32)
    vis = visual_vectors(conn, rows)
    return rows, mets, D, M, vis


_centroid: dict = {}


def corpus_centroid(conn, texts_fn) -> np.ndarray:
    """Mean vector of the arguments (the corpus the images are matched against)."""
    n = conn.execute("SELECT count(*), max(id) FROM nodes WHERE kind='argument'").fetchone()
    key = (str(config.DB_PATH), tuple(n))
    if key not in _centroid:
        V = db.embed_cached(conn, texts_fn())
        _centroid.clear()
        _centroid[key] = V.mean(axis=0) if len(V) else np.zeros(config.EMBED_DIM, dtype=np.float32)
    return _centroid[key]


def concepts(conn, text: str) -> tuple[list[str], str]:
    """Abstract concepts of a text (LLM, cached); falls back to the text itself."""
    try:
        out, _, _ = ask(conn, "image_concepts", CONCEPT_PROMPT.format(text=text[:3000], n=config.IMAGE_CONCEPTS),
                        CONCEPT_SCHEMA, temperature=0.3)
        cs = [c.strip() for c in out["concepts"] if c.strip()][: config.IMAGE_CONCEPTS]
        if cs:
            return cs, "llm"
    except llm.LLMError:
        pass
    return [text[:300]], "text"


def _rrf(*lists, k: int = 60) -> dict:
    out = collections.defaultdict(float)
    for lst in lists:
        for rank, (i, _) in enumerate(lst):
            out[i] += 1 / (k + rank)
    return out


def find(conn, text: str, vec: np.ndarray, centroid: np.ndarray, k: int) -> tuple[list[dict], dict]:
    """Images for a text: subject and metaphor routes, alternating. Returns (results, meta)."""
    rows, mets, D, M, vis = _library(conn)
    if not rows:
        return [], {"note": "the image library is empty: add files to IMAGE_DIR and run stage 10"}
    by_id = {r["id"]: r for r in rows}
    # subject: description and picture, each hub-corrected, fused by rank
    d = {r["id"]: float(D[j] @ vec - D[j] @ centroid) for j, r in enumerate(rows)}
    v = {i: float(x @ vec - x @ centroid) for i, x in vis.items()}
    fused = _rrf(*[sorted(x.items(), key=lambda kv: -kv[1]) for x in (d, v) if x])
    subject = [(i, d.get(i, 0.0), "subject: " + ", ".join(
        ([f"description {d[i]:+.2f}"] if i in d else []) + ([f"picture {v[i]:+.2f}"] if i in v else [])), {})
        for i in sorted(fused, key=lambda i: -fused[i])]
    # metaphor: the node's concepts against every metaphor line; an image keeps its best pair
    cs, how = concepts(conn, text)
    metaphor = []
    try:
        Q = db.embed_cached(conn, cs, query=True) if len(mets) else None
    except llm.LLMError:  # offline and not warmed up for this node: subject route only
        Q, how = None, "unavailable offline (run python -m dipa.warm)"
    if Q is not None:
        S = M @ Q.T - (M @ centroid)[:, None]
        best = {}
        for p, m in enumerate(mets):
            q = int(np.argmax(S[p]))
            if m["image_id"] not in best or S[p, q] > best[m["image_id"]][0]:
                best[m["image_id"]] = (float(S[p, q]), m, cs[q])
        metaphor = [(i, s, f"metaphor: “{m['keyword']}” ↔ “{c}” ({s:+.2f})",
                     {"metaphor": {"keyword": m["keyword"], "gloss": m["gloss"]}, "concept": c})
                    for i, (s, m, c) in sorted(best.items(), key=lambda kv: -kv[1][0])]
    # alternate the routes, skipping near-duplicate pictures
    routes = {"subject": iter(subject), "metaphor": iter(metaphor)}
    desc_of = {r["id"]: D[j] for j, r in enumerate(rows)}
    out, seen, picked = [], set(), []
    while len(out) < k and routes:
        for match in list(routes):
            for iid, score, why, extra in routes[match]:
                if iid in seen or iid not in by_id:
                    continue
                if any(float(desc_of[iid] @ p) > NEAR_DUPLICATE for p in picked):
                    continue
                seen.add(iid)
                picked.append(desc_of[iid])
                out.append(image_out(by_id[iid], score, why, match=match, **extra))
                break
            else:
                del routes[match]
            if len(out) >= k:
                break
    return out, {"concepts": cs, "concepts_from": how}


def image_out(r, score: float = 0.0, why: str = "", **extra) -> dict:
    return {"type": "image", "id": r["slug"], "kind": "image", "slug": r["slug"], "title": r["title"],
            "text": r["description"], "url": f"/image-file/{r['slug']}", "credit": r["credit"],
            "source": r["source"], "source_url": r["source"], "license": r["license"], "creator": r["creator"],
            "provenance": "llm", "document": None, "abstraction": None, "score": score, "why": why, **extra}


def reverse(conn, slug: str, arg_rows, arg_vecs: np.ndarray, k: int) -> list[tuple[str, float, str, str]]:
    """Arguments a picture fits: subject (description + picture vs argument) and metaphor (its
    metaphor lines vs argument), hub-corrected from the argument side, alternating.
    Returns [(argument id, score, why, match)]."""
    r = conn.execute("SELECT * FROM images WHERE slug=?", (slug,)).fetchone()
    if r is None or not len(arg_rows):
        return []
    rows, mets, D, M, vis = _library(conn)
    j = next((n for n, x in enumerate(rows) if x["id"] == r["id"]), None)
    if j is None:  # no vectors for this image yet (offline server): nothing to match
        return []
    A = arg_vecs
    # subject
    qs = [D[j]] + ([vis[r["id"]]] if r["id"] in vis else [])
    lib = np.vstack([D] + ([np.stack(list(vis.values()))] if vis else []))
    S = A @ np.stack(qs).T - REVERSE_CENTRE["subject"] * (A @ lib.mean(axis=0))[:, None]
    subj = sorted(((a["id"], float(S[p].max()), "subject: fits what the picture shows") for p, a in enumerate(arg_rows)),
                  key=lambda x: -x[1])
    # metaphor
    mine = [(p, m) for p, m in enumerate(mets) if m["image_id"] == r["id"]]
    meta = []
    if mine:
        Q = np.stack([M[p] for p, _ in mine])
        S = A @ Q.T - REVERSE_CENTRE["metaphor"] * (A @ M.mean(axis=0))[:, None]
        best = S.argmax(axis=1)
        meta = sorted(((a["id"], float(S[p, best[p]]), f"metaphor: “{mine[best[p]][1]['keyword']}”")
                       for p, a in enumerate(arg_rows)), key=lambda x: -x[1])
    out, seen = [], set()
    routes = {"subject": iter(subj), "metaphor": iter(meta)}
    while len(out) < k and routes:
        for match in list(routes):
            for aid, s, why in routes[match]:
                if aid in seen:
                    continue
                seen.add(aid)
                out.append((aid, s, why, match))
                break
            else:
                del routes[match]
            if len(out) >= k:
                break
    return out
