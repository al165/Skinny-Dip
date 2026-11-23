"""Grow the image library from a shared sheet, and move its vectors to an offline server.

    python -m dipa.imagelib import <sheet CSV link or file> [--dry-run]   # on the dev machine
    python -m dipa.imagelib export <file.json>    # the library's vectors (for ship.sh --images)
    python -m dipa.imagelib load <file.json>      # on the server: add those vectors, reload the library

import: every row whose Ready column is ticked becomes an image:
  - the picture is downloaded from "Image file URL" into DIPA_IMAGE_DIR (Google Drive share links
    are converted; GIFs are refused; pictures over MAX_SIDE px are scaled down),
  - <file>.source.txt next to it holds the credit (source, creator, license, credit, description),
  - its wiki page (wiki/images/<slug>.md) is written from the sheet. Empty fields are drafted:
    no description → the vision model; no subjects → from the description; no metaphors → the LLM.
    No credit line → "<Title> by <Creator>, <License>, via <source site>",
  - then stage 10 loads and embeds the library.
  A row already imported is skipped unless it changed in the sheet; then its page is rewritten
  (unless the page is locked: lock an image page in the wiki to keep your edits). The record of
  imported rows is DIPA_IMAGE_DIR/.sheet-imports.json.

Columns (matched by name, case and punctuation ignored): Image file URL, Source page URL, Title,
Creator, License, Credit line, Description, Subjects, Metaphors (one per line, "keyword: gloss"),
Added by, Ready. Without a Ready column every row is taken.

export / load: an offline server can't embed new pictures or texts. `export` writes the vectors of
every image in the library (description, metaphor lines, picture) to a JSON file; on the server
`load` adds them to its caches and reloads the library from the shipped pages, with no model.
"""
import argparse
import base64
import csv
import hashlib
import io
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from dipa import config, db, images, llm
from dipa.ingest.common import log

MAX_SIDE = 2000
IMPORTS_FILE = ".sheet-imports.json"
EXT = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
USER_AGENT = "dip-args-image-import/0.1"   # generic client name

COLUMNS = {   # canonical field: accepted column names (normalised)
    "file_url": ["imagefileurl", "fileurl", "imageurl", "image", "file", "downloadurl", "directlink"],
    "source": ["sourcepageurl", "sourceurl", "source", "pageurl", "url", "link"],
    "title": ["title", "name"],
    "creator": ["creator", "artist", "author", "photographer", "by"],
    "license": ["license", "licence", "rights"],
    "credit": ["creditline", "credit", "attribution"],
    "description": ["description", "desc", "whatitshows"],
    "subjects": ["subjects", "subject", "tags", "keywords"],
    "metaphors": ["metaphors", "metaphor"],
    "added_by": ["addedby", "contributor", "submittedby", "who"],
    "ready": ["ready", "approved", "include", "publish", "ok"],
}

SUBJECTS_PROMPT = """An image is described as:

{description}

Give `subjects`: 3-8 short phrases naming the things, people, places and materials it shows."""
SUBJECTS_SCHEMA = {"type": "object", "properties": {"subjects": {"type": "array", "items": {"type": "string"}}},
                   "required": ["subjects"]}


# --- reading the sheet ---------------------------------------------------------------------

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def sheet_csv_url(link: str) -> str:
    """A Google Sheets link as a CSV download (an /edit link of a sheet shared by link, or a
    published /pub link); anything else is used as it is."""
    m = re.search(r"docs\.google\.com/spreadsheets/d/([A-Za-z0-9_-]+)", link)
    if m and "/pub" not in link and "export" not in link:
        gid = re.search(r"[#&?]gid=(\d+)", link)
        return f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv" + (f"&gid={gid.group(1)}" if gid else "")
    if "/pub" in link and "output=csv" not in link:
        return link + ("&" if "?" in link else "?") + "output=csv"
    return link


