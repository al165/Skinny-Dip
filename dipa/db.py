"""SQLite schema for dip-args (prototype: stages 1-4). Later stages add holders, arguments,
argument_mentions, edges, vectors and overrides (see the proposal doc)."""
import contextlib
import hashlib
import json
import sqlite3

from dipa import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY,
    path        TEXT UNIQUE NOT NULL,
    type        TEXT NOT NULL,            -- text | pdf | html | web
    title       TEXT,
    sha256      TEXT NOT NULL,
    source      TEXT,                     -- original URL
    summary     TEXT,                     -- stage 2
    author      TEXT,                     -- stage 2: who "the author" is, as the text names them
    author_stance TEXT,                   -- stage 2: the document's main line, one sentence
    edited      TEXT,                     -- fields changed by a manual edit (dipa/edits.py)
    ingested_at TEXT DEFAULT (datetime('now'))
);

-- Verbatim source text. hidden=1: contents, credits, references... never extracted from or shown.
CREATE TABLE IF NOT EXISTS passages (
    id            INTEGER PRIMARY KEY,
    item_id       INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    section       TEXT,
    ord           INTEGER NOT NULL,
    text          TEXT NOT NULL,
    hidden        INTEGER NOT NULL DEFAULT 0,
    hidden_reason TEXT
);
CREATE INDEX IF NOT EXISTS passages_item ON passages(item_id, ord);

-- Stage 2: the voices a document names (per document; cross-document holder merging is stage 5).
CREATE TABLE IF NOT EXISTS cast (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,            -- author | person | org | industry | field | public | unspecified
    description TEXT,
    edited      TEXT
);

-- Stage 3: what one document says, attributed. Stage 4 fills the quote_* columns.
CREATE TABLE IF NOT EXISTS mentions (
    id          INTEGER PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    passage_id  INTEGER REFERENCES passages(id) ON DELETE SET NULL,
    pass        TEXT NOT NULL,            -- author | voices
    holder      TEXT NOT NULL,
    holder_type TEXT NOT NULL,
    voice       TEXT NOT NULL,            -- argued | conceded | reported | endorsed | rejected
    kind        TEXT NOT NULL,            -- position | fact | example | recommendation | definition | question
    statement   TEXT NOT NULL,
    quote       TEXT NOT NULL,
    cue         TEXT,                     -- voices pass: the words that attribute the view
    author_response TEXT,                 -- voices pass: how the author treats the view
    holder_orig TEXT,                     -- the extractor's holder when the check replaced it (a narrative or idea, not a who)
    attribution_check TEXT,               -- voices pass: LLM check, holder | author (probably the author's own point) | author_echo (same, and the cue is just the claim) | moved (from the author pass by check_author)
    quote_score REAL,
    quote_start INTEGER,                  -- character offsets into passages.text (verified passage)
    quote_end   INTEGER,
    quote_passage INTEGER,                -- the passage the quote was found in (may differ from passage_id)
    quote_ok    INTEGER,
    model       TEXT NOT NULL,
    edited      TEXT                      -- fields changed by a manual edit (dipa/edits.py)
);
CREATE INDEX IF NOT EXISTS mentions_item ON mentions(item_id);

-- Stage 5: who holds views, corpus-wide. Named holders merge across documents by name;
-- placeholder authors and "unspecified" stay per document. Rebuilt on every stage-5 run.
CREATE TABLE IF NOT EXISTS holders (
    id          INTEGER PRIMARY KEY,
    slug        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,            -- author | person | org | government | industry | field | public | work | unspecified
    description TEXT,
    aliases_json TEXT,                    -- every spelling seen in mentions
    keys_json   TEXT,                     -- holder keys this holder covers (see wiki.holder_key)
    page        TEXT,                     -- its wiki page file name
    item_id     INTEGER                   -- set for per-document holders (placeholder author, unspecified)
);

-- Stage 5-6 (later 7): arguments, and later issues and themes. id is stable across rebuilds:
-- for an argument, a hash of its mentions' (document sha, quote), so wiki edits keyed by it survive.
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,            -- argument | issue | theme
    level       INTEGER NOT NULL,         -- 1 argument, 2 issue, 3 theme
    parent_id   TEXT,
    holder_id   INTEGER REFERENCES holders(id),
    kind_of_view TEXT,                    -- argument: position | fact | example | recommendation | definition | question
    title       TEXT,
    summary_tpl TEXT,                     -- summary with "{holder}" where the holder's name goes
    summary     TEXT,                     -- rendered: what users search and read
    abstraction INTEGER,                  -- 1 (a fact) .. 5 (a sweeping thesis)
    origin      TEXT NOT NULL DEFAULT 'extracted',  -- extracted | synthesised | manual
    locked      INTEGER NOT NULL DEFAULT 0,
    model       TEXT
);
CREATE TABLE IF NOT EXISTS argument_mentions (
    argument_id TEXT NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    mention_id  INTEGER NOT NULL REFERENCES mentions(id) ON DELETE CASCADE,
    PRIMARY KEY (argument_id, mention_id)
);

