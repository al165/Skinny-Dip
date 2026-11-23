"""Manual corrections that survive re-ingesting: the author of a document, holder names and types,
and the holder/voice of single mentions.

The corrections live in <DATA_DIR>/edits.yaml, not only in the database, so `--reset` and
reruns of stages 2-3 do not lose them. They point at things by stable keys (part of a document's
title or file name, a holder's name, a mention's verbatim quote), not by row ids, which change
on a rerun. The ingest runner applies them after stage 2 (so extraction already uses the right
author name) and after stage 3. Rows changed by an edit say so in their `edited` column.

    python -m dipa.edits author "Small AI" "AIxDESIGN"                  # who wrote a document
    python -m dipa.edits rename "Emily Bender" "Emily M. Bender" [--doc D] [--type person]
    python -m dipa.edits mention 905 --holder "Timnit Gebru" --type person --voice reported
    python -m dipa.edits list                                           # edits + whether they still match
    python -m dipa.edits apply                                          # re-apply edits.yaml to the database

You can also edit edits.yaml by hand and run `apply`.
"""
import argparse
import re

import yaml

from dipa import config, db, parse
from dipa.ingest.common import HOLDER_TYPES

EDITS_PATH = config.DATA_DIR / "edits.yaml"
VOICES = ["argued", "conceded", "reported", "endorsed", "rejected"]
HEADER = ("# Manual corrections for dip-args, applied after ingest stages 2 and 3.\n"
          "# Edit by hand or with `python -m dipa.edits`, then run `python -m dipa.edits apply`.\n"
          "# doc = part of the document's title or file name (must match one document).\n")


def load() -> list[dict]:
    if not EDITS_PATH.exists():
        return []
    return yaml.safe_load(EDITS_PATH.read_text()) or []


def save(edits: list[dict]):
    EDITS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EDITS_PATH.write_text(HEADER + yaml.safe_dump(edits, sort_keys=False, allow_unicode=True, width=120))


def _migrate(conn):
    """Adds the `edited` columns to databases made before this module existed."""
    for table in ("items", "cast", "mentions"):
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if "edited" not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN edited TEXT")
    conn.commit()


def find_doc(conn, doc: str) -> list:
    d = doc.lower()
    return [r for r in conn.execute("SELECT * FROM items")
            if d in (r["title"] or "").lower() or d in parse.source_name(r["path"]).lower()]


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _mark(conn, table: str, row_id: int, fields: str):
    old = conn.execute(f"SELECT edited FROM {table} WHERE id=?", (row_id,)).fetchone()[0]
    have = set(filter(None, (old or "").split(",")))
    conn.execute(f"UPDATE {table} SET edited=? WHERE id=?", (",".join(sorted(have | set(fields.split(",")))), row_id))


def _apply_one(conn, e: dict, item_ids: set | None) -> int:
    """Applies one edit; returns how many rows it changed (0 = stale, or not in these items)."""
    op = e["op"]
    docs = find_doc(conn, e["doc"]) if e.get("doc") else None
    if docs is not None and len(docs) != 1:
        return 0
    scope = {docs[0]["id"]} if docs else None
    if item_ids is not None:
        scope = (scope & item_ids) if scope else item_ids
    if scope is not None and not scope:
        return 0
    where, args = ("item_id IN (%s)" % ",".join("?" * len(scope)), list(scope)) if scope else ("1", [])
    n = 0
    if op == "author":
        iid = docs[0]["id"]
        old = docs[0]["author"] or ""
        conn.execute("UPDATE items SET author=? WHERE id=?", (e["name"], iid))
        _mark(conn, "items", iid, "author")
        for r in conn.execute("SELECT id FROM cast WHERE item_id=? AND type='author'", (iid,)).fetchall():
            conn.execute("UPDATE cast SET name=? WHERE id=?", (e["name"], r["id"]))
            _mark(conn, "cast", r["id"], "name")
        # only rows that still carry the old name, so a per-mention fix to the holder is kept
        for r in conn.execute("SELECT id FROM mentions WHERE item_id=? AND lower(holder)=?",
                              (iid, old.lower())).fetchall():
            conn.execute("UPDATE mentions SET holder=? WHERE id=?", (e["name"], r["id"]))
            _mark(conn, "mentions", r["id"], "holder")
            n += 1
        return n + 1
    if op == "rename":
        for table, col, type_col in (("cast", "name", "type"), ("mentions", "holder", "holder_type")):
            rows = conn.execute(f"SELECT id FROM {table} WHERE {where} AND lower({col})=?",
                                args + [e["from"].lower()]).fetchall()
            for r in rows:
                conn.execute(f"UPDATE {table} SET {col}=? WHERE id=?", (e["to"], r["id"]))
                fields = col
                if e.get("type"):
                    conn.execute(f"UPDATE {table} SET {type_col}=? WHERE id=? AND {type_col}!='author'",
                                 (e["type"], r["id"]))
                    fields += "," + type_col
                _mark(conn, table, r["id"], fields)
            n += len(rows)
        # already applied earlier: the new name is there instead (counts as matching, not stale)
        n += conn.execute(f"SELECT count(*) FROM mentions WHERE {where} AND lower(holder)=? AND edited LIKE '%holder%'",
                          args + [e["to"].lower()]).fetchone()[0] if n == 0 else 0
        return n
    if op == "mention":
        q = _norm(e["quote"])
        rows = [r for r in conn.execute(f"SELECT id, quote FROM mentions WHERE {where}", args)
                if _norm(r["quote"]) == q]
        sets = {k: e[k] for k in ("holder", "holder_type", "voice", "statement") if e.get(k)}
        for r in rows:
            conn.execute("UPDATE mentions SET %s WHERE id=?" % ",".join(f"{k}=?" for k in sets),
                         list(sets.values()) + [r["id"]])
            _mark(conn, "mentions", r["id"], ",".join(sets))
        return len(rows)
    raise ValueError(f"unknown edit op {op!r}")


