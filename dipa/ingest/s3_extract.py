"""Stage 3: extract attributed mentions, in two passes per window of passages.

  pass "author": what the author puts forward (argued | conceded)
  pass "voices": every view held by someone else (reported | endorsed | rejected), even one
                 mentioned in a single clause. A separate pass so minority views aren't dropped.

Each mention carries a verbatim quote and the passage it came from; stage 4 checks the quote."""
import re
from concurrent.futures import ThreadPoolExecutor

from rapidfuzz import fuzz

from dipa import config, db
from dipa.llm import LLMError
from dipa.ingest.common import HOLDER_TYPES, KINDS, _db_lock, ask, fmt_secs, log
from dipa.ingest.s4_verify import normalise

CUE_MIN_SCORE = 85  # a voices-pass item whose attribution cue isn't in the window is dropped

SYSTEM = ("You extract arguments from documents for a research tool that maps debates. "
          "You are exact about WHO holds each view, and you copy quotes character for character.")


def _schema(voices: list[str], holder_types: list[str], attributed: bool) -> dict:
    props = {"passage": {"type": "integer"}}
    if attributed:  # written first, so the model must find the attribution before it writes the view
        props["cue"] = {"type": "string"}
    props |= {"holder": {"type": "string"}, "holder_type": {"type": "string", "enum": holder_types},
              "statement": {"type": "string"}}
    if attributed:
        props["author_response"] = {"type": "string"}
    props |= {"voice": {"type": "string", "enum": voices}, "kind": {"type": "string", "enum": KINDS},
              "quote": {"type": "string"}}
    return {"type": "object", "properties": {"items": {"type": "array", "items": {
        "type": "object", "properties": props, "required": list(props)}}}, "required": ["items"]}


AUTHOR_SCHEMA = _schema(["argued", "conceded"], ["author"], attributed=False)
VOICES_SCHEMA = _schema(["reported", "endorsed", "rejected"], HOLDER_TYPES[1:], attributed=True)

CONTEXT = """DOCUMENT: "{title}"
AUTHOR: {author}
SUMMARY: {summary}
AUTHOR'S MAIN LINE: {stance}
VOICES NAMED IN THE DOCUMENT:
{voices}"""

COMMON_RULES = """- statement: ONE self-contained sentence that a reader understands without the passage. Replace "it", "this", "they" with what they refer to. Never start with "The author", "The passage" or "The text". Write the proposition only; who holds it goes in holder.
- kind: position (a claim or argument someone could disagree with) | fact (a figure, date or event stated as fact) | example (a specific case, project or story used to make a point) | recommendation (what should be done) | definition | question (a question the text poses).
- quote: copy 6-40 CONSECUTIVE words EXACTLY as they appear in the passage (same spelling and punctuation, no "..."), the words that best support the statement.
- passage: the [P..] number the quote comes from.
- Say the point being made, not just a title or a topic: "The paper discusses the risks of large language models" is too vague; say which risks or what it argues. A title, heading, image caption or credit on its own is not a point: skip it.
- One item per distinct point; do not repeat a point. Skip transitions, filler and minor details that only support a point you have already listed. Keep passage order.
- If there is nothing of this kind, return an empty list."""

AUTHOR_TASK = """TASK: list what the AUTHOR ({author}) puts forward IN THEIR OWN VOICE in the passages below: their positions and arguments, facts and figures they state, examples they describe, recommendations, definitions, questions they pose.
Do NOT list content the text attributes to someone else; another reader lists those separately:
- what a named or quoted person says, thinks, wrote, tweeted or did ("Bender said...", "Dean wrote that...", "She was cut off from her email", "Krishnan collaborates with...")
- what a cited paper, study, survey or report argues or finds ("the paper lays out...", "Strubell and her collaborators found...")
- what another person's or group's project, artwork, book or research does or argues ("Fruiting Bodies challenges...", "Masakhane builds...")
- quotations, epigraphs, image captions and credits.
If the author adds their OWN judgement on such content (agrees, disagrees, draws a conclusion from it), list that judgement.
- holder: "{author}".
- voice: argued = the author asserts it | conceded = the author grants a point that cuts against their own case ("to be fair...", "admittedly...").
{rules}"""