def read_rows(src: str) -> list[dict]:
    if re.match(r"^https?://", src):
        r = httpx.get(sheet_csv_url(src), follow_redirects=True, timeout=60, headers={"User-Agent": USER_AGENT})
        r.raise_for_status()
        if "text/html" in r.headers.get("content-type", ""):
            raise SystemExit("the sheet link returned a web page, not CSV: share the sheet as 'anyone with the link "
                             "can view', or use File → Share → Publish to web → CSV")
        text = r.content.decode("utf-8-sig")
    else:
        text = Path(src).read_text(encoding="utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    names = {_norm(h): h for h in reader.fieldnames or []}
    field_of = {}
    for field, options in COLUMNS.items():
        hit = next((names[o] for o in options if o in names), None)
        if hit:
            field_of[field] = hit
    missing = [f for f in ("file_url", "title") if f not in field_of]
    if missing:
        raise SystemExit(f"the sheet needs columns for {missing}; it has {list(reader.fieldnames or [])}")
    rows = []
    for n, raw in enumerate(reader, start=2):   # row 1 is the header
        row = {f: (raw.get(h) or "").strip() for f, h in field_of.items()}
        row["_row"] = n
        row["_has_ready"] = "ready" in field_of
        rows.append(row)
    return rows


def is_ready(row: dict) -> bool:
    if not row["_has_ready"]:
        return True
    return _norm(row.get("ready", "")) in {"true", "yes", "y", "x", "1", "ready", "ok", "done", "approved"}


def parse_metaphors(cell: str) -> list[dict]:
    out = []
    for line in re.split(r"[\n;]+", cell or ""):
        line = line.strip().lstrip("-•* ").strip()
        if not line:
            continue
        kw, _, gloss = line.partition(":")
        if kw.strip():
            out.append({"keyword": kw.strip().lower(), "gloss": gloss.strip()})
    return out


# --- downloading ---------------------------------------------------------------------------

def file_link(url: str) -> str:
    """Google Drive share links → direct download; other links unchanged."""
    m = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?(?:.*&)?id=)([A-Za-z0-9_-]+)", url)
    if m:
        return f"https://drive.google.com/uc?export=download&id={m.group(1)}"
    return url


def download(url: str) -> tuple[bytes, str]:
    r = httpx.get(file_link(url), follow_redirects=True, timeout=120, headers={"User-Agent": USER_AGENT})
    r.raise_for_status()
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if ctype == "image/gif":
        raise ValueError("GIFs are not supported (use a PNG, JPEG or WebP)")
    if ctype not in EXT:
        hint = " (a web page: use the direct link to the file itself)" if "html" in ctype else ""
        raise ValueError(f"not an image: {ctype or 'unknown type'}{hint}")
    return shrink(r.content, ctype), EXT[ctype]


def shrink(data: bytes, ctype: str) -> bytes:
    """Scale pictures larger than MAX_SIDE down (keeps format; the embedding model sees far less)."""
    try:
        from PIL import Image
    except ImportError:
        return data
    im = Image.open(io.BytesIO(data))
    if max(im.size) <= MAX_SIDE:
        return data
    im.thumbnail((MAX_SIDE, MAX_SIDE))
    buf = io.BytesIO()
    fmt = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}[ctype]
    if fmt == "JPEG" and im.mode not in ("RGB", "L"):
        im = im.convert("RGB")
    im.save(buf, format=fmt, quality=90)
    return buf.getvalue()


# --- import --------------------------------------------------------------------------------

def _row_hash(row: dict) -> str:
    keys = ("file_url", "source", "title", "creator", "license", "credit", "description", "subjects", "metaphors")
    return hashlib.sha256(json.dumps([row.get(k, "") for k in keys]).encode()).hexdigest()[:16]


def _credit(row: dict) -> str:
    if row.get("credit"):
        return row["credit"]
    site = urlparse(row.get("source", "")).netloc.removeprefix("www.")
    parts = [row["title"]] + ([f"by {row['creator']}"] if row.get("creator") else [])
    tail = ", ".join(x for x in (row.get("license"), f"via {site}" if site else "") if x)
    return " ".join(parts) + (f", {tail}" if tail else "")


def _free_name(stem: str, ext: str, taken: set) -> str:
    name, n = f"{stem}{ext}", 2
    while name in taken or (config.IMAGE_DIR / name).exists():
        name, n = f"{stem}-{n}{ext}", n + 1
    return name


