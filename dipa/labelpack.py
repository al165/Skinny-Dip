"""A labelling pack for a team: one workbook per person, three tasks, overlap for agreement.

    DIP_DATA_DIR=data-full python -m dipa.labelpack make [--people 4] [--out DIR]
    DIP_DATA_DIR=data-full python -m dipa.labelpack score [DIR]

make writes DIR (default <data>/labelling/): labelling-A.xlsx … one per person, GUIDE.md, key.json.
Each workbook has a Guide tab and three task tabs with dropdowns (upload to Google Drive and open
with Google Sheets: tabs and dropdowns are kept; download it back as .xlsx into DIR):
  Summaries   is the argument summary attributed right, faithful to the source, worth keeping?
  Counters    how does argument B relate to argument A? (blind: the model's verdict is not shown)
  Mentions    is the extracted holder / voice / statement right?
OVERLAP of each task's items are given to two people (rotating pairs), the rest to one, so the
score can say how much people agree: the model can't be held to more than that.

score reads the filled workbooks back (labelling-*.xlsx in DIR) and writes DIR/report.md:
summary attribution / faithfulness rates, the counter judge's AUC and best threshold against the
team's labels, mention accuracy per pass, and agreement (percent and Cohen's kappa) on overlap rows.
"""
import argparse
import collections
import itertools
import json
import random
from pathlib import Path

from dipa import config, db

TASKS = ("summaries", "counters", "mentions")
SIZES = {"summaries": 120, "counters": 160, "mentions": 100}
OVERLAP = 0.2
YN = ["y", "n", "unsure"]
RELATIONS = ["opposed", "qualifies", "different question", "compatible", "supports", "same view", "specific case"]
JUDGE_DIGIT = {r: i + 1 for i, r in enumerate(RELATIONS)}   # the judge's 1-7 scale, same order
VOICES = ["argued", "conceded", "reported", "endorsed", "rejected"]

GUIDE = """# Labelling guide

Thank you for helping. You'll judge what the system extracted from the corpus. There are three
tabs; do them in any order. Each row takes about a minute. If a row is unclear, pick "unsure" and
say why in Notes rather than guessing. Please don't discuss rows with each other until you're
done: some rows are labelled by two people, to see how much we agree.

## Summaries

Each row is one *argument*: a view, who holds it (the holder), and the summary the model wrote.
Below it are the verbatim quotes it came from, how each document treats the view, and a piece of
the source text.

- **attribution_ok**: does the summary credit the view to the right holder? "n" if it gives the
  document's author someone else's view (or the other way round), or names the wrong person or
  group.
- **faithful**: does the summary say only what the source says? "n" if it adds a reason, example
  or detail the source doesn't give, or if it turns the holder's view into the author's rebuttal
  (e.g. "Investors argue the hype is a problem" when the source says investors fund the hype and
  the author criticises it).
- **worth_keeping**: would someone exploring the debate want this as an argument? "n" for trivial
  details, titles, captions or credits.

## Counters

Each row is a pair of arguments, A and B, each with its holder and a quote. Pick how **B relates
to A**:

| Choice | Meaning |
| --- | --- |
| opposed | B argues against A's position on the same question |
| qualifies | B partly pushes back on A: a limit, exception or complication |
| different question | B is about something else, even if the topic is related |
| compatible | both could be held together, and neither backs the other up |
| supports | B gives evidence or reasons for A |
| same view | B states essentially the same claim as A (maybe by someone else) |
| specific case | B is an example, case or more detailed instance of A |

Judge the views, whoever holds them. A view an author describes only to argue against it still
counts as that holder's view. Two articles that both reject the same view *agree* with each
other; they don't counter each other.

## Mentions

Each row is one thing the model extracted from a passage: who holds a view (holder), how the
document's author treats it (voice), and the view itself (statement). The passage is shown.

- **holder_ok**: is the holder right (name and type)? If not, write the right holder in
  correct_holder.
- **voice_ok**: argued = the author's own claim; conceded = the author grants a point against their
  own case; reported = someone else's view, the author is neutral; endorsed = someone else's view,
  the author agrees; rejected = someone else's view, the author argues against it.
- **statement_ok**: is the statement one self-contained sentence that says the point (not just a
  title or a minor detail)?
- **worth_keeping**: as above.
"""