VOICES_TASK = """TASK: list every view in the passages below that the text ATTRIBUTES to someone other than the author ({author}): people or organisations quoted or paraphrased, companies, governments, "the industry", critics, proponents, "some researchers", "many people believe", "it has been argued", and views the author sets up in order to argue against them. Also list:
- what a cited paper, study, survey or report argues or finds (holder = its authors if the text names them, e.g. "Gebru and her coauthors", else the work itself)
- what a featured project, artwork, book or piece of research does or argues (holder = its creator if the text names them, else the project's name with holder_type work)
- what a named person or group did, when the text describes their work or actions (voice usually reported)
- narratives, stories and promises the text says others tell, sell or believe ("one story often told is...", "we're sold the idea that...", "big tech tells us...", "investors keep funding..."), even when the author attacks them (voice rejected).
Diversity matters: a view mentioned in a single clause counts as much as one discussed at length.
Only list a view if the text itself attributes it to someone else. A point the author makes in their own voice is NOT another voice, even if it is about the industry or policymakers: skip it.
- cue: the exact words in the passage that attribute the view to its holder ("it's been argued that", "Amodei said", "according to the survey", "critics claim", "industry figures promise"). If you cannot point to such words, do not list the item.
- holder: WHO holds the view, as specifically as the text allows: a person, organisation, company, government or group of people ("Emily M. Bender", "Google", "AI companies", "some researchers", "policymakers"). Use the name from the VOICES list when it matches. Never "{author}".
  The holder is never an idea, narrative, concept, practice, process, image, event or "the text". For a narrative or idea ("the cloud narrative", "universalism"), the holder is whoever the text says tells, sells or believes it ("big tech", "AI companies", "investors"); if the text does not say who, write "unspecified". A project, paper or artwork is the holder only when the text presents it as making the argument and does not name its creator.
- holder_type: person | org | government (a state, ministry, regulator or colonial power) | industry (a sector or companies in general) | field (researchers, a discipline) | public | work (a project, paper, artwork or report whose creator the text does not name) | unspecified (e.g. "some argue").
- statement: the view as its holder would put it, without the holder's name.
- author_response: in at most 12 words, how the author treats this view in the text (agrees, disagrees and why, or just reports it).
- voice: from author_response: rejected = the author disagrees or argues against it (common: views are often introduced in order to be refuted) | endorsed = the author agrees or cites it as support | reported = the author neither agrees nor disagrees.
{rules}"""

CHECK_SCHEMA = {"type": "object", "properties": {"verdicts": {"type": "array", "items": {
    "type": "object",
    "properties": {"item": {"type": "integer"},
                   "held_by": {"type": "string", "enum": ["holder", "author"]},
                   "holder_kind": {"type": "string", "enum": ["who", "what"]},
                   "who": {"type": "string"},
                   "who_type": {"type": "string", "enum": HOLDER_TYPES[1:]}},
    "required": ["item", "held_by", "holder_kind", "who", "who_type"]}}}, "required": ["verdicts"]}

CHECK_PROMPT = """The AUTHOR of "{title}" is: {author}.
Below are passages from it, then numbered views that another reader attributed to people OTHER than the author. Check each one against the passages.

held_by:
- holder = the passages present this as the view of the named holder or of someone other than the author: they name or quote them, report what they say or believe ("it's been argued", "companies tell us", "critics claim", "Amodei defined"), or describe a position the author then argues against. This includes narratives, stories and promises the text says are told, sold or believed by others ("one story often told is...", "we're sold a narrative that...", "big tech tells us...", "investors keep funding..."): the narrative itself is the holder's view, even when the author attacks it.
- author = the author states it in their own voice. This includes the author's own claims ABOUT a group: "the industry hypes AGI" or "this creates bad science" are the author's claims, not the industry's view.

holder_kind:
- who = the holder is a person, organisation, company, government or group of people, or a project, paper or artwork the passages present as making the argument.
- what = the holder is an idea, narrative, concept, practice, process, image, event or the text itself ("the cloud narrative", "universalism", "AGI orientation", "AI-generated image of...").
who / who_type: if what, whoever the passages say tells, promotes or believes this view ("big tech" / industry, "AI companies" / industry), or "unspecified" / unspecified if they do not say. If who, repeat the holder and its type (person | org | government | industry | field | public | work | unspecified).

Return one verdict for EVERY item, in order ({n} items).

PASSAGES:
{passages}

VIEWS:
{items}"""