def apply(conn, item_id: int | None = None, ops=("author", "rename", "mention"), quiet=False) -> list[tuple]:
    """Re-applies every edit of the given kinds. Returns [(edit, rows changed)]."""
    _migrate(conn)
    out = []
    with db.tx(conn):
        for e in load():
            if e["op"] in ops:
                out.append((e, _apply_one(conn, e, {item_id} if item_id else None)))
    if not quiet and out:
        from dipa.ingest.common import log
        hit = sum(1 for _, k in out if k)
        log(f"  manual edits: {hit}/{len(out)} applied" + (f" (others are for other documents or no longer match; "
                                                            f"`python -m dipa.edits list`)" if hit < len(out) else ""))
    return out


def _describe(e: dict) -> str:
    if e["op"] == "author":
        return f"author of '{e['doc']}' = {e['name']}"
    if e["op"] == "rename":
        return f"rename '{e['from']}' -> '{e['to']}'" + (f" ({e['type']})" if e.get("type") else "") + \
            (f" in '{e['doc']}'" if e.get("doc") else " in all documents")
    sets = ", ".join(f"{k}={e[k]}" for k in ("holder", "holder_type", "voice", "statement") if e.get(k))
    return f"mention “{e['quote'][:60]}…” in '{e['doc']}': {sets}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("author", help="set who wrote a document")
    a.add_argument("doc")
    a.add_argument("name")
    r = sub.add_parser("rename", help="rename a holder (in one document with --doc, else everywhere)")
    r.add_argument("old")
    r.add_argument("new")
    r.add_argument("--doc")
    r.add_argument("--type", choices=HOLDER_TYPES[1:])
    m = sub.add_parser("mention", help="correct one mention (by its id in the current database)")
    m.add_argument("id", type=int)
    m.add_argument("--holder")
    m.add_argument("--type", choices=HOLDER_TYPES)
    m.add_argument("--voice", choices=VOICES)
    m.add_argument("--statement")
    sub.add_parser("list", help="show edits and how many rows each matches now")
    sub.add_parser("apply", help="re-apply edits.yaml to the database")
    x = sub.add_parser("remove", help="delete an edit by its number in `list` (rows keep the edited value "
                                      "until the stage that made them is rerun)")
    x.add_argument("n", type=int)
    args = ap.parse_args()

    conn = db.connect()
    _migrate(conn)
    edits = load()
    new = None
    if args.cmd == "author":
        docs = find_doc(conn, args.doc)
        if len(docs) != 1:
            raise SystemExit(f"'{args.doc}' matches {len(docs)} documents: "
                             + "; ".join(d["title"] for d in docs) if docs else f"no document matches '{args.doc}'")
        new = {"op": "author", "doc": args.doc, "name": args.name}
        edits = [e for e in edits if not (e["op"] == "author" and find_doc(conn, e["doc"]) == docs)]
    elif args.cmd == "rename":
        if args.doc and len(find_doc(conn, args.doc)) != 1:
            raise SystemExit(f"--doc '{args.doc}' must match exactly one document")
        new = {"op": "rename", "from": args.old, "to": args.new}
        new |= {k: v for k, v in (("type", args.type), ("doc", args.doc)) if v}
    elif args.cmd == "mention":
        row = conn.execute("SELECT m.*, i.title FROM mentions m JOIN items i ON i.id=m.item_id WHERE m.id=?",
                           (args.id,)).fetchone()
        if not row:
            raise SystemExit(f"no mention #{args.id}")
        sets = {k: v for k, v in (("holder", args.holder), ("holder_type", args.type), ("voice", args.voice),
                                  ("statement", args.statement)) if v}
        if not sets:
            raise SystemExit("nothing to change: give --holder, --type, --voice or --statement")
        new = {"op": "mention", "doc": row["title"], "quote": row["quote"], **sets}
    elif args.cmd == "remove":
        if not 1 <= args.n <= len(edits):
            raise SystemExit(f"no edit {args.n}")
        print(f"removed: {_describe(edits.pop(args.n - 1))}")
        save(edits)
        return
    if new:
        edits.append(new)
        save(edits)
        print(f"saved to {EDITS_PATH}: {_describe(new)}")
    if args.cmd == "list":
        if not edits:
            print(f"no edits ({EDITS_PATH})")
        # dry run: apply inside a transaction we roll back, to count matches
        for i, e in enumerate(edits, 1):
            k = _apply_one(conn, e, None)
            print(f"{i:3}. {_describe(e)}  [{k} row(s)]" + ("  ← matches nothing (stale?)" if not k else ""))
        conn.rollback()
        return
    for e, k in apply(conn, quiet=True):
        if new is None or e is new or e == new:
            print(f"  {_describe(e)}: {k} row(s)" + ("  ← matches nothing" if not k else ""))


if __name__ == "__main__":
    main()
