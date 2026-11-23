"""The editable wiki: stage 9 writes it, `sync` reads people's edits back.

    python -m dipa.wiki sync     # read edits into the database (also runs before every corpus stage)
    python -m dipa.wiki write    # (re)write the pages from the database (stage 9)
    python -m dipa.wiki apply    # sync + rebuild holders/arguments + tree placement + write (no new LLM text)

Pages (<DATA_DIR>/wiki/):
  index.md                 themes -> issues, and the holders
  themes/<slug>.md         a theme: title, summary, its issues
  issues/<slug>.md         an issue: the question, state of the debate, its arguments, its tensions
  holders/<slug>.md        a holder: name, type, the keys it covers, description, its arguments
  sources/<file>.md        a source document: display title, URL and author, its summary
  unsorted.md              arguments that fit no issue yet
  lint.md                  things to check: unverified quotes, near-duplicate issues, key conflicts

What an edit does (on sync):
  title / summary (issue, theme)    stored in `overrides`, shown as "Summary · edited"
  ## Notes (any page)               kept as written
  an [[arg:…]] line added to an issue page          the argument moves to that issue (manual)
  an [[arg:…]] line removed and on no other page    the argument becomes unsorted (manual)
  a line with two [[arg:…]] added under ## Tensions a manual CONTRADICTS edge
  a tension line deleted                            the edge is rejected: stage 8 never re-adds it
  an [[issue:…]] line added to a theme page         the issue moves to that theme
  holder page: name / type / description            the holder is renamed / retyped everywhere
  holder page: a key added to `keys:`               that holder merges into this one
  source page: title / url / author                 shown everywhere instead of the parsed ones
                                                    (extraction keeps the original title: no re-run)
  locked: true                                      stage 9 never rewrites the page

The LLM only drafts: issue and theme texts once (stage 7 when the tree is built), argument
summaries once (stage 6 for new arguments). After that new arguments are appended to the
nearest issue or to unsorted.md, and nothing a person wrote is replaced.
"""
import argparse
import collections
import json
import re
from pathlib import Path

import yaml

from dipa import config, db
from dipa import parse as parse_mod
from dipa.ingest.common import log

ARG = re.compile(r"\[\[arg:([a-z0-9-]+)(?:\|[^\]]*)?\]\]")
ISSUE = re.compile(r"\[\[issue:([a-z0-9-]+)(?:\|[^\]]*)?\]\]")
FRONT = re.compile(r"^---\n(.*?)\n---\n?", re.S)
VOICE = {"argued": "argued in", "conceded": "conceded in", "reported": "reported in",
         "endorsed": "endorsed in", "rejected": "rejected in"}


def wiki_dir() -> Path:
    return Path(config.WIKI_DIR)


def slugify(text: str, n: int = 60) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:n] or "page"


# --- parsing -------------------------------------------------------------------------------

def parse(text: str) -> dict:
    """Front matter, the summary (leading "> " lines) and the ## sections of a page."""
    m = FRONT.match(text)
    front = (yaml.safe_load(m.group(1)) or {}) if m else {}
    body = text[m.end():] if m else text
    summary, sections, cur = [], collections.OrderedDict(), None
    for line in body.splitlines():
        h = re.match(r"^##\s+(.*)", line)
        if h:
            cur = h.group(1).split("·")[0].split("(")[0].strip().lower()
            sections[cur] = []
        elif cur is None:
            if line.startswith(">"):
                summary.append(line[1:].strip())
        else:
            sections[cur].append(line)
    return {"front": front, "summary": " ".join(s for s in summary if s).strip(), "sections": sections}


def _section(page: dict, name: str) -> list[str]:
    return page["sections"].get(name, [])


def _notes(page: dict) -> str:
    return "\n".join(_section(page, "notes")).strip()


def _dump(front: dict, body: str) -> str:
    return "---\n" + yaml.safe_dump(front, sort_keys=False, allow_unicode=True, width=1000).strip() + "\n---\n" + body


def is_locked(path: Path) -> bool:
    if not path.is_file():
        return False
    m = FRONT.match(path.read_text(encoding="utf-8"))
    try:
        return bool(m and (yaml.safe_load(m.group(1)) or {}).get("locked"))
    except yaml.YAMLError:
        return True  # a page we cannot parse is never overwritten