REATTR_SCHEMA = {"type": "object", "properties": {"verdicts": {"type": "array", "items": {
    "type": "object",
    "properties": {"item": {"type": "integer"},
                   "held_by": {"type": "string", "enum": ["author", "other"]},
                   "holder": {"type": "string"},
                   "holder_type": {"type": "string", "enum": HOLDER_TYPES[1:]},
                   "voice": {"type": "string", "enum": ["reported", "endorsed", "rejected"]}},
    "required": ["item", "held_by", "holder", "holder_type", "voice"]}}}, "required": ["verdicts"]}

REATTR_PROMPT = """The AUTHOR of "{title}" is: {author}.
Below are passages from it, then numbered statements that another reader credited to the author. Each one names or concerns someone else. Check each against the passages.

held_by:
- author = the author asserts it in their own voice. This includes the author's own judgement or conclusion about someone else's work ("this shows that...", "the paper is right that...").
- other = the passage reports what someone else said, believes, wrote, found, made or did: a quote or paraphrase, a survey's or paper's findings, what a project or artwork does or argues, a person's activities or experiences.
If other: holder = who, as specifically as the passages name them (the person or creator if named, else the paper, project or organisation); holder_type = person | org | government | industry | field | public | work | unspecified; voice = how the author treats it: reported (neutral) | endorsed (agrees, uses it as support) | rejected (argues against it).
If author: holder = "{author}", holder_type and voice: anything.

Return one verdict for EVERY item, in order ({n} items).

PASSAGES:
{passages}

STATEMENTS:
{items}"""

NOT_A_WHO = re.compile(r"\b(narratives?|discourses?|orientation|imaginar(y|ies)|branding|myths?|paradox|"
                       r"perceptions?|notions?|concepts?|ideas?)\b", re.I)
PEOPLE = re.compile(r"\b(proponents|critics|advocates|supporters|believers|people|researchers|those|"
                    r"companies|scholars|experts|authors|leaders)\b", re.I)
NAMED_TYPES = {"person", "org", "work"}


def _is_named(name: str, known: list[str]) -> bool:
    """Is this one of the named people, organisations or works on the document's cast list?"""
    n = name.strip().lower()
    return any(fuzz.token_set_ratio(n, k.lower()) >= 90 for k in known)


GENERIC_TYPES = {"government", "industry", "field", "public", "unspecified"}  # "policymakers", "the AI industry": the author's claims ABOUT them are common


def _names_voice(it: dict, names: list[str]) -> str | None:
    """The first named voice (person, org, government, work) an author-pass item mentions."""
    text = f"{it['statement']} {it['quote']}".lower()
    for n in names:
        if len(n) >= 4 and fuzz.partial_ratio(n.lower(), text) >= 92:
            return n
    return None


def _echoed_cue(it: dict) -> bool:
    """The voices pass sometimes 'cites' the claim itself as its attribution cue (a long cue that
    is the quote, without the holder's name). That is no attribution: the author's own point."""
    cue, quote = normalise(it["cue"])[0], normalise(it["quote"])[0]
    if len(cue.split()) < 10 or fuzz.partial_ratio(cue, quote) < 85:
        return False
    tokens = [t for t in normalise(it["holder"])[0].split() if len(t) >= 4 and t not in ("some", "many", "other", "their")]
    return not any(t in cue for t in tokens)


PROMPT = """{context}

{task}

{prev}PASSAGES:
{passages}"""


def windows(conn, item_id: int):
    rows = conn.execute("SELECT * FROM passages WHERE item_id=? AND hidden=0 ORDER BY ord",
                        (item_id,)).fetchall()
    win, size = [], 0
    for r in rows:
        if win and size + len(r["text"]) > config.WINDOW_CHARS:
            yield win
            win, size = [], 0
        win.append(r)
        size += len(r["text"])
    if win:
        yield win


