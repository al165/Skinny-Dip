"""Review report for stages 1-4: one markdown page per document + a summary + a labelling sheet.

    python -m dipa.report                 # -> <DATA_DIR>/reports/
    python -m dipa.report --sample 60     # size of the gold labelling sample (default 60)
"""
import argparse
import collections
import csv
import random
import re

from dipa import config, db, edits


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def doc_page(conn, item) -> str:
    iid = item["id"]
    ps = conn.execute("SELECT * FROM passages WHERE item_id=? ORDER BY ord", (iid,)).fetchall()
    ms = conn.execute("SELECT * FROM mentions WHERE item_id=? ORDER BY coalesce(quote_passage, passage_id), id",
                      (iid,)).fetchall()
    cast = conn.execute("SELECT * FROM cast WHERE item_id=?", (iid,)).fetchall()
    hidden = [p for p in ps if p["hidden"]]
    out = [f"# {item['title']}", "",
           f"Source: {item['source'] or item['path']}", "",
           f"**Author:** {item['author']}{' ✎' if item['edited'] else ''}  ", f"**Main line:** {item['author_stance']}", "",
           f"> {item['summary']}", "",
           f"{len(ps)} passages, {len(hidden)} hidden · {len(ms)} mentions "
           f"({sum(m['pass'] == 'author' for m in ms)} author, {sum(m['pass'] == 'voices' for m in ms)} other voices) · "
           f"quotes verified {sum(bool(m['quote_ok']) for m in ms)}/{len(ms)}", "",
           "## Cast", ""]
    out += [f"- **{c['name']}** ({c['type']}): {c['description']}" for c in cast]
    out += ["", "## Hidden passages", ""]
    out += [f"- P{p['id']} · {p['hidden_reason']} · {p['text'][:140].replace(chr(10), ' ')}…" for p in hidden] or ["(none)"]
    out += ["", "## Mentions, in passage order", "",
            "`A` = author pass, `V` = other-voices pass. ✓/✗ = quote found in the source (score).", ""]
    by_p = collections.defaultdict(list)
    for m in ms:
        by_p[m["quote_passage"] or m["passage_id"]].append(m)
    for p in ps:
        if p["id"] not in by_p:
            continue
        out.append(f"### P{p['id']} · {p['section'] or ''}")
        out.append("")
        out.append("> " + p["text"].replace("\n", "\n> "))
        out.append("")
        for m in by_p[p["id"]]:
            mark = "✓" if m["quote_ok"] else "✗"
            tag = "A" if m["pass"] == "author" else "V"
            line = (f"- `{tag}` **{m['holder']}** ({m['holder_type']}, {m['voice']}, {m['kind']}): "
                    f"{m['statement']} — “{m['quote']}” {mark} {m['quote_score']:.0f} · #{m['id']}")
            if m["edited"]:
                line += f" · ✎ edited ({m['edited']})"
            if m["attribution_check"] == "author":
                line += "  \n  ⚠ check: probably the author's own point"
            if m["author_response"]:
                line += f"  \n  author's response: {m['author_response']}"
            out.append(line)
        out.append("")
    return "\n".join(out)


def summary(conn) -> str:
    items = conn.execute("SELECT * FROM items ORDER BY id").fetchall()
    ms = conn.execute("SELECT * FROM mentions").fetchall()
    out = ["# Stage 1-4 test run", "", "| Document | Passages | Hidden | Author | Other voices | Quotes OK | Distinct holders |",
           "| --- | --- | --- | --- | --- | --- | --- |"]
    for it in items:
        im = [m for m in ms if m["item_id"] == it["id"]]
        np_ = conn.execute("SELECT count(*), sum(hidden) FROM passages WHERE item_id=?", (it["id"],)).fetchone()
        out.append(f"| {it['title'][:60]} | {np_[0]} | {np_[1] or 0} | {sum(m['pass'] == 'author' for m in im)} | "
                   f"{sum(m['pass'] == 'voices' for m in im)} | {sum(bool(m['quote_ok']) for m in im)}/{len(im)} | "
                   f"{len({m['holder'].lower() for m in im})} |")
    for col in ("voice", "kind", "holder_type"):
        c = collections.Counter(m[col] for m in ms)
        out += ["", f"**{col}:** " + ", ".join(f"{k} {v}" for k, v in c.most_common())]
    secs = conn.execute("SELECT task, count(*), sum(seconds) FROM llm_cache GROUP BY task").fetchall()
    out += ["", "## LLM time (uncached calls)", "", "| Task | Calls | Seconds |", "| --- | --- | --- |"]
    out += [f"| {r[0]} | {r[1]} | {r[2]:.0f} |" for r in secs]
    return "\n".join(out)