def _pages(kind: str) -> list[Path]:
    d = wiki_dir() / kind
    return sorted(d.glob("*.md")) if d.is_dir() else []


# --- overrides -----------------------------------------------------------------------------

def _set_override(conn, target: str, field: str, value: str | None):
    if value is None:
        conn.execute("DELETE FROM overrides WHERE target=? AND field=?", (target, field))
    else:
        conn.execute("INSERT INTO overrides(target, field, value) VALUES (?,?,?) ON CONFLICT(target, field) "
                     "DO UPDATE SET value=excluded.value, edited_at=datetime('now')", (target, field, value))


def override(conn, target: str, field: str) -> str | None:
    r = conn.execute("SELECT value FROM overrides WHERE target=? AND field=?", (target, field)).fetchone()
    return r[0] if r else None


def apply_overrides(conn):
    """Edited titles/summaries onto issue and theme nodes; issue -> theme moves; argument
    placements onto parent_id. Called after stage 7 and after every sync."""
    for r in conn.execute("SELECT target, field, value FROM overrides WHERE field IN ('title','summary')").fetchall():
        if conn.execute("SELECT 1 FROM nodes WHERE id=?", (r[0],)).fetchone():
            conn.execute(f"UPDATE nodes SET {r[1]}=?, origin='manual' WHERE id=?", (r[2], r[0]))
    for r in conn.execute("SELECT target, value FROM overrides WHERE field='theme'").fetchall():
        if conn.execute("SELECT 1 FROM nodes WHERE id=? AND kind='theme'", (r[1],)).fetchone():
            conn.execute("UPDATE nodes SET parent_id=? WHERE id=? AND kind='issue'", (r[1], r[0]))
    conn.execute("UPDATE items SET display_title=NULL, display_source=NULL, display_author=NULL")
    for item in conn.execute("SELECT id, path FROM items").fetchall():
        target = f"src:{parse_mod.source_name(item['path'])}"
        for field, col in (("title", "display_title"), ("url", "display_source"), ("author", "display_author")):
            v = override(conn, target, field)
            if v:
                conn.execute(f"UPDATE items SET {col}=? WHERE id=?", (v, item["id"]))
    conn.execute("UPDATE nodes SET parent_id=NULL WHERE kind='argument'")
    conn.execute("UPDATE nodes SET parent_id=(SELECT p.issue_id FROM placements p WHERE p.argument_id=nodes.id "
                 "AND p.issue_id IN (SELECT id FROM nodes WHERE kind='issue')) WHERE kind='argument'")
    conn.commit()


# --- sync: pages -> database ---------------------------------------------------------------

def _written(conn, page: str, kind: str) -> set[str]:
    return {r[0] for r in conn.execute("SELECT key FROM wiki_written WHERE page=? AND kind=?", (page, kind))}


