"""Stage 6 (corpus-wide): a title, a 1-3 sentence summary and an abstraction level per argument.

The summary is what users search and read, so it must say who holds the view. The LLM writes
it with a "{holder}" placeholder ("{holder} argues that..."), filled in by `render()` from the
holders table: renaming a holder (dipa.edits) re-renders summaries without an LLM call.
Unspecified holders get no placeholder ("Some argue that...").

How each document treats the view (rejected in one, endorsed in another) is not in the LLM
summary: `reception()` builds it from the mention voices, so it is never invented.

Each view comes with the source passage its quote is in (and the end of the passage before),
so the summariser can give the holder's actual reasons: with statements and quotes alone it
filled in reasons the source never gave. Rejected views get no passage: there the passage is
mostly the author's rebuttal, and the model folded it into the holder's view.

Arguments are summarised in batches of BATCH per holder, all batches in parallel. LLM answers
are cached by input, so unchanged arguments cost nothing on a rerun. Locked nodes are skipped."""
import collections
import re

from dipa import config, db
from dipa.ingest.common import ask_all, fmt_secs, log

BATCH = 4
PASSAGE_CHARS = 1000   # source passage shown per mention
BEFORE_CHARS = 300     # end of the passage before it
MENTIONS_WITH_PASSAGE = 2

ABSTRACTION_SCALE = """abstraction: 1-5, the scope of what the view itself asserts, not how big its topic is.
  1 = a single fact, date, name, number or definition (a definition of a big idea like AGI is still 1)
  2 = one specific case, project, person, event or story
  3 = an interpretation or point about one particular topic
  4 = a general argument that applies across many cases
  5 = a sweeping thesis about technology, society or the world"""

SCHEMA = {"type": "object", "properties": {"summaries": {"type": "array", "items": {
    "type": "object",
    "properties": {"item": {"type": "integer"}, "title": {"type": "string"}, "summary": {"type": "string"},
                   "abstraction": {"type": "integer", "minimum": 1, "maximum": 5}},
    "required": ["item", "title", "summary", "abstraction"]}}}, "required": ["summaries"]}

PROMPT = """Write a title and a summary for each numbered view below. All of them are held by {holder_line}.
Each view comes with the statements and verbatim quotes it was extracted from{docs_note}, and for some the source passage the quote is in.

For each view:
- title: 3-8 words that state the view itself, not its topic ("AGI is a misleading research goal", not "AGI"). No holder name.
- summary: 1-3 sentences a reader understands without the source. {who_rule} Say what the view is, and the reason, evidence or example the source gives for it. Use the source passage to understand what the holder claims and why, but use ONLY what it says: add no reasons, examples or context that are not there. Never write "the passage", "the text", "the article" or "the document".
  The source passage may also contain other people's views and the document author's response to this view: keep those out of the summary.
  Summarise the view AS ITS HOLDER WOULD PUT IT. Some views are marked "the document's author rejects this": the source quotes them in order to argue against them. Still state the holder's view, not the author's rebuttal, and do not say that anyone agrees or disagrees: that is shown separately.
- {abstraction}

Return one entry for EVERY view, in order ({n} views).

VIEWS:
{items}"""

WHO_NAMED = ('Start with the literal placeholder {holder} followed by a verb that fits what the view is: '
             '"{holder} argues that..." (a position), "{holder} reports that..." (a fact), "{holder} describes..." '
             '(an example or project), "{holder} defines X as..." (a definition), "{holder} asks..." (a question), '
             '"{holder} recommends..." (a recommendation). Use {holder} once, at the start; after that write "they" '
             'or rephrase. Never write the holder\'s actual name.')
WHO_UNSPECIFIED = ('The view is attributed to no one in particular: start with "Some argue that...", "It is often '
                   'claimed that..." or "A common narrative holds that...".')
WHO_AUTHOR = WHO_NAMED.replace('"{holder} reports that...", ', '')


VERB = re.compile(r"\{holder\} (argues|claims|reports|describes|defines|asks|recommends|suggests|says|notes|"
                  r"states|highlights|proposes|questions|emphasizes|emphasises|warns|contends|maintains|holds|"
                  r"believes|calls|advocates|criticizes|criticises|explains|points|presents|sees|views|"
                  r"challenges|promotes|frames|promises|insists|doubts|shows|finds)\b")