-- Stage 8: typed relations between arguments. method: graded (LLM judge) | manual (wiki) |
-- rejected (deleted in the wiki: never re-added). evidence_json holds the judge's probabilities.
CREATE TABLE IF NOT EXISTS edges (
    src         TEXT NOT NULL,
    dst         TEXT NOT NULL,
    type        TEXT NOT NULL,            -- CONTRADICTS | SUPPORTS | ELABORATES | EXAMPLE_OF | SHARED_WITH
    weight      REAL NOT NULL,
    method      TEXT NOT NULL,
    evidence_json TEXT,
    PRIMARY KEY (src, dst, type)
);
CREATE INDEX IF NOT EXISTS edges_dst ON edges(dst, type);

-- The wiki's state (dipa/wiki.py). The wiki pages are the source of truth for curation; these
-- tables are what wiki_sync read from them, plus what each page showed when it was written.
-- Edited titles/summaries of issues and themes (target = node id).
CREATE TABLE IF NOT EXISTS overrides (
    target      TEXT NOT NULL,
    field       TEXT NOT NULL,            -- title | summary | notes
    value       TEXT NOT NULL,
    edited_at   TEXT DEFAULT (datetime('now')),
    PRIMARY KEY (target, field)
);
-- Which issue an argument belongs to. auto = stage 7 (nearest issue); manual = moved in the wiki.
-- issue_id NULL = unsorted. Survives stage-5 rebuilds (argument ids are stable).
CREATE TABLE IF NOT EXISTS placements (
    argument_id TEXT PRIMARY KEY,
    issue_id    TEXT,
    method      TEXT NOT NULL             -- auto | manual
);
-- Holder pages: each page claims holder keys ("name:emily m bender", "author-of:<file>",
-- "unspecified-in:<file>") and sets their display name, type and description.
CREATE TABLE IF NOT EXISTS holder_rules (
    key         TEXT PRIMARY KEY,
    page        TEXT NOT NULL,            -- the holder page file name
    name        TEXT NOT NULL,
    type        TEXT NOT NULL,
    description TEXT
);
-- What a page listed when stage 9 last wrote it (kind: arg | tension | issue), so wiki_sync can
-- tell a line the person deleted from one that is new since the page was written.
CREATE TABLE IF NOT EXISTS wiki_written (
    page        TEXT NOT NULL,
    kind        TEXT NOT NULL,
    key         TEXT NOT NULL,
    PRIMARY KEY (page, kind, key)
);

-- Stage 10: the image library. The pages in wiki/images/ are the source of truth; these rows
-- are rebuilt from them. Picture vectors are cached by file hash (image_visual), text ones by text.
CREATE TABLE IF NOT EXISTS images (
    id          INTEGER PRIMARY KEY,
    slug        TEXT UNIQUE NOT NULL,
    path        TEXT NOT NULL,            -- the image file
    page        TEXT NOT NULL,            -- its wiki page
    title       TEXT,
    description TEXT,
    subjects_json TEXT,
    source      TEXT, creator TEXT, license TEXT, credit TEXT,
    file_sha    TEXT, page_sha TEXT
);
CREATE TABLE IF NOT EXISTS image_metaphors (
    id          INTEGER PRIMARY KEY,
    image_id    INTEGER NOT NULL REFERENCES images(id) ON DELETE CASCADE,
    keyword     TEXT NOT NULL,
    gloss       TEXT
);
CREATE TABLE IF NOT EXISTS image_visual (
    file_sha    TEXT NOT NULL,
    model       TEXT NOT NULL,
    vec         BLOB NOT NULL,
    PRIMARY KEY (file_sha, model)
);