def sync(conn) -> dict:
    """Read every page's edits into the database. Returns what changed."""
    changed = collections.Counter()
    if not wiki_dir().is_dir():
        return changed
    issues = {f"issues/{p.name}": (p, parse(p.read_text(encoding="utf-8"))) for p in _pages("issues")}
    themes = {f"themes/{p.name}": (p, parse(p.read_text(encoding="utf-8"))) for p in _pages("themes")}
    holders = {f"holders/{p.name}": (p, parse(p.read_text(encoding="utf-8"))) for p in _pages("holders")}
    unsorted_path = wiki_dir() / "unsorted.md"
    unsorted = parse(unsorted_path.read_text(encoding="utf-8")) if unsorted_path.is_file() else None
    node = {r["id"]: r for r in conn.execute("SELECT id, kind, title, summary FROM nodes")}

    with db.tx(conn):
        # where every argument is listed now
        listed = collections.defaultdict(set)   # arg -> pages
        for key, (_, pg) in issues.items():
            for line in _section(pg, "arguments"):
                for a in ARG.findall(line):
                    listed[a].add(key)
        if unsorted:
            for line in sum(unsorted["sections"].values(), []):
                for a in ARG.findall(line):
                    listed[a].add("unsorted.md")

        for key, (path, pg) in issues.items():
            iid = pg["front"].get("id")
            if iid not in node:
                continue
            changed["title"] += _sync_text(conn, iid, "title", str(pg["front"].get("title") or ""), node[iid]["title"])
            changed["summary"] += _sync_text(conn, iid, "summary", pg["summary"], node[iid]["summary"])
            if (_notes(pg) or None) != override(conn, iid, "notes"):
                _set_override(conn, iid, "notes", _notes(pg) or None)
                changed["notes"] += 1
            theme = pg["front"].get("theme")
            if theme and theme in node and node[theme]["kind"] == "theme" and theme != override(conn, iid, "theme"):
                if conn.execute("SELECT parent_id FROM nodes WHERE id=?", (iid,)).fetchone()[0] != theme:
                    _set_override(conn, iid, "theme", theme)
                    changed["theme"] += 1
            now = {a for line in _section(pg, "arguments") for a in ARG.findall(line) if a in node}
            before = _written(conn, key, "arg")
            for a in now - before:                       # added here: moved by hand
                conn.execute("INSERT OR REPLACE INTO placements VALUES (?,?, 'manual')", (a, iid))
                changed["moved"] += 1
            for a in before - now:                       # removed, and on no other page: unsorted
                if not listed.get(a):
                    conn.execute("INSERT OR REPLACE INTO placements VALUES (?, NULL, 'manual')", (a,))
                    changed["unsorted"] += 1
            changed["tensions"] += _sync_tensions(conn, key, pg, node)

        for key, (path, pg) in themes.items():
            tid = pg["front"].get("id")
            if tid not in node:
                continue
            changed["title"] += _sync_text(conn, tid, "title", str(pg["front"].get("title") or ""), node[tid]["title"])
            changed["summary"] += _sync_text(conn, tid, "summary", pg["summary"], node[tid]["summary"])
            if (_notes(pg) or None) != override(conn, tid, "notes"):
                _set_override(conn, tid, "notes", _notes(pg) or None)
                changed["notes"] += 1
            now = {i for line in _section(pg, "issues") for i in ISSUE.findall(line) if i in node}
            for i in now - _written(conn, key, "issue"):
                _set_override(conn, i, "theme", tid)
                changed["theme"] += 1

        changed["holders"] += _sync_holders(conn, holders)
        changed["sources"] += _sync_sources(conn)
    apply_overrides(conn)
    return {k: v for k, v in changed.items() if v}


def _sync_sources(conn) -> int:
    """Source pages: an edited title / url / author is stored as an override of the parsed one."""
    n = 0
    items = {parse_mod.source_name(r["path"]): r for r in conn.execute("SELECT * FROM items")}
    for p in _pages("sources"):
        pg = parse(p.read_text(encoding="utf-8"))
        f = pg["front"]
        item = items.get(str(f.get("file") or ""))
        if item is None:
            continue
        target = f"src:{f['file']}"
        if (_notes(pg) or None) != override(conn, target, "notes"):
            _set_override(conn, target, "notes", _notes(pg) or None)
            n += 1
        for field, parsed in (("title", item["title"]), ("url", item["source"]), ("author", item["author"])):
            v = str(f.get(field) or "").strip()
            want = v if v and v != (parsed or "") else None   # same as parsed: no override
            if want != override(conn, target, field):
                _set_override(conn, target, field, want)
                n += 1
    return n


def _sync_text(conn, target, field, page_value: str, db_value: str | None) -> int:
    page_value = (page_value or "").strip()
    if not page_value or page_value == (db_value or "").strip():
        return 0
    _set_override(conn, target, field, page_value)
    return 1


def _sync_tensions(conn, key, pg, node) -> int:
    now = set()
    for line in _section(pg, "tensions"):
        ids = [a for a in ARG.findall(line) if a in node]
        if len(ids) >= 2:
            now.add("|".join(sorted(ids[:2])))
    before = _written(conn, key, "tension")
    n = 0
    for pair in before - now:          # deleted by hand: rejected, never re-added by stage 8
        a, b = pair.split("|")
        for s, d in ((a, b), (b, a)):
            conn.execute("DELETE FROM edges WHERE src=? AND dst=? AND type='CONTRADICTS'", (s, d))
            conn.execute("INSERT OR REPLACE INTO edges(src, dst, type, weight, method) VALUES (?,?,?,?,?)",
                         (s, d, "CONTRADICTS", 0.0, "rejected"))
        n += 1
    for pair in now - before:          # typed by hand: a manual counter
        a, b = pair.split("|")
        for s, d in ((a, b), (b, a)):
            conn.execute("DELETE FROM edges WHERE src=? AND dst=? AND type='CONTRADICTS'", (s, d))
            conn.execute("INSERT INTO edges(src, dst, type, weight, method) VALUES (?,?,?,?,?)",
                         (s, d, "CONTRADICTS", 1.0, "manual"))
        n += 1
    return n