# --- sampling ------------------------------------------------------------------------------

def _passage_text(conn, m, limit=1200) -> str:
    pid = m["quote_passage"] or m["passage_id"]
    r = conn.execute("SELECT text FROM passages WHERE id=?", (pid,)).fetchone() if pid else None
    if not r:
        return ""
    t = r[0]
    if len(t) <= limit:
        return t
    start = max(0, (m["quote_start"] or 0) - limit // 3)
    return ("…" if start else "") + t[start:start + limit] + "…"


def sample_summaries(conn, rng, n) -> list[dict]:
    from dipa.ingest.s6_summarise import reception
    rows = conn.execute("SELECT n.*, h.name holder, h.type htype FROM nodes n JOIN holders h ON h.id=n.holder_id "
                        "WHERE n.kind='argument' AND n.summary IS NOT NULL").fetchall()
    by_type = collections.defaultdict(list)
    for r in rows:
        by_type[r["htype"]].append(r)
    picked = _stratified(rng, by_type, n)
    out = []
    for a in picked:
        ms = conn.execute("SELECT m.*, coalesce(i.display_title, i.title) doc FROM argument_mentions am JOIN mentions m "
                          "ON m.id=am.mention_id JOIN items i ON i.id=m.item_id WHERE am.argument_id=?", (a["id"],)).fetchall()
        rec = "; ".join(f"{r['voice']} in “{r['document']}”" for r in reception(conn, a["id"]))
        out.append({"id": a["id"], "holder": f"{a['holder']} ({a['htype']})", "title": a["title"], "summary": a["summary"],
                    "how documents treat it": rec,
                    "quotes": "\n".join(f"“{m['quote']}” ({m['doc']})" for m in ms),
                    "source text": _passage_text(conn, ms[0]) if ms else ""})
    return out


def sample_counters(conn, rng, n) -> list[dict]:
    from dipa.ingest import s8_relations as s8
    args, jobs, prompts, _ = s8.candidates(conn)
    quote = {}
    for r in conn.execute("SELECT am.argument_id, m.quote FROM argument_mentions am JOIN mentions m ON m.id=am.mention_id "
                          "ORDER BY m.quote_ok DESC, length(m.quote) DESC"):
        quote.setdefault(r[0], r[1])
    bins = collections.defaultdict(list)   # by the judge's counter score, so labels cover the whole range
    for (a, b), p in zip(jobs, prompts):
        probs = s8.cached_judgement(conn, p)
        if probs is None or args[a]["hid"] == args[b]["hid"]:
            continue
        sc = s8.counter_score(probs)
        band = next(k for k, lo in enumerate((0.8, 0.5, 0.25, 0.1, -1)) if sc >= lo)
        bins[band].append((a, b, sc, probs))
    picked = _stratified(rng, bins, n)
    return [{"id": f"{a}|{b}", "A: holder": args[a]["holder"], "A: view": args[a]["summary"], "A: quote": quote.get(a, ""),
             "B: holder": args[b]["holder"], "B: view": args[b]["summary"], "B: quote": quote.get(b, ""),
             "_score": round(sc, 4), "_probs": {str(k): round(v, 4) for k, v in probs.items()}}
            for a, b, sc, probs in picked]


def sample_mentions(conn, rng, n) -> list[dict]:
    rows = conn.execute("SELECT m.*, coalesce(i.display_title, i.title) doc FROM mentions m JOIN items i ON i.id=m.item_id "
                        "WHERE m.holder_id IS NOT NULL").fetchall()
    strata = collections.defaultdict(list)
    for r in rows:
        strata[(r["item_id"], r["pass"])].append(r)
    picked = _stratified(rng, strata, n)
    return [{"id": str(m["id"]), "document": m["doc"], "holder": m["holder"], "holder_type": m["holder_type"],
             "voice": m["voice"], "kind": m["kind"], "statement": m["statement"], "quote": m["quote"],
             "passage": _passage_text(conn, m)} for m in picked]


def _stratified(rng, groups: dict, n: int) -> list:
    """About n items spread evenly over the groups (smaller groups give what they have)."""
    pools = {k: rng.sample(v, len(v)) for k, v in groups.items() if v}
    out = []
    while len(out) < n and any(pools.values()):
        for k in sorted(pools, key=str):
            if pools[k] and len(out) < n:
                out.append(pools[k].pop())
    rng.shuffle(out)
    return out


# --- assignment and workbooks ----------------------------------------------------------------

def assign(items: list[dict], people: list[str], rng) -> dict[str, list[dict]]:
    """Each item to one person, OVERLAP of them to two (rotating over every pair of people)."""
    n_over = round(len(items) * OVERLAP)
    pairs = list(itertools.combinations(people, 2))
    out = {p: [] for p in people}
    for k, item in enumerate(items):
        if k < n_over:
            for p in pairs[k % len(pairs)]:
                out[p].append(item)
        else:
            out[min(people, key=lambda p: len(out[p]))].append(item)
    for p in people:
        rng.shuffle(out[p])
    return out


SHEETS = {
    "summaries": {"show": ["holder", "title", "summary", "how documents treat it", "quotes", "source text"],
                  "label": [("attribution_ok", YN), ("faithful", YN), ("worth_keeping", YN), ("notes", None)]},
    "counters": {"show": ["A: holder", "A: view", "A: quote", "B: holder", "B: view", "B: quote"],
                 "label": [("relation of B to A", RELATIONS), ("notes", None)]},
    "mentions": {"show": ["document", "holder", "holder_type", "voice", "kind", "statement", "quote", "passage"],
                 "label": [("holder_ok", YN), ("correct_holder", None), ("voice_ok", YN), ("statement_ok", YN),
                           ("worth_keeping", YN), ("notes", None)]},
}
WIDTH = {"summary": 60, "source text": 70, "quotes": 50, "passage": 70, "statement": 50, "quote": 45,
         "A: view": 55, "B: view": 55, "A: quote": 40, "B: quote": 40, "notes": 30, "title": 30,
         "how documents treat it": 30, "relation of B to A": 20, "correct_holder": 22}


def write_workbook(path: Path, person: str, rows: dict[str, list[dict]]):
    from openpyxl import Workbook
    from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation
    wb = Workbook()
    g = wb.active
    g.title = "Guide"
    g["A1"] = f"Labeller {person}: your name →"
    g["B1"] = ""
    g["A1"].font = Font(bold=True)
    g["B1"].fill = PatternFill("solid", fgColor="FFF2CC")
    for k, line in enumerate(GUIDE.splitlines(), start=3):
        g.cell(row=k, column=1, value=line)
    g.column_dimensions["A"].width = 120
    label_fill = PatternFill("solid", fgColor="E2EFDA")
    for task in TASKS:
        spec = SHEETS[task]
        ws = wb.create_sheet(task.capitalize())
        cols = ["id"] + spec["show"] + [c for c, _ in spec["label"]]
        ws.append(cols)
        for c, name in enumerate(cols, start=1):
            cell = ws.cell(row=1, column=c)
            cell.font = Font(bold=True)
            if name in dict(spec["label"]):
                cell.fill = label_fill
            ws.column_dimensions[cell.column_letter].width = WIDTH.get(name, 14 if name != "id" else 8)
        for item in rows[task]:   # PDF text can hold control characters a workbook can't store
            ws.append([ILLEGAL_CHARACTERS_RE.sub("", str(item.get(c, ""))) for c in cols])
        for r in ws.iter_rows(min_row=2):
            for cell in r:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        n = len(rows[task]) + 1
        for name, options in spec["label"]:
            c = cols.index(name) + 1
            letter = ws.cell(row=1, column=c).column_letter
            for row in range(2, n + 1):
                ws.cell(row=row, column=c).fill = label_fill
            if options:
                dv = DataValidation(type="list", formula1='"' + ",".join(options) + '"', allow_blank=True)
                ws.add_data_validation(dv)
                dv.add(f"{letter}2:{letter}{n}")
        ws.freeze_panes = "B2"
    wb.save(path)


def make(out: Path, people_n: int, seed: int = 7):
    conn = db.connect()
    rng = random.Random(seed)
    people = [chr(ord("A") + k) for k in range(people_n)]
    items = {"summaries": sample_summaries(conn, rng, SIZES["summaries"]),
             "counters": sample_counters(conn, rng, SIZES["counters"]),
             "mentions": sample_mentions(conn, rng, SIZES["mentions"])}
    plan = {t: assign(items[t], people, rng) for t in TASKS}
    out.mkdir(parents=True, exist_ok=True)
    for p in people:
        write_workbook(out / f"labelling-{p}.xlsx", p, {t: plan[t][p] for t in TASKS})
    (out / "GUIDE.md").write_text(GUIDE)
    key = {"data": str(config.DB_PATH), "people": people, "overlap": OVERLAP,
           "counters": {c["id"]: {"score": c["_score"], "probs": c["_probs"]} for c in items["counters"]},
           "assignment": {t: {p: [i["id"] for i in plan[t][p]] for p in people} for t in TASKS}}
    (out / "key.json").write_text(json.dumps(key, indent=1))
    per = {p: {t: len(plan[t][p]) for t in TASKS} for p in people}
    print(f"wrote {out}: {', '.join(f'labelling-{p}.xlsx' for p in people)}, GUIDE.md, key.json")
    print(f"items: { {t: len(items[t]) for t in TASKS} } ({OVERLAP:.0%} labelled by two people)")
    print(f"rows per person: {per}")


# --- scoring -------------------------------------------------------------------------------

def read_labels(folder: Path) -> dict[str, dict[str, dict[str, dict]]]:
    """{task: {item id: {labeller: row}}} from every labelling-*.xlsx in the folder."""
    from openpyxl import load_workbook
    out = {t: collections.defaultdict(dict) for t in TASKS}
    for path in sorted(folder.glob("labelling-*.xlsx")):
        person = path.stem.split("-", 1)[1]
        wb = load_workbook(path, read_only=True, data_only=True)
        for task in TASKS:
            name = task.capitalize()
            if name not in wb.sheetnames:
                continue
            rows = list(wb[name].iter_rows(values_only=True))
            head = [str(h or "").strip() for h in rows[0]]
            for r in rows[1:]:
                d = {h: ("" if v is None else str(v).strip()) for h, v in zip(head, r)}
                labels = [c for c, _ in SHEETS[task]["label"] if c != "notes" and c != "correct_holder"]
                if d.get("id") and any(d.get(c) for c in labels):
                    out[task][d["id"]][person] = d
    return out


def _kappa(pairs: list[tuple[str, str]]) -> float | None:
    if len(pairs) < 2:
        return None
    cats = sorted({x for p in pairs for x in p})
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    pa = collections.Counter(a for a, _ in pairs)
    pb = collections.Counter(b for _, b in pairs)
    pe = sum(pa[c] / n * pb[c] / n for c in cats)
    return None if pe == 1 else (po - pe) / (1 - pe)


def _agreement(labels: dict, field: str, norm=lambda x: x) -> str:
    pairs = [(norm(v[a][field]), norm(v[b][field])) for v in labels.values() if len(v) >= 2
             for a, b in [sorted(v)[:2]] if v[a].get(field) and v[b].get(field)]
    if not pairs:
        return "no overlap rows labelled yet"
    agree = sum(x == y for x, y in pairs) / len(pairs)
    k = _kappa(pairs)
    return f"{agree:.0%} agree on {len(pairs)} shared rows" + (f", kappa {k:.2f}" if k is not None else "")


def _rate(labels: dict, field: str) -> str:
    vals = [r[field] for v in labels.values() for r in v.values() if r.get(field) in ("y", "n")]
    return f"{sum(x == 'y' for x in vals)}/{len(vals)} ({sum(x == 'y' for x in vals) / len(vals):.0%})" if vals else "—"


def _auc(scores: list[float], positives: list[bool]) -> float | None:
    pos = [s for s, p in zip(scores, positives) if p]
    neg = [s for s, p in zip(scores, positives) if not p]
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def score(folder: Path):
    key = json.loads((folder / "key.json").read_text())
    labels = read_labels(folder)
    lines = ["# Labelling results", ""]
    done = {t: sum(len(v) for v in labels[t].values()) for t in TASKS}
    total = {t: sum(len(v) for v in key["assignment"][t].values()) for t in TASKS}
    lines += ["| Task | Labels in | Assigned |", "| --- | --- | --- |"]
    lines += [f"| {t} | {done[t]} | {total[t]} |" for t in TASKS] + [""]

    s = labels["summaries"]
    lines += ["## Summaries", "", "| Question | Yes | Agreement (shared rows) |", "| --- | --- | --- |"]
    for f in ("attribution_ok", "faithful", "worth_keeping"):
        lines.append(f"| {f} | {_rate(s, f)} | {_agreement(s, f)} |")
    bad = [(i, r) for i, v in s.items() for r in v.values() if r.get("faithful") == "n" and r.get("notes")]
    if bad:
        lines += ["", "Unfaithful, with notes:", ""] + [f"- `{i}` {r['notes']}" for i, r in bad[:20]]

    c = labels["counters"]
    lines += ["", "## Counters", ""]
    rows = [(key["counters"][i]["score"], r["relation of B to A"]) for i, v in c.items() for r in v.values()
            if i in key["counters"] and r.get("relation of B to A") in RELATIONS]
    if rows:
        scores = [x for x, _ in rows]
        for name, positive in (("opposed or qualifies", {"opposed", "qualifies"}), ("opposed only", {"opposed"})):
            pos = [r in positive for _, r in rows]
            auc = _auc(scores, pos)
            best = max(((t, _prf(scores, pos, t)) for t in [x / 20 for x in range(1, 20)]), key=lambda x: x[1][2])
            cur = _prf(scores, pos, 0.5)
            lines += [f"Counter = {name}: {sum(pos)} of {len(pos)} labelled pairs. Judge AUC "
                      f"{auc:.2f}" if auc is not None else f"Counter = {name}: not enough of both kinds yet",
                      f"- at the current threshold 0.5: precision {cur[0]:.0%}, recall {cur[1]:.0%}",
                      f"- best threshold by F1: {best[0]:.2f} (precision {best[1][0]:.0%}, recall {best[1][1]:.0%})", ""]
        dist = collections.Counter(r for _, r in rows)
        lines += ["What the team chose: " + ", ".join(f"{k} {v}" for k, v in dist.most_common()), ""]
        argmax = [(key["counters"][i]["probs"], r["relation of B to A"]) for i, v in c.items() for r in v.values()
                  if i in key["counters"] and r.get("relation of B to A") in RELATIONS]
        same = sum(RELATIONS[int(max(p, key=p.get)) - 1] == r for p, r in argmax)
        lines += [f"The judge's top answer matches the team's on {same} of {len(argmax)} pairs (7 choices).", ""]
    lines += [f"Agreement (7 choices): {_agreement(c, 'relation of B to A')}",
              f"Agreement (counter or not): {_agreement(c, 'relation of B to A', lambda x: x in ('opposed', 'qualifies'))}", ""]

    m = labels["mentions"]
    lines += ["## Mentions", "", "| Question | Yes | Agreement (shared rows) |", "| --- | --- | --- |"]
    for f in ("holder_ok", "voice_ok", "statement_ok", "worth_keeping"):
        lines.append(f"| {f} | {_rate(m, f)} | {_agreement(m, f)} |")
    passes = {str(r[0]): r[1] for r in db.connect().execute("SELECT id, pass FROM mentions")}
    for p in ("author", "voices"):
        sub = {i: v for i, v in m.items() if passes.get(i) == p}
        lines.append(f"\n{p} pass: holder right {_rate(sub, 'holder_ok')}, voice right {_rate(sub, 'voice_ok')}")
    (folder / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n→ {folder / 'report.md'}")


def _prf(scores, positives, t) -> tuple[float, float, float]:
    tp = sum(s >= t and p for s, p in zip(scores, positives))
    fp = sum(s >= t and not p for s, p in zip(scores, positives))
    fn = sum(s < t and p for s, p in zip(scores, positives))
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return prec, rec, (2 * prec * rec / (prec + rec) if prec + rec else 0.0)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    mk = sub.add_parser("make")
    mk.add_argument("--people", type=int, default=4)
    mk.add_argument("--out", default=None)
    mk.add_argument("--seed", type=int, default=7)
    sc = sub.add_parser("score")
    sc.add_argument("folder", nargs="?", default=None)
    a = ap.parse_args()
    default = config.DATA_DIR / "labelling"
    if a.cmd == "make":
        make(Path(a.out) if a.out else default, a.people, a.seed)
    else:
        score(Path(a.folder) if a.folder else default)


if __name__ == "__main__":
    main()