-- Embeddings by text, so reruns and rebuilt rows reuse them.
CREATE TABLE IF NOT EXISTS embed_cache (
    text_hash   TEXT NOT NULL,
    model       TEXT NOT NULL,
    vec         BLOB NOT NULL,
    PRIMARY KEY (text_hash, model)
);

-- Every LLM answer, keyed by task + input hash + model, so reruns are free.
CREATE TABLE IF NOT EXISTS llm_cache (
    task        TEXT NOT NULL,
    input_hash  TEXT NOT NULL,
    model       TEXT NOT NULL,
    output_json TEXT NOT NULL,
    seconds     REAL,
    PRIMARY KEY (task, input_hash, model)
);
"""


def connect(path=None) -> sqlite3.Connection:
    path = path or config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)  # worker threads use it under a lock (ingest.common)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    _add_columns(conn)
    return conn


# Columns added after the first test databases were made: added in place, so old databases still open.
ADDED_COLUMNS = {"items": ["edited TEXT", "display_title TEXT", "display_source TEXT", "display_author TEXT"], "cast": ["edited TEXT"], "holders": ["keys_json TEXT", "page TEXT"],
                 "mentions": ["edited TEXT", "holder_orig TEXT", "holder_id INTEGER"]}


def _add_columns(conn):
    for table, cols in ADDED_COLUMNS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for col in cols:
            if col.split()[0] not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col}")
    conn.commit()


@contextlib.contextmanager
def tx(conn):
    try:
        yield
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def cache_get(conn, task: str, payload, model: str):
    h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    row = conn.execute("SELECT output_json FROM llm_cache WHERE task=? AND input_hash=? AND model=?",
                       (task, h, model)).fetchone()
    return h, (json.loads(row[0]) if row else None)


def cache_put(conn, task: str, h: str, model: str, output, seconds: float):
    conn.execute("INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?)",
                 (task, h, model, json.dumps(output), seconds))
    conn.commit()


def embed_cached(conn, texts: list[str], query: bool = False):
    """Embeddings (numpy, unit length) for texts, computed once per text and model."""
    import numpy as np
    from dipa import llm
    model = config.EMBED_MODEL + (":query" if query else "")  # queries use the model's query prompt
    keys = [hashlib.sha256(t.encode()).hexdigest() for t in texts]
    have = {}
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        q = "SELECT text_hash, vec FROM embed_cache WHERE model=? AND text_hash IN (%s)" % ",".join("?" * len(part))
        have |= {r[0]: np.frombuffer(r[1], dtype=np.float32) for r in conn.execute(q, [model] + part)}
    todo = [(k, t) for k, t in dict(zip(keys, texts)).items() if k not in have]
    if todo:
        vecs = llm.embed([t for _, t in todo], query=query)
        for (k, _), v in zip(todo, vecs):
            v = np.asarray(v, dtype=np.float32)
            v /= np.linalg.norm(v) or 1.0
            have[k] = v
            conn.execute("INSERT OR REPLACE INTO embed_cache VALUES (?,?,?)", (k, model, v.tobytes()))
        conn.commit()
    return np.stack([have[k] for k in keys]) if keys else np.zeros((0, config.EMBED_DIM), dtype=np.float32)


def cached_vectors(conn, texts: list[str], query: bool = False) -> list:
    """Like embed_cached, but never calls the model: [vector or None] per text. For the offline
    demo, where a text nobody embedded yet (an image page edited on the server) is skipped."""
    import numpy as np
    model = config.EMBED_MODEL + (":query" if query else "")
    out = []
    for t in texts:
        r = conn.execute("SELECT vec FROM embed_cache WHERE text_hash=? AND model=?",
                         (hashlib.sha256(t.encode()).hexdigest(), model)).fetchone()
        out.append(np.frombuffer(r[0], dtype=np.float32) if r else None)
    return out

