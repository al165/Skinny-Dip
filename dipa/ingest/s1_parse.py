"""Stage 1: parse a source into verbatim passages, then hide what is not content.

Hiding runs in two layers (a third, manual layer comes with the wiki's lint page):
  1. rules: contents lists, reference lists, fragments, repeated headers/footers, link lists
  2. the LLM labels each remaining passage content | meta (credits, bios, licences, navigation...)
Hidden passages stay in the table with a reason; nothing is extracted from them.
"""
import collections
import hashlib
import re
from pathlib import Path

from dipa import chunk, config, db, parse
from dipa.ingest.common import ask, ask_all, fmt_secs, log

# --- layer 1: rules -------------------------------------------------------------------------

TOC_LINE = re.compile(r"(\.{3,}|…{2,}|\s{2,})\s*\d{1,3}\s*$|^\s*\d{1,3}\s+[A-Z][^.]{2,60}$")
REF_LINE = re.compile(r"(\(\d{4}[a-z]?\)|\b(19|20)\d{2}\b.*\b(doi|https?://|pp\.|vol\.|journal|press)\b"
                      r"|^\s*\[\d+\]|^\s*\d+\.\s+[A-Z][a-z]+,\s+[A-Z])", re.I)
URL = re.compile(r"https?://\S+|\[[^\]]*\]\([^)]*\)")
# Short web boilerplate lines that chunking would otherwise glue onto real content.
BOILERPLATE = re.compile(r"^(join our newsletter|subscribe|sign up|share (this|on)|follow us|read more|"
                         r"related( articles| reading)?:?$|advertisement|listen to this|click here|"
                         r"this article (was|is) (originally )?(published|part of))", re.I)


def strip_boilerplate(sections) -> int:
    """Drop short boilerplate paragraphs in place; returns how many."""
    n = 0
    for sec in sections:
        keep = [p for p in sec.paras if not (len(p.text) < 200 and BOILERPLATE.match(p.text.strip()))]
        n += len(sec.paras) - len(keep)
        sec.paras = keep
    return n


MD_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
BARE_URL = re.compile(r"https?://\S+")


def visible(text: str, drop_links: bool = False) -> str:
    """The text a reader sees: no image markup or bare URLs; links as their anchor text (or
    removed entirely with drop_links). Rules look at this, not the raw markdown: a live blog
    with inline links ("[told CNBC](https://...)") is prose, not a link list, and dates in
    URL paths (/2026/03/) are not reference-list years."""
    t = MD_IMAGE.sub(" ", text.replace("```", " "))
    t = MD_LINK.sub("" if drop_links else r"\1", t)
    return BARE_URL.sub(" ", t)


def rule_reason(text: str, repeats: int) -> str | None:
    """Why a passage is not content, by rule; None = keep it (the LLM looks next)."""
    raw = text.strip()
    t = visible(raw).strip()
    lines = [ln for ln in t.splitlines() if ln.strip()]
    words = len(t.split())
    if repeats >= 3:
        return f"repeated {repeats}x in the document (header/footer)"
    if words < 6 and not re.search(r"[.!?]$", t):
        return "fragment under 6 words"
    if len(lines) >= 3 and sum(bool(TOC_LINE.search(ln)) for ln in lines) / len(lines) >= 0.6:
        return "contents list"
    if len(lines) >= 2 and sum(bool(REF_LINE.search(ln)) for ln in lines) / len(lines) >= 0.5:
        return "reference list"
    # mostly links: few words outside link anchors, and no real sentence among them
    outside = visible(raw, drop_links=True)
    if URL.search(raw) and len(outside.split()) < 0.3 * max(words, 1) and not has_prose(outside):
        return "mostly links"
    return None


# --- layer 2: LLM ---------------------------------------------------------------------------

META_LABELS = ["content", "contents_list", "credits_or_acknowledgements", "author_bio", "references",
               "licence_or_copyright", "navigation_or_subscribe", "image_credit_or_caption", "header_or_footer"]

META_SCHEMA = {
    "type": "object",
    "properties": {"labels": {"type": "array", "items": {
        "type": "object",
        "properties": {"passage": {"type": "integer"},
                       "label": {"type": "string", "enum": META_LABELS}},
        "required": ["passage", "label"]}}},
    "required": ["labels"],
}

META_PROMPT = """Below are numbered passages from the document "{title}".
Return one label for EVERY passage, in order ({n} passages: {ids}).
Labels:
- content: argument, explanation, information, narrative, examples, quotes, questions - anything a reader might cite. This is the normal case.
- contents_list | credits_or_acknowledgements | author_bio | references | licence_or_copyright | navigation_or_subscribe | image_credit_or_caption | header_or_footer: text ABOUT the publication rather than its subject.
Use a non-content label only if the WHOLE passage is that kind of text; a passage that mixes it with real content is content. When in doubt, choose content.

{passages}"""


# Labels the LLM over-applies to slide decks, where the chunker glues a caption or a running
# header onto real prose: those only hide a passage that has no real sentence in it.
PROSE_GUARDED = {"image_credit_or_caption", "navigation_or_subscribe", "header_or_footer", "contents_list"}
PROSE_SENTENCE_WORDS = 12