def _sync_holders(conn, holders: dict) -> int:
    """Holder pages -> holder_rules. A key on two pages goes to the page it was added to."""
    old = {(r["key"], r["page"], r["name"], r["type"], r["description"] or "")
           for r in conn.execute("SELECT * FROM holder_rules")}
    claims = collections.defaultdict(list)    # key -> [(added_here, page, front, summary)]
    for key, (path, pg) in holders.items():
        f = pg["front"]
        keys = f.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        written = _written(conn, key, "key")
        for k in keys:
            k = str(k).strip()
            if k:
                claims[k].append((k not in written, path.name, f, pg["summary"]))
    conn.execute("DELETE FROM holder_rules")
    for k, cs in claims.items():
        added, page, f, summary = sorted(cs, key=lambda c: (not c[0], c[1]))[0]
        conn.execute("INSERT INTO holder_rules VALUES (?,?,?,?,?)",
                     (k, page, str(f.get("name") or k), str(f.get("type") or "unspecified"), summary or None))
    new = {(r["key"], r["page"], r["name"], r["type"], r["description"] or "")
           for r in conn.execute("SELECT * FROM holder_rules")}
    return len(old ^ new)


# --- write: database -> pages --------------------------------------------------------------

def _arg_line(conn, a, holders, with_issue=False) -> str:
    h = holders.get(a["holder_id"])
    rec = conn.execute("SELECT DISTINCT m.voice, coalesce(i.display_title, i.title) FROM argument_mentions am "
                       "JOIN mentions m ON m.id=am.mention_id "
                       "JOIN items i ON i.id=m.item_id WHERE am.argument_id=?", (a["id"],)).fetchall()
    where = "; ".join(f"{VOICE.get(r[0], r[0])} “{r[1][:50]}”" for r in rec)
    hl = f"[[holder:{_holder_page(h).removesuffix('.md')}|{h['name']}]]" if h else "?"
    issue = ""
    if with_issue and a["parent_id"]:
        q = conn.execute("SELECT title FROM nodes WHERE id=?", (a["parent_id"],)).fetchone()
        issue = f" · issue: [[issue:{a['parent_id']}|{q[0] if q else a['parent_id']}]]" if q else ""
    return f"- [[arg:{a['id']}]] **{a['title']}** — {a['summary']} _({hl} · {where}{issue})_"


def _holder_page(h) -> str:
    return h["page"] or f"{h['slug']}.md"


def _issue_page(conn, iid: str, title: str) -> str:
    known = conn.execute("SELECT page FROM wiki_written WHERE kind='node' AND key=?", (iid,)).fetchone()
    return known[0].split("/", 1)[1] if known else f"{slugify(title, 50)}-{iid[1:7]}.md"


def _record(conn, page: str, kind: str, keys):
    conn.execute("DELETE FROM wiki_written WHERE page=? AND kind=?", (page, kind))
    conn.executemany("INSERT OR IGNORE INTO wiki_written VALUES (?,?,?)", [(page, kind, k) for k in keys])