def import_sheet(src: str, dry_run: bool = False):
    config.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    config.IMAGE_WIKI_DIR.mkdir(parents=True, exist_ok=True)
    record_path = config.IMAGE_DIR / IMPORTS_FILE
    record = json.loads(record_path.read_text()) if record_path.exists() else {}
    rows = read_rows(src)
    if rows and not rows[0]["_has_ready"]:
        log("  no Ready column: taking every row")
    taken = {v["file"] for v in record.values()}
    done = {"new": 0, "updated": 0, "unchanged": 0, "not ready": 0, "failed": 0}
    for row in rows:
        label = f"row {row['_row']} “{row.get('title') or '?'}”"
        if not is_ready(row):
            done["not ready"] += 1
            continue
        if not row.get("file_url") or not row.get("title"):
            log(f"  {label}: skipped, needs an image file URL and a title")
            done["failed"] += 1
            continue
        key, h = row["file_url"], _row_hash(row)
        prev = record.get(key)
        if prev and prev["hash"] == h and (config.IMAGE_DIR / prev["file"]).exists():
            done["unchanged"] += 1
            continue
        if dry_run:
            log(f"  {label}: would {'update' if prev else 'import'}")
            continue
        try:
            if prev and (config.IMAGE_DIR / prev["file"]).exists() and prev.get("file_url") == row["file_url"]:
                name = prev["file"]          # same picture: keep the file, refresh the texts
            else:
                data, ext = download(row["file_url"])
                name = prev["file"] if prev and prev["file"].endswith(ext) else _free_name(images.slugify(row["title"]), ext, taken)
                (config.IMAGE_DIR / name).write_bytes(data)
            taken.add(name)
            img = config.IMAGE_DIR / name
            src_lines = [f"source: {row.get('source') or row['file_url']}", f"creator: {row.get('creator', '')}",
                         f"license: {row.get('license', '')}", f"credit: {_credit(row)}"]
            if row.get("description"):
                src_lines.append(f"description: {' '.join(row['description'].split())}")
            img.with_name(img.name + ".source.txt").write_text("\n".join(src_lines) + "\n", encoding="utf-8")
            page = _page_path(img)
            if page.exists() and images.parse_page(page.read_text(encoding="utf-8")).get("locked") == "true":
                log(f"  {label}: page is locked in the wiki; picture and credit updated, page kept")
            else:
                page.write_text(images.render_page(_page_data(row, img)), encoding="utf-8")
            record[key] = {"file": name, "hash": h, "row": row["_row"], "added_by": row.get("added_by", ""),
                           "file_url": row["file_url"]}
            record_path.write_text(json.dumps(record, indent=1, ensure_ascii=False))
            done["updated" if prev else "new"] += 1
            log(f"  {label}: {'updated' if prev else 'imported'} as {name}")
        except (httpx.HTTPError, ValueError, OSError, llm.LLMError) as e:
            done["failed"] += 1
            log(f"  {label}: FAILED: {e}")
    log(f"  sheet: {done}")
    if not dry_run and (done["new"] or done["updated"]):
        images.run(db.connect())


def _page_path(img: Path) -> Path:
    """The page for a picture: the existing one (matched by its file: line), else a new slug."""
    return images._page_for([img])[img]