def _render(win) -> str:
    out, section = [], None
    for r in win:
        if r["section"] != section:
            section = r["section"]
            out.append(f"## {section}")
        out.append(f"[P{r['ord']}]\n{r['text']}")  # position, not row id: ids change on re-parse
    return "\n\n".join(out)


def _extract(conn, pass_name, task, schema, context, prev, win) -> tuple[dict, float]:
    """One extraction call; a window whose answer overflows the output cap is split in half
    and each half extracted on its own (dense windows, e.g. a page listing many projects)."""
    prompt = PROMPT.format(context=context, task=task, prev=prev, passages=_render(win))
    try:
        out, secs, _ = ask(conn, f"extract_{pass_name}", prompt, schema, system=SYSTEM)
        return out, secs
    except LLMError as e:
        if "token cap" not in str(e) or len(win) < 2:
            raise
        mid = len(win) // 2
        log(f"      {pass_name}: answer too long for {len(win)} passages, splitting the window")
        a, sa = _extract(conn, pass_name, task, schema, context, prev, win[:mid])
        b, sb = _extract(conn, pass_name, task, schema, context, prev, win[mid:])
        return {"items": a["items"] + b["items"]}, sa + sb


def run(conn, item_id: int):
    item = conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()
    cast = conn.execute("SELECT * FROM cast WHERE item_id=? AND type!='author'", (item_id,)).fetchall()
    # names to look for in author-pass items: full names, plus surnames of people ("Krishnan")
    names = []
    for c in cast:
        if c["type"] in GENERIC_TYPES:
            continue
        names.append(c["name"])
        if c["type"] == "person" and " " in c["name"] and " and " not in c["name"]:
            names.append(c["name"].split()[-1])
    context = CONTEXT.format(
        title=item["title"], author=item["author"], summary=item["summary"], stance=item["author_stance"],
        voices="\n".join(f"- {c['name']} ({c['type']}): {c['description']}" for c in cast) or "(none)")
    tasks = {
        "author": (AUTHOR_TASK.format(author=item["author"], rules=COMMON_RULES), AUTHOR_SCHEMA),
        "voices": (VOICES_TASK.format(author=item["author"], rules=COMMON_RULES), VOICES_SCHEMA),
    }
    known = [c["name"] for c in cast if c["type"] in NAMED_TYPES]
    ctx = {"item": item, "context": context, "tasks": tasks, "names": names, "known": known,
           "cast_names": [c["name"] for c in cast]}
    with db.tx(conn):
        conn.execute("DELETE FROM mentions WHERE item_id=?", (item_id,))
    wins = list(windows(conn, item_id))
    jobs = [(wi, win, wins[wi - 1][-1]["text"] if wi else None) for wi, win in enumerate(wins)]
    # Windows are independent (each only sees the previous passage's text), so they run in
    # parallel; results are written in window order. Ollama needs OLLAMA_NUM_PARALLEL >= LLM_PARALLEL
    # for this to be faster (otherwise it queues the requests).
    with ThreadPoolExecutor(max_workers=config.LLM_PARALLEL) as pool:
        futures = [pool.submit(_window, conn, ctx, wi, win, prev, len(wins)) for wi, win, prev in jobs]
        total, n_items, dropped = 0.0, {"author": 0, "voices": 0}, 0
        for (wi, win, _), fut in zip(jobs, futures):
            found, n_drop, secs, notes = fut.result()
            ord_to_id = {r["ord"]: r["id"] for r in win}  # the prompts number passages by position
            total += secs
            dropped += n_drop
            for note in notes:
                log(note)
            with _db_lock, db.tx(conn):  # workers commit cache rows on the same connection
                for pass_name, items in found.items():
                    for it in items:
                        pid = ord_to_id.get(it["passage"])
                        conn.execute(
                            "INSERT INTO mentions(item_id, passage_id, pass, holder, holder_type, voice, kind, "
                            "statement, quote, cue, author_response, attribution_check, holder_orig, model) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (item_id, pid, pass_name, it["holder"].strip(), it["holder_type"], it["voice"],
                             it["kind"], it["statement"].strip(), it["quote"].strip(), it.get("cue"),
                             it.get("author_response"), it.get("check"), it.get("holder_orig"), config.LLM_MODEL))
                        n_items[pass_name] += 1
            log(f"    window {wi + 1}/{len(wins)}: {len(win)} passages · running totals "
                f"author {n_items['author']}, voices {n_items['voices']} · LLM time {fmt_secs(total)}")
    log(f"  extracted {n_items['author']} author + {n_items['voices']} other-voice mentions "
        f"({dropped} dropped) · LLM time {fmt_secs(total)} (summed over parallel calls)")


