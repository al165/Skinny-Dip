"""Stage 2: per document, who is speaking. A summary, the author's main line, and every named
voice (people, organisations, groups). Extraction gets this list so it can write
"Prof. Y argues..." instead of "the passage says...".

Map over blocks of CAST_BLOCK_CHARS of visible text, then reduce when there is more than one."""
from urllib.parse import urlparse

from dipa import config, db
from dipa.ingest.common import HOLDER_TYPES, ask, ask_all, fmt_secs, log

VOICE = {"type": "object",
         "properties": {"name": {"type": "string"},
                        "type": {"type": "string", "enum": HOLDER_TYPES[1:]},
                        "description": {"type": "string"}},
         "required": ["name", "type", "description"]}

CAST_SCHEMA = {
    "type": "object",
    "properties": {
        "author": {"type": "string"},
        "summary": {"type": "string"},
        "author_stance": {"type": "string"},
        "voices": {"type": "array", "items": VOICE},
    },
    "required": ["author", "summary", "author_stance", "voices"],
}

MAP_PROMPT = """You are preparing a document for argument mapping. Read this {part} of "{title}"{source}.

Return:
- author: who wrote it, as the text names them (a person, several people, or an organisation). Only use names that appear in the text. If the text does not name the author, write "{fallback}".
- summary: 3-5 sentences on what this text covers and argues.
- author_stance: one sentence with the author's main line.
- voices: every person, organisation or group OTHER than the author whose views, actions or work the text describes: quoted people, researchers, authors of cited papers, contributors and the projects or artworks the text features, companies, governments, "the AI industry", "critics", "some researchers", "the public". For each: name exactly as the text refers to them; type (person | org | government | industry | field | public | work = a project, paper or artwork whose creator is not named | unspecified); description = who they are and their role in this text, in at most 15 words. Include voices that appear only once.

TEXT:
{text}"""

REDUCE_SCHEMA = {"type": "object", "properties": {k: {"type": "string"} for k in ("author", "summary", "author_stance")},
                 "required": ["author", "summary", "author_stance"]}

REDUCE_PROMPT = """These are notes on consecutive parts of the document "{title}". Merge them into one record for the whole document:
- author: one name, only as the notes give it (if the notes disagree, prefer a name found in the text; never invent one).
- summary: 4-6 sentences for the whole document.
- author_stance: one sentence.

NOTES:
{notes}"""


def merge_voices(lists: list[list[dict]]) -> list[dict]:
    """Union of the voice lists of all blocks, merged in code (an LLM merge of a long deck's
    voices overflows the output cap). Same voice = fuzzy name match; keeps the longer name
    and description."""
    from rapidfuzz import fuzz
    merged: list[dict] = []
    for v in (v for lst in lists for v in lst):
        for m in merged:
            if fuzz.token_set_ratio(v["name"].lower(), m["name"].lower()) >= 90:
                if len(v["name"]) > len(m["name"]):
                    m["name"] = v["name"]
                if len(v["description"]) > len(m["description"]):
                    m["description"] = v["description"]
                break
        else:
            merged.append(dict(v))
    return merged


def _blocks(texts: list[str], limit: int) -> list[str]:
    out, cur = [], ""
    for t in texts:
        if cur and len(cur) + len(t) > limit:
            out.append(cur)
            cur = ""
        cur += t + "\n\n"
    if cur:
        out.append(cur)
    return out


def run(conn, item_id: int):
    item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    texts = [r["text"] for r in conn.execute(
        "SELECT text FROM passages WHERE item_id=? AND hidden=0 ORDER BY ord", (item_id,))]
    blocks = _blocks(texts, config.CAST_BLOCK_CHARS)
    src = f" ({item['source']})" if item["source"] else ""
    host = urlparse(item["source"]).netloc.removeprefix("www.") if item["source"] else ""
    fallback = f"the author of this {'article on ' + host if host else 'document'}"
    notes, total = [], 0.0
    prompts = [MAP_PROMPT.format(part="document" if len(blocks) == 1 else f"part {i + 1} of {len(blocks)}",
                                 title=item["title"], source=src, fallback=fallback, text=b)
               for i, b in enumerate(blocks)]
    for out, secs, _ in ask_all(conn, "cast_map", prompts, CAST_SCHEMA):
        notes.append(out)
        total += secs
    if len(notes) == 1:
        cast = notes[0]
    else:
        import json
        brief = [{k: n[k] for k in ("author", "summary", "author_stance")} for n in notes]
        cast, secs, _ = ask(conn, "cast_reduce",
                            REDUCE_PROMPT.format(title=item["title"], notes=json.dumps(brief, indent=1)),
                            REDUCE_SCHEMA)
        cast["voices"] = merge_voices([n["voices"] for n in notes])
        total += secs
    with db.tx(conn):
        conn.execute("UPDATE items SET summary=?, author=?, author_stance=? WHERE id=?",
                     (cast["summary"], cast["author"], cast["author_stance"], item_id))
        conn.execute("DELETE FROM cast WHERE item_id=?", (item_id,))
        conn.execute("INSERT INTO cast(item_id, name, type, description) VALUES (?,?,?,?)",
                     (item_id, cast["author"], "author", "author of this document"))
        seen = {cast["author"].strip().lower()}
        for v in cast["voices"]:
            key = v["name"].strip().lower()
            if key in seen:
                continue
            seen.add(key)
            conn.execute("INSERT INTO cast(item_id, name, type, description) VALUES (?,?,?,?)",
                         (item_id, v["name"].strip(), v["type"], v["description"]))
    log(f"  cast: author = {cast['author']!r}, {len(seen) - 1} voices, "
        f"{len(blocks)} block(s) ({fmt_secs(total)})")