def sample_sheet(conn, path, n: int):
    """Stratified sample for hand-labelling: half author pass, half other voices, spread over documents."""
    rng = random.Random(7)
    rows = conn.execute(
        "SELECT m.*, i.title, p.text AS passage_text FROM mentions m JOIN items i ON i.id=m.item_id "
        "LEFT JOIN passages p ON p.id=coalesce(m.quote_passage, m.passage_id)").fetchall()
    groups = collections.defaultdict(list)
    for r in rows:
        groups[(r["item_id"], r["pass"])].append(r)
    per = max(1, n // max(1, len(groups)))
    picked = []
    for g in groups.values():
        picked += rng.sample(g, min(per, len(g)))
    rng.shuffle(picked)
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "document", "pass", "holder", "holder_type", "voice", "kind", "statement", "quote",
                    "passage", "check", "holder_ok (y/n)", "voice_ok (y/n)", "statement_ok (y/n)", "worth_keeping (y/n)", "notes"])
        for r in picked:
            w.writerow([r["id"], r["title"], r["pass"], r["holder"], r["holder_type"], r["voice"], r["kind"],
                        r["statement"], r["quote"], r["passage_text"], r["attribution_check"] or "", "", "", "", "", ""])
    return len(picked)


def arguments_page(conn) -> str:
    """Stages 5-6: every holder and their arguments, with reception and sources."""
    from dipa.ingest.s6_summarise import reception
    hs = conn.execute("SELECT h.*, count(n.id) n FROM holders h JOIN nodes n ON n.holder_id=h.id "
                      "GROUP BY h.id ORDER BY n DESC, h.name").fetchall()
    n_args = conn.execute("SELECT count(*) FROM nodes WHERE kind='argument'").fetchone()[0]
    out = ["# Arguments by holder", "",
           f"{len(hs)} holders, {n_args} arguments. Summaries are written by {config.LLM_MODEL}; "
           "the reception line comes from the extracted voices, not the LLM.", ""]
    for h in hs:
        out += [f"## {h['name']} ({h['type']}) · {h['n']} argument(s)", ""]
        if h["description"]:
            out += [f"_{h['description']}_", ""]
        for a in conn.execute("SELECT * FROM nodes WHERE holder_id=? AND kind='argument' ORDER BY abstraction DESC, id",
                              (h["id"],)):
            rec = ", ".join(f"{r['voice']} in “{r['document'][:40]}”" for r in reception(conn, a["id"]))
            out.append(f"- **{a['title']}** · abs {a['abstraction']} · {a['kind_of_view']} · `{a['id']}`  \n"
                       f"  {a['summary']}  \n  _{rec}_")
            for m in conn.execute("SELECT m.quote, m.quote_ok FROM argument_mentions am JOIN mentions m ON m.id=am.mention_id "
                                  "WHERE am.argument_id=?", (a["id"],)):
                out.append(f"  > “{m['quote']}” {'✓' if m['quote_ok'] else '✗'}")
        out.append("")
    return "\n".join(out)


def summary_sheet(conn, path, n: int = 30) -> int:
    """Stratified sample of argument summaries for hand-checking attribution and faithfulness."""
    rng = random.Random(11)
    rows = conn.execute("SELECT n.*, h.name AS holder, h.type AS htype FROM nodes n JOIN holders h ON h.id=n.holder_id "
                        "WHERE n.kind='argument' AND n.summary IS NOT NULL").fetchall()
    groups = collections.defaultdict(list)
    for r in rows:
        groups[r["htype"]].append(r)
    picked, per = [], max(2, n // max(1, len(groups)))
    for g in groups.values():
        picked += rng.sample(g, min(per, len(g)))
    rng.shuffle(picked)
    picked = picked[:n]
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["argument_id", "holder", "holder_type", "title", "summary", "voices", "statements", "quotes",
                    "attribution_ok (y/n)", "faithful (y/n)", "worth_keeping (y/n)", "notes"])
        for a in picked:
            ms = conn.execute("SELECT m.* FROM argument_mentions am JOIN mentions m ON m.id=am.mention_id "
                              "WHERE am.argument_id=?", (a["id"],)).fetchall()
            w.writerow([a["id"], a["holder"], a["htype"], a["title"], a["summary"],
                        "; ".join(sorted({m["voice"] for m in ms})), " | ".join(m["statement"] for m in ms),
                        " | ".join(m["quote"] for m in ms), "", "", "", ""])
    return len(picked)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=60)
    a = ap.parse_args()
    conn = db.connect()
    edits._migrate(conn)
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    for it in conn.execute("SELECT * FROM items"):
        (config.REPORT_DIR / f"{slug(it['title'])}.md").write_text(doc_page(conn, it))
    (config.REPORT_DIR / "summary.md").write_text(summary(conn))
    k = sample_sheet(conn, config.REPORT_DIR / "gold_sample.csv", a.sample)
    msg = f"summary.md, one page per document, gold_sample.csv with {k} rows"
    if conn.execute("SELECT count(*) FROM nodes WHERE kind='argument' AND summary IS NOT NULL").fetchone()[0]:
        (config.REPORT_DIR / "arguments.md").write_text(arguments_page(conn))
        k2 = summary_sheet(conn, config.REPORT_DIR / "summary_sample.csv")
        msg += f", arguments.md, summary_sample.csv with {k2} rows"
    print(f"wrote {config.REPORT_DIR}/ ({msg})")


if __name__ == "__main__":
    main()