def write(conn) -> dict:
    """Write every page from the database. Locked pages are left as they are."""
    root = wiki_dir()
    for d in ("issues", "themes", "holders"):
        (root / d).mkdir(parents=True, exist_ok=True)
    holders = {r["id"]: r for r in conn.execute("SELECT * FROM holders")}
    args = conn.execute("SELECT * FROM nodes WHERE kind='argument' AND summary IS NOT NULL ORDER BY id").fetchall()
    by_issue = collections.defaultdict(list)
    for a in args:
        by_issue[a["parent_id"]].append(a)
    issues = conn.execute("SELECT * FROM nodes WHERE kind='issue' ORDER BY title").fetchall()
    themes = conn.execute("SELECT * FROM nodes WHERE kind='theme' ORDER BY title").fetchall()
    counts = collections.Counter()
    keep = set()

    issue_file = {i["id"]: _issue_page(conn, i["id"], i["title"]) for i in issues}
    theme_file = {t["id"]: f"{slugify(t['title'], 50)}-{t['id'][1:7]}.md" for t in themes}
    for t in themes:
        known = conn.execute("SELECT page FROM wiki_written WHERE kind='node' AND key=?", (t["id"],)).fetchone()
        if known:
            theme_file[t["id"]] = known[0].split("/", 1)[1]

    with db.tx(conn):
        for i in issues:
            page = f"issues/{issue_file[i['id']]}"
            keep.add(page)
            _record(conn, page, "node", [i["id"]])
            path = root / page
            if is_locked(path):
                counts["locked"] += 1
                continue
            members = by_issue.get(i["id"], [])
            # one argument per holder first, so the sides of the debate show up at the top
            seen, first, rest = set(), [], []
            for a in sorted(members, key=lambda a: -(a["abstraction"] or 3)):
                (rest if a["holder_id"] in seen else first).append(a)
                seen.add(a["holder_id"])
            ids = [a["id"] for a in members]
            tensions = []
            if ids:
                q = ",".join("?" * len(ids))
                for r in conn.execute(f"SELECT src, dst, weight, method FROM edges WHERE type='CONTRADICTS' AND src<dst "
                                      f"AND method!='rejected' AND src IN ({q}) AND dst IN ({q}) ORDER BY weight DESC",
                                      ids + ids):
                    tensions.append(r)
            title_of = {a["id"]: a["title"] for a in members}
            body = [f"> {i['summary'] or ''}", "",
                    f"## Arguments ({len(members)}) · move a line to another issue page to move the argument", ""]
            body += [_arg_line(conn, a, holders) for a in first + rest] or ["_(none)_"]
            body += ["", "## Tensions · delete a line to reject it; add `- [[arg:A]] CONTRADICTS [[arg:B]]` to add one", ""]
            body += [f"- [[arg:{t['src']}]] CONTRADICTS [[arg:{t['dst']}]] · {title_of.get(t['src'])} ↔ "
                     f"{title_of.get(t['dst'])} ({t['method']}, {t['weight']:.2f})" for t in tensions] or ["_(none)_"]
            body += ["", "## Notes", "", override(conn, i["id"], "notes") or ""]
            front = {"kind": "issue", "id": i["id"], "title": i["title"], "theme": i["parent_id"],
                     "edited": i["origin"] == "manual", "locked": False}
            path.write_text(_dump(front, "\n".join(body).rstrip() + "\n"), encoding="utf-8")
            _record(conn, page, "arg", ids)
            _record(conn, page, "tension", ["|".join(sorted((t["src"], t["dst"]))) for t in tensions])
            counts["issues"] += 1

        for t in themes:
            page = f"themes/{theme_file[t['id']]}"
            keep.add(page)
            _record(conn, page, "node", [t["id"]])
            path = root / page
            if is_locked(path):
                counts["locked"] += 1
                continue
            its = [i for i in issues if i["parent_id"] == t["id"]]
            body = [f"> {t['summary'] or ''}", "", "## Issues · add an [[issue:…]] line here to move an issue to this theme", ""]
            body += [f"- [[issue:{i['id']}|{i['title']}]] ({len(by_issue.get(i['id'], []))} arguments)" for i in its]
            body += ["", "## Notes", "", override(conn, t["id"], "notes") or ""]
            front = {"kind": "theme", "id": t["id"], "title": t["title"], "edited": t["origin"] == "manual", "locked": False}
            path.write_text(_dump(front, "\n".join(body).rstrip() + "\n"), encoding="utf-8")
            _record(conn, page, "issue", [i["id"] for i in its])
            counts["themes"] += 1

        by_holder = collections.defaultdict(list)
        for a in args:
            by_holder[a["holder_id"]].append(a)
        for hid, h in holders.items():
            page = f"holders/{_holder_page(h)}"
            keep.add(page)
            path = root / page
            if is_locked(path):
                counts["locked"] += 1
                continue
            mine = by_holder.get(hid, [])
            ids = [a["id"] for a in mine]
            against = []
            if ids:
                q = ",".join("?" * len(ids))
                against = conn.execute(
                    f"SELECT h.name, h.slug, h.page, count(*) n FROM edges e JOIN nodes n ON n.id=e.dst JOIN holders h "
                    f"ON h.id=n.holder_id WHERE e.type='CONTRADICTS' AND e.method!='rejected' AND e.src IN ({q}) "
                    f"AND h.id!=? GROUP BY h.id ORDER BY n DESC", (*ids, hid)).fetchall()
            keys = json.loads(h["keys_json"] or "[]")
            body = [f"> {h['description'] or ''}", "",
                    f"## Arguments ({len(mine)}) · generated: edit them on their issue pages", ""]
            body += [_arg_line(conn, a, holders, with_issue=True) for a in
                     sorted(mine, key=lambda a: -(a["abstraction"] or 3))] or ["_(none)_"]
            body += ["", "## Disagrees with · generated", ""]
            body += [f"- [[holder:{_holder_page(r).removesuffix('.md')}|{r['name']}]] ({r['n']} counter link(s))"
                     for r in against] or ["_(none)_"]
            body += ["", "## Notes", "", override(conn, f"holder:{page}", "notes") or ""]
            front = {"kind": "holder", "name": h["name"], "type": h["type"], "keys": keys, "locked": False}
            path.write_text(_dump(front, "\n".join(body).rstrip() + "\n"), encoding="utf-8")
            _record(conn, page, "key", keys)
            counts["holders"] += 1

        # sources
        (root / "sources").mkdir(exist_ok=True)
        for item in conn.execute("SELECT * FROM items ORDER BY title").fetchall():
            file = parse_mod.source_name(item["path"])
            page = f"sources/{slugify(Path(file).stem, 70)}.md"
            keep.add(page)
            path = root / page
            if is_locked(path):
                counts["locked"] += 1
                continue
            n_ps = conn.execute("SELECT count(*), sum(hidden) FROM passages WHERE item_id=?", (item["id"],)).fetchone()
            n_args = conn.execute("SELECT count(DISTINCT am.argument_id) FROM argument_mentions am JOIN mentions m "
                                  "ON m.id=am.mention_id WHERE m.item_id=?", (item["id"],)).fetchone()[0]
            hs = conn.execute("SELECT h.name, h.slug, h.page, count(DISTINCT n.id) c FROM mentions m JOIN holders h "
                              "ON h.id=m.holder_id JOIN argument_mentions am ON am.mention_id=m.id JOIN nodes n "
                              "ON n.id=am.argument_id WHERE m.item_id=? GROUP BY h.id ORDER BY c DESC LIMIT 15",
                              (item["id"],)).fetchall()
            body = [f"> {item['summary'] or ''}", "",
                    f"**Main line:** {item['author_stance'] or ''}", "",
                    f"## About · generated", "",
                    f"- file: `{file}`",
                    f"- parsed title: {item['title']}",
                    f"- {n_ps[0]} passages ({n_ps[1] or 0} hidden), {n_args} arguments", "",
                    "## Voices · generated", ""]
            body += [f"- [[holder:{_holder_page(h).removesuffix('.md')}|{h['name']}]] ({h['c']})" for h in hs] or ["_(none)_"]
            body += ["", "## Notes", "", override(conn, f"src:{file}", "notes") or ""]
            front = {"kind": "source", "file": file,
                     "title": item["display_title"] or item["title"],
                     "url": item["display_source"] or item["source"] or "",
                     "author": item["display_author"] or item["author"] or "",
                     "locked": False}
            path.write_text(_dump(front, "\n".join(body).rstrip() + "\n"), encoding="utf-8")
            counts["sources"] += 1

        # unsorted
        uns = by_issue.get(None, [])
        body = ["> Arguments that fit no issue yet. Move a line to an issue page to file it.", "",
                f"## Unsorted ({len(uns)})", ""]
        body += [_arg_line(conn, a, holders) for a in uns] or ["_(none)_"]
        (root / "unsorted.md").write_text(_dump({"kind": "unsorted"}, "\n".join(body) + "\n"), encoding="utf-8")
        _record(conn, "unsorted.md", "arg", [a["id"] for a in uns])

        _write_index(conn, root, themes, issues, issue_file, theme_file, holders, by_holder, by_issue)
        _write_lint(conn, root, issues)

        # pages of issues/themes/holders that no longer exist (unlocked only)
        for d in ("issues", "themes", "holders", "sources"):
            for p in _pages(d):
                rel = f"{d}/{p.name}"
                if rel not in keep:
                    if is_locked(p):
                        counts["stale_locked"] += 1
                    else:
                        p.unlink()
                        conn.execute("DELETE FROM wiki_written WHERE page=?", (rel,))
                        counts["removed"] += 1
    return dict(counts)