def _page_data(row: dict, img: Path) -> dict:
    """The page fields from the sheet; empty description / subjects / metaphors are drafted."""
    rel = str(img.relative_to(config.IMAGE_DIR))
    d = {"title": row["title"], "file": rel, "source": row.get("source") or row["file_url"],
         "creator": row.get("creator", ""), "license": row.get("license", ""), "credit": _credit(row),
         "description": " ".join(row.get("description", "").split()),
         "subjects": [s.strip() for s in row.get("subjects", "").split(",") if s.strip()],
         "metaphors": parse_metaphors(row.get("metaphors", ""))}
    if not d["description"]:   # the vision model describes the picture (subjects and metaphors come with it)
        drafted = images.draft(img, rel)
        d["description"] = drafted["description"]
        d["subjects"] = d["subjects"] or drafted["subjects"]
        d["metaphors"] = d["metaphors"] or drafted["metaphors"]
    if not d["subjects"]:
        out = llm.generate(SUBJECTS_PROMPT.format(description=d["description"]), schema=SUBJECTS_SCHEMA)
        d["subjects"] = [s.strip() for s in out["subjects"] if s.strip()]
    if not d["metaphors"]:
        out = llm.generate(images.METAPHOR_PROMPT.format(title=d["title"], description=d["description"],
                                                         subjects=", ".join(d["subjects"])),
                           schema=images.METAPHOR_SCHEMA, temperature=0.5)
        d["metaphors"] = [{"keyword": m["keyword"].strip().lower(), "gloss": m["gloss"].strip()}
                          for m in out["metaphors"] if m["keyword"].strip()]
    return d


# --- moving vectors to an offline server ---------------------------------------------------

def _b64(v) -> str:
    return base64.b64encode(v.astype("float32").tobytes()).decode()


def export_vectors(out: str):
    """Every library image's description, metaphor and picture vectors, keyed as the caches key them."""
    conn = db.connect()
    images.run(conn)   # the library as the pages say, every vector computed
    rows = conn.execute("SELECT * FROM images").fetchall()
    mets = conn.execute("SELECT * FROM image_metaphors").fetchall()
    texts = [images.desc_text(r) for r in rows] + [images.metaphor_text(m) for m in mets]
    vecs = db.embed_cached(conn, texts)
    vis = images.visual_vectors(conn, rows)
    by_id = {r["id"]: r for r in rows}
    payload = {"embed_model": config.EMBED_MODEL,
               "texts": [{"hash": hashlib.sha256(t.encode()).hexdigest(), "vec": _b64(v)} for t, v in zip(texts, vecs)],
               "pictures": [{"file_sha": by_id[i]["file_sha"], "vec": _b64(v)} for i, v in vis.items()]}
    Path(out).write_text(json.dumps(payload))
    log(f"  exported {len(payload['texts'])} text and {len(payload['pictures'])} picture vectors for "
        f"{len(rows)} images → {out}")


def load_vectors(path: str):
    """Add exported vectors to this database's caches, then reload the library (no model needed)."""
    payload = json.loads(Path(path).read_text())
    if payload["embed_model"] != config.EMBED_MODEL:
        raise SystemExit(f"vectors are from {payload['embed_model']}, this server uses {config.EMBED_MODEL}")
    conn = db.connect()
    with db.tx(conn):
        conn.executemany("INSERT OR REPLACE INTO embed_cache VALUES (?,?,?)",
                         [(t["hash"], config.EMBED_MODEL, base64.b64decode(t["vec"])) for t in payload["texts"]])
        conn.executemany("INSERT OR REPLACE INTO image_visual VALUES (?,?,?)",
                         [(p["file_sha"], config.EMBED_MODEL, base64.b64decode(p["vec"])) for p in payload["pictures"]])
    log(f"  loaded {len(payload['texts'])} text and {len(payload['pictures'])} picture vectors")
    pairs = images.sync_pages()      # pages are shipped; nothing to draft on the server
    changed = images.load_pages(conn, pairs)
    rows, mets, D, M, vis = images._library(conn)
    log(f"  library: {len(rows)} images searchable ({changed} new or changed page(s)), {len(vis)} with picture vectors")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("import", help="import ticked rows of the sheet")
    i.add_argument("src", help="the sheet's link (shared by link, or published as CSV) or a CSV file")
    i.add_argument("--dry-run", action="store_true")
    e = sub.add_parser("export", help="write the library's vectors to a JSON file")
    e.add_argument("out")
    lo = sub.add_parser("load", help="add exported vectors and reload the library (offline server)")
    lo.add_argument("path")
    a = ap.parse_args()
    if a.cmd == "import":
        import_sheet(a.src, a.dry_run)
    elif a.cmd == "export":
        export_vectors(a.out)
    else:
        load_vectors(a.path)


if __name__ == "__main__":
    main()