def _window(conn, ctx: dict, wi: int, win, prev_text: str | None, n_wins: int):
    """Both passes and both checks for one window. Runs in a worker thread: no database writes
    except the LLM cache (locked in ask), log lines are returned, not printed."""
    item, context, tasks, names, known = ctx["item"], ctx["context"], ctx["tasks"], ctx["names"], ctx["known"]
    cast_names = ctx["cast_names"]
    notes, total, dropped = [], 0.0, 0
    win_norm = normalise("\n".join(r["text"] for r in win))[0]
    prev = (f"PREVIOUS PASSAGE (context only, do not extract from it):\n{prev_text[-700:]}\n\n"
            if prev_text else "")
    found = {"author": [], "voices": []}
    with ThreadPoolExecutor(max_workers=2) as passes:  # the two passes of a window in parallel too
        outs = {name: passes.submit(_extract, conn, name, task, schema, context, prev, win)
                for name, (task, schema) in tasks.items()}
    for pass_name, fut in outs.items():
        try:
            out, secs = fut.result()
        except Exception as e:  # noqa: BLE001 - log and go on; a rerun retries uncached windows
            notes.append(f"    window {wi + 1}/{n_wins} pass {pass_name}: FAILED {e}")
            continue
        total += secs
        for it in out["items"]:
            if not it["statement"].strip() or not it["quote"].strip():
                dropped += 1
                continue
            if pass_name == "voices":
                if it["holder"].strip().lower() == (item["author"] or "").strip().lower():
                    dropped += 1  # the author's own point, already covered by the author pass
                    continue
                cue = normalise(it["cue"])[0]
                if len(cue) < 3 or fuzz.partial_ratio(cue, win_norm) < CUE_MIN_SCORE:
                    dropped += 1  # no attribution in the text: probably the author's own point
                    notes.append(f"      dropped (cue not found): {it['holder']}: {it['statement'][:80]}")
                    continue
            found[pass_name].append(it)
    if found["voices"]:
        listed = "\n".join(f"{i + 1}. holder: {it['holder']} | view: {it['statement']} | "
                           f"attribution: \"{it.get('cue', '')}\"" for i, it in enumerate(found["voices"]))
        chk, secs, _ = ask(conn, "check_voices", CHECK_PROMPT.format(
            title=item["title"], author=item["author"], passages=_render(win), items=listed,
            n=len(found["voices"])), CHECK_SCHEMA)
        total += secs
        verdicts = {v["item"]: v for v in chk["verdicts"]}
        for i, it in enumerate(found["voices"]):
            v = verdicts.get(i + 1) or {}
            # Stored, not enforced on its own: the yes/no check is unstable on views the author
            # sets up to reject (gold sample: 4 of 10 flagged items really were the author's).
            it["check"] = v.get("held_by")
            # A cue that is just the claim itself makes the flag stronger. Still not dropped: on the
            # test run, dropping these lost real minority views (AAAI 77%, Klein/Buchanan, Odell).
            if it["check"] == "author" and _echoed_cue(it):
                it["check"] = "author_echo"
            # A narrative, idea or image is not a holder: use whoever the text says holds it.
            # The check's answer is only taken within limits (on the test run it also erased
            # named people and invented holders like "regenerative").
            who = (v.get("who") or "").strip()
            if v.get("holder_kind") == "what" and who and who.lower() != it["holder"].strip().lower() \
                    and not _is_named(it["holder"], known):
                if who.lower().startswith(("the author", "author")):
                    it["check"] = it["check"] if it["check"] in ("author", "author_echo") else "author"
                elif who.lower() == "unspecified" or _is_named(who, cast_names):
                    notes.append(f"      holder {it['holder']!r} is a what: now {who!r}")
                    it["holder_orig"] = it["holder"]
                    it["holder"] = who
                    it["holder_type"] = "unspecified" if who.lower() == "unspecified" else (v.get("who_type") or "unspecified")
            if (NOT_A_WHO.search(it["holder"]) and not PEOPLE.search(it["holder"])) or re.fullmatch(r"(the )?\w+ism", it["holder"].strip().lower()):
                notes.append(f"      holder {it['holder']!r} names an idea: now 'unspecified'")
                it["holder_orig"] = it.get("holder_orig") or it["holder"]
                it["holder"], it["holder_type"] = "unspecified", "unspecified"
    # author-pass items that name another voice: ask whether they are really that voice's
    named = [(i, n) for i, it in enumerate(found["author"]) if (n := _names_voice(it, names))]
    if named:
        listed = "\n".join(f"{k + 1}. {found['author'][i]['statement']} (quote: \"{found['author'][i]['quote']}\"; "
                           f"names {n})" for k, (i, n) in enumerate(named))
        chk, secs, _ = ask(conn, "check_author", REATTR_PROMPT.format(
            title=item["title"], author=item["author"], passages=_render(win), items=listed, n=len(named)),
            REATTR_SCHEMA)
        total += secs
        verdict = {v["item"]: v for v in chk["verdicts"]}
        moved = set()
        for k, (i, n) in enumerate(named):
            v = verdict.get(k + 1)
            if not v or v["held_by"] != "other" or not v["holder"].strip() \
                    or v["holder"].strip().lower() == (item["author"] or "").lower():
                continue
            it = found["author"][i]
            moved.add(i)
            if any(_same_text(it["quote"], o["quote"]) for o in found["voices"]):
                continue  # the voices pass already has it
            found["voices"].append(it | {"holder": v["holder"], "holder_type": v["holder_type"],
                                         "voice": v["voice"], "cue": n, "check": "moved",
                                         "author_response": f"moved from the author pass ({v['voice']})"})
            notes.append(f"      moved to {v['holder']}: {it['statement'][:70]}")
        found["author"] = [it for i, it in enumerate(found["author"]) if i not in moved]
    found, n_dup = _dedupe(found, notes)
    return found, dropped + n_dup, total, notes