def is_plural(name: str) -> bool:
    """Rough: "X and Y", "investors", "AI companies", "those who..." take plural verbs."""
    n = name.strip().lower()
    last = re.sub(r"\(.*?\)", "", n).strip().split(" ")[-1] if n else ""
    return (" and " in n or " & " in n or n.startswith(("those ", "some ", "many ")) or
            (last.endswith("s") and not last.endswith(("ss", "us", "is")) and len(last) > 3))


def _plural_verb(v: str) -> str:
    if v.endswith("ies"):
        return v[:-3] + "y"
    if v.endswith(("sses", "shes", "ches", "xes", "zes")):
        return v[:-2]
    return v[:-1]


def render(tpl: str | None, holder_name: str) -> str | None:
    """Fill the {holder} placeholder (verb in the plural for plural holders); capitalise it at
    the start of a sentence."""
    if tpl is None:
        return None
    out = tpl
    if is_plural(holder_name):
        out = VERB.sub(lambda m: "{holder} " + _plural_verb(m.group(1)), out)
    out = out.replace("{holder}", holder_name)
    return re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)


def reception(conn, argument_id: str) -> list[dict]:
    """How each document treats the view: [{document, voice}], from the mention voices."""
    rows = conn.execute(
        "SELECT coalesce(i.display_title, i.title), m.voice, coalesce(i.display_source, i.source) "
        "FROM argument_mentions am JOIN mentions m ON m.id=am.mention_id "
        "JOIN items i ON i.id=m.item_id WHERE am.argument_id=? ORDER BY i.id", (argument_id,)).fetchall()
    seen, out = set(), []
    for r in rows:
        if (r[0], r[1]) not in seen:
            seen.add((r[0], r[1]))
            out.append({"document": r[0], "voice": r[1], "url": r[2]})
    return out


def _templatise(summary: str, holder: dict) -> str:
    """Make sure the summary uses the placeholder even if the model wrote the name instead."""
    if holder["type"] == "unspecified" or "{holder}" in summary:
        return summary
    for name in sorted({holder["name"], *_aliases(holder)}, key=len, reverse=True):
        pat = re.compile(re.escape(name), re.I)
        if pat.search(summary):
            return pat.sub("{holder}", summary)
    return "{holder}: " + summary  # last resort: keep the attribution visible


def _aliases(holder) -> list[str]:
    import json
    return json.loads(holder["aliases_json"] or "[]")