def _write_index(conn, root, themes, issues, issue_file, theme_file, holders, by_holder, by_issue):
    out = ["---\nkind: index\n---", "# Debate map", "",
           f"{len(themes)} themes, {len(issues)} issues, {sum(len(v) for v in by_issue.values())} arguments, "
           f"{len(holders)} holders · [[unsorted]] ({len(by_issue.get(None, []))}) · [[lint]]", ""]
    for t in themes:
        out += [f"## [[theme:{t['id']}|{t['title']}]]", ""]
        out += [f"- [[issue:{i['id']}|{i['title']}]] ({len(by_issue.get(i['id'], []))})"
                for i in issues if i["parent_id"] == t["id"]]
        out.append("")
    loose = [i for i in issues if not i["parent_id"]]
    if loose:
        out += ["## Issues without a theme", ""] + [f"- [[issue:{i['id']}|{i['title']}]]" for i in loose] + [""]
    out += ["## Holders", ""]
    for h in sorted(holders.values(), key=lambda h: (-len(by_holder.get(h["id"], [])), h["name"].lower())):
        out.append(f"- [[holder:{_holder_page(h).removesuffix('.md')}|{h['name']}]] · {h['type']} · "
                   f"{len(by_holder.get(h['id'], []))}")
    (root / "index.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def _write_lint(conn, root, issues):
    out = ["---\nkind: lint\n---", "# Things to check", ""]
    bad = conn.execute("SELECT DISTINCT am.argument_id, n.title FROM argument_mentions am JOIN mentions m ON m.id=am.mention_id "
                       "JOIN nodes n ON n.id=am.argument_id WHERE m.quote_ok=0").fetchall()
    out += [f"## Quotes not found in the source ({len(bad)})", ""] + \
           [f"- [[arg:{r[0]}]] {r[1]}" for r in bad] + [""]
    if len(issues) > 1:
        vecs = db.embed_cached(conn, [f"{i['title']} {i['summary'] or ''}" for i in issues])
        sims = vecs @ vecs.T
        dups = [(float(sims[a, b]), issues[a], issues[b]) for a in range(len(issues))
                for b in range(a + 1, len(issues)) if sims[a, b] >= 0.9]
        out += [f"## Issues that may be the same question ({len(dups)})", "",
                "Merge by moving the arguments of one to the other (and its title into Notes).", ""]
        out += [f"- {s:.2f} · [[issue:{x['id']}|{x['title']}]] / [[issue:{y['id']}|{y['title']}]]"
                for s, x, y in sorted(dups, key=lambda d: -d[0])] + [""]
    hidden = conn.execute("SELECT count(*) FROM passages WHERE hidden=1").fetchone()[0]
    out += [f"## Hidden passages: {hidden}", "", "Listed per document in reports/<document>.md.", ""]
    (root / "lint.md").write_text("\n".join(out) + "\n", encoding="utf-8")


def run(conn):
    """Stage 9."""
    counts = write(conn)
    log(f"  wiki: {counts} → {wiki_dir()}")


def apply(conn, embed_ok: bool = True) -> dict:
    """After an edit: sync, then rebuild what depends on it without writing new LLM text:
    holders + arguments (stage 5, cached LLM pairs only), tree placement (stage 7, no LLM),
    and the pages (stage 9)."""
    changed = sync(conn)
    if changed.get("holders"):
        from dipa.ingest import s5_merge
        s5_merge.run(conn)
        apply_overrides(conn)
    counts = write(conn)
    return {"changed": changed, "written": counts}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["sync", "write", "apply"])
    a = ap.parse_args()
    conn = db.connect()
    if a.cmd == "sync":
        print(sync(conn))
    elif a.cmd == "write":
        print(write(conn))
    else:
        print(apply(conn))


if __name__ == "__main__":
    main()