DUP_SCORE = 90  # two quotes this similar (partial ratio) are the same piece of text


def _same_text(a: str, b: str) -> bool:
    a, b = normalise(a)[0], normalise(b)[0]
    return min(len(a), len(b)) >= 20 and fuzz.partial_ratio(a, b) >= DUP_SCORE


def _dedupe(found: dict, notes: list) -> tuple[dict, int]:
    """When both passes took the same sentence, decide who holds it:
    - a voices item the check says is really the author's point, that the author does not
      reject, and whose holder is a generic group ("the AI industry"), gives way to the author
      item (a named holder is kept: the check wrongly flagged AAAI's survey, Klein and Buchanan);
    - otherwise the author item gives way when the voices item is reported or endorsed (the
      author pass restated someone else's view);
    - a rejected view is kept next to the author item: the holder's view and the author's
      counter-claim are two different statements, both worth keeping."""
    drop_a, drop_v = set(), set()
    for vi, v in enumerate(found["voices"]):
        for ai, a in enumerate(found["author"]):
            if not _same_text(v["quote"], a["quote"]):
                continue
            if v.get("check") in ("author", "author_echo") and v["voice"] != "rejected" \
                    and v["holder_type"] in GENERIC_TYPES:
                drop_v.add(vi)
                notes.append(f"      dup, kept author's: {v['holder']}: {v['statement'][:70]}")
            elif v["voice"] in ("reported", "endorsed") and v.get("check") in ("holder", "moved", None):
                drop_a.add(ai)
                notes.append(f"      dup, kept {v['holder']}'s: {a['statement'][:70]}")
    out = {"author": [a for i, a in enumerate(found["author"]) if i not in drop_a],
           "voices": [v for i, v in enumerate(found["voices"]) if i not in drop_v]}
    return out, len(drop_a) + len(drop_v)