def run(conn, only: set[str] | None = None, full: bool = False):
    """Summarise arguments that have no summary yet (stage 5 keeps the summaries of arguments that
    survive a rebuild: new arguments are added, existing text is not rewritten). full=True redoes
    every unlocked argument (after changing this prompt); only= a set of ids, for testing."""
    holders = {r["id"]: r for r in conn.execute("SELECT * FROM holders")}
    args = conn.execute("SELECT * FROM nodes WHERE kind='argument' AND locked=0 ORDER BY holder_id, id").fetchall()
    if only is not None:
        args = [a for a in args if a["id"] in only]
    elif not full:
        args = [a for a in args if a["summary_tpl"] is None]
    if not args:
        log("  summaries: nothing new to summarise")
        return
    ments = collections.defaultdict(list)
    for r in conn.execute(
            "SELECT am.argument_id, m.*, i.title AS doc FROM argument_mentions am JOIN mentions m ON m.id=am.mention_id "
            "JOIN items i ON i.id=m.item_id ORDER BY m.item_id, m.id"):
        ments[r["argument_id"]].append(r)

    passages = _passages(conn, [m for ms in ments.values() for m in ms])

    by_holder = collections.defaultdict(list)
    for a in args:
        by_holder[a["holder_id"]].append(a)
    batches, prompts = [], []
    for hid, group in by_holder.items():
        h = holders[hid]
        if h["type"] == "unspecified":
            holder_line, who = "no one in particular (the text attributes them to unnamed others)", WHO_UNSPECIFIED
        else:
            desc = f", {h['description']}" if h["description"] else ""
            holder_line = f"{{holder}} = {h['name']} ({h['type']}{desc})"
            who = WHO_AUTHOR if h["type"] == "author" else WHO_NAMED
        for i in range(0, len(group), BATCH):
            batch = group[i:i + BATCH]
            docs = {m["doc"] for a in batch for m in ments[a["id"]]}
            lines = []
            for k, a in enumerate(batch):
                lines.append(f"{k + 1}.")
                for j, m in enumerate(ments[a["id"]][:6]):
                    src = f" [in “{m['doc']}”]" if len(docs) > 1 else ""
                    stance = {"rejected": "the document's author rejects this",
                              "endorsed": "the document's author agrees",
                              "reported": "reported without comment"}.get(m["voice"], "")
                    resp = ""  # the author's response text leaked into the holder's view; the label is enough
                    lines.append(f"   - statement{src}: {m['statement']}\n     quote: \"{m['quote']}\""
                                 + (f"\n     note: {stance}{resp}" if stance else ""))
                    # not for rejected views: there the passage is mostly the author's rebuttal, and
                    # the model folded it into the holder's view (tested on data-test5)
                    ctx = passages.get(m["id"]) if j < MENTIONS_WITH_PASSAGE and m["voice"] != "rejected" else None
                    if ctx:
                        lines.append(f"     source passage: <<<{ctx}>>>")
            prompts.append(PROMPT.format(holder_line=holder_line, who_rule=who, abstraction=ABSTRACTION_SCALE,
                                         docs_note="" if len(docs) > 1 else f" (from “{next(iter(docs))}”)",
                                         n=len(batch), items="\n".join(lines)))
            batches.append((h, batch))
    answers = ask_all(conn, "summarise", prompts, SCHEMA)
    secs, missing = 0.0, 0
    with db.tx(conn):
        for (h, batch), (out, s, _) in zip(batches, answers):
            secs += s
            got = {e["item"]: e for e in out["summaries"]}
            for k, a in enumerate(batch):
                e = got.get(k + 1)
                if not e or not e["summary"].strip():
                    missing += 1
                    continue
                tpl = _templatise(e["summary"].strip(), h)
                conn.execute("UPDATE nodes SET title=?, summary_tpl=?, summary=?, abstraction=?, model=? WHERE id=?",
                             (e["title"].strip(), tpl, render(tpl, h["name"]), e["abstraction"], config.LLM_MODEL, a["id"]))
    log(f"  summaries: {len(args) - missing}/{len(args)} arguments in {len(prompts)} calls"
        f"{f' ({missing} missing: rerun stage 6)' if missing else ''} · LLM time {fmt_secs(secs)}")


def rerender(conn):
    """After a holder rename: refresh every rendered summary from its template."""
    with db.tx(conn):
        for r in conn.execute("SELECT n.id, n.summary_tpl, h.name FROM nodes n JOIN holders h ON h.id=n.holder_id "
                              "WHERE n.summary_tpl IS NOT NULL").fetchall():
            conn.execute("UPDATE nodes SET summary=? WHERE id=?", (render(r["summary_tpl"], r["name"]), r["id"]))


def _passages(conn, mentions) -> dict[int, str]:
    """{mention id: the source passage around its quote}: the end of the passage before, then the
    passage the quote was verified in, cut to PASSAGE_CHARS around the quote."""
    out = {}
    for m in mentions:
        pid = m["quote_passage"] or m["passage_id"]
        if not pid:
            continue
        p = conn.execute("SELECT item_id, ord, text FROM passages WHERE id=?", (pid,)).fetchone()
        if p is None:
            continue
        text = p["text"]
        if len(text) > PASSAGE_CHARS:  # keep the window around the quote
            centre = m["quote_start"] if m["quote_start"] is not None else 0
            lo = max(0, min(centre - PASSAGE_CHARS // 3, len(text) - PASSAGE_CHARS))
            text = ("…" if lo else "") + text[lo:lo + PASSAGE_CHARS] + ("…" if lo + PASSAGE_CHARS < len(p["text"]) else "")
        prev = conn.execute("SELECT text FROM passages WHERE item_id=? AND ord<? AND hidden=0 ORDER BY ord DESC LIMIT 1",
                            (p["item_id"], p["ord"])).fetchone()
        before = ("…" + prev["text"][-BEFORE_CHARS:] + "\n") if prev else ""
        out[m["id"]] = (before + text).replace(">>>", ">> >")
    return out