def has_prose(text: str) -> bool:
    """True if the passage holds at least one sentence of PROSE_SENTENCE_WORDS+ words."""
    sentences = re.split(r"(?<=[.!?])\s+|\n{2,}", text)
    return any(len(s.split()) >= PROSE_SENTENCE_WORDS and re.search(r"[a-z]", s) for s in sentences)


def _windows(rows, limit):
    win, size = [], 0
    for r in rows:
        if win and size + len(r["text"]) > limit:
            yield win
            win, size = [], 0
        win.append(r)
        size += len(r["text"])
    if win:
        yield win


def label_meta(conn, item_id: int, title: str):
    # Passages are labelled by their position in the document (ord), not their row id: ids change
    # on every re-parse, which would change every prompt and miss the LLM cache.
    rows = conn.execute("SELECT id, ord, text FROM passages WHERE item_id=? AND hidden=0 ORDER BY ord",
                        (item_id,)).fetchall()
    ord_to_id = {r["ord"]: r["id"] for r in rows}
    hidden, secs_total = 0, 0.0
    wins = list(_windows(rows, 6000))
    prompts = [META_PROMPT.format(title=title, n=len(win), ids=", ".join(f"P{r['ord']}" for r in win),
                                  passages="\n\n".join(f"[P{r['ord']}]\n{r['text']}" for r in win))
               for win in wins]
    for win, (out, secs, _) in zip(wins, ask_all(conn, "meta", prompts, META_SCHEMA)):
        secs_total += secs
        texts = {r["id"]: r["text"] for r in win}
        with db.tx(conn):
            for lab in out["labels"]:
                lab = lab | {"passage": ord_to_id.get(lab["passage"])}
                if lab["label"] != "content" and lab["passage"] in texts:
                    if lab["label"] in PROSE_GUARDED and has_prose(texts[lab["passage"]]):
                        conn.execute("UPDATE passages SET hidden_reason=? WHERE id=?",
                                     (f"kept: llm said {lab['label']}, but it has prose", lab["passage"]))
                        continue
                    conn.execute("UPDATE passages SET hidden=1, hidden_reason=? WHERE id=?",
                                 (f"llm: {lab['label']}", lab["passage"]))
                    hidden += 1
    log(f"  meta labels: {hidden} more passage(s) hidden by the LLM ({fmt_secs(secs_total)})")


# --- the stage --------------------------------------------------------------------------------

def item_key(source: str) -> str:
    """What `items.path` holds for a source: the URL itself, or the resolved file path."""
    return source if parse.is_url(source) else str(Path(source).resolve())


def ingest(conn, source: str, force: bool = False) -> int | None:
    """Parse a file or an http(s) URL. Web pages are fetched on every run and hashed by their
    extracted text, so a change in the page's layout or ads alone doesn't re-parse them."""
    key = item_key(source)
    if parse.is_url(source):
        name = source
        log(f"  fetching {source}")
        parsed = parse.load_url(source)
        digest = hashlib.sha256(parsed.text.encode()).hexdigest()
        url = source
    else:
        path = Path(key)
        name, parsed = path.name, None
        digest = parse.sha256(path)
        url = parse.read_source(path)
    row = conn.execute("SELECT id, sha256, source FROM items WHERE path=?", (key,)).fetchone()
    if row and row["sha256"] == digest and not force:
        log(f"  unchanged: {name}")
        if row["source"] != url and url:  # e.g. a .source.txt added or edited later: no re-parse
            with db.tx(conn):
                conn.execute("UPDATE items SET source=? WHERE id=?", (url, row["id"]))
            log(f"  source updated: {url}")
        return row["id"]
    if row:
        with db.tx(conn):
            conn.execute("DELETE FROM items WHERE id=?", (row["id"],))
    if parsed is None:
        log(f"  parsing {name}")
        parsed = parse.load(path)
        url = url or parsed.source
    title, sections = chunk.structure(parsed.text, parsed.title)
    if title == "Untitled":
        title = source if parse.is_url(source) else path.stem
    n_boiler = strip_boilerplate(sections)
    chunks = chunk.chunk(sections)
    if not chunks:
        log(f"  no text in {name}")
        return None
    counts = collections.Counter(hashlib.md5(c.text.strip().lower().encode()).hexdigest() for c in chunks)
    with db.tx(conn):
        item_id = conn.execute(
            "INSERT INTO items(path, type, title, sha256, source) VALUES (?,?,?,?,?)",
            (key, parsed.type, title, digest, url)).lastrowid
        n_rule = 0
        for c in chunks:
            reps = counts[hashlib.md5(c.text.strip().lower().encode()).hexdigest()]
            reason = rule_reason(c.text, reps)
            if reason is None and c.role in ("link", "caption"):
                reason = f"chunker role: {c.role}"
            n_rule += reason is not None
            conn.execute("INSERT INTO passages(item_id, section, ord, text, hidden, hidden_reason) "
                         "VALUES (?,?,?,?,?,?)",
                         (item_id, c.section, c.ord, c.text, int(reason is not None),
                          f"rule: {reason}" if reason else None))
    log(f"  {title}: {len(chunks)} passages, {n_rule} hidden by rules, {n_boiler} boilerplate line(s) dropped")
    label_meta(conn, item_id, title)
    return item_id
