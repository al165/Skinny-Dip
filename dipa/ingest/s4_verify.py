"""Stage 4: check every quote against the verbatim source.

Fuzzy match (RapidFuzz partial_ratio_alignment) on normalised text: lower case, straight quotes,
one space. Looks in the claimed passage first, then its neighbours, then the whole document, and
keeps the best. Offsets are mapped back to the original passage text, so the UI can highlight
the exact words. quote_ok = score >= QUOTE_MIN_SCORE and the quote has at least 5 words."""
import re

from rapidfuzz import fuzz

from dipa import config, db
from dipa.ingest.common import log

_TRANS = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"', "–": "-", "—": "-", " ": " "})


def normalise(text: str) -> tuple[str, list[int]]:
    """Normalised text + for each of its characters the index in the original."""
    out, idx, space = [], [], False
    for i, ch in enumerate(text.translate(_TRANS)):
        if ch.isspace() or ch in "*_#>`":  # markdown emphasis/heading marks never belong in a quote match
            if not space and out:
                out.append(" ")
                idx.append(i)
            space = True
            continue
        space = False
        out.append(ch.lower())
        idx.append(i)
    return "".join(out).strip(), idx


def best_match(quote: str, passages: list) -> tuple[float, int | None, int | None, int | None]:
    q, _ = normalise(quote)
    q = re.sub(r"\s*(\.\.\.|…)\s*", " ", q).strip()
    best = (0.0, None, None, None)
    if not q:
        return best
    for p in passages:
        t, idx = normalise(p["text"])
        if not t:
            continue
        al = fuzz.partial_ratio_alignment(q, t)
        if al and al.score > best[0]:
            start = idx[al.dest_start] if al.dest_start < len(idx) else None
            end = idx[al.dest_end - 1] + 1 if 0 < al.dest_end <= len(idx) else None
            best = (al.score, p["id"], start, end)
            if al.score >= 99.5:
                break
    return best


def run(conn, item_id: int):
    passages = conn.execute("SELECT id, ord, text FROM passages WHERE item_id=? ORDER BY ord",
                            (item_id,)).fetchall()
    by_id = {p["id"]: i for i, p in enumerate(passages)}
    rows = conn.execute("SELECT id, passage_id, quote FROM mentions WHERE item_id=?", (item_id,)).fetchall()
    ok = 0
    with db.tx(conn):
        for m in rows:
            order = []
            if m["passage_id"] in by_id:  # claimed passage, then +-2 neighbours, then everything
                i = by_id[m["passage_id"]]
                near = [passages[j] for j in (i, i - 1, i + 1, i - 2, i + 2) if 0 <= j < len(passages)]
                near_ids = {p["id"] for p in near}
                order = near + [p for p in passages if p["id"] not in near_ids]
            else:
                order = list(passages)
            score, pid, start, end = best_match(m["quote"], order[:5])
            if score < config.QUOTE_MIN_SCORE:
                score2, pid2, start2, end2 = best_match(m["quote"], order[5:])
                if score2 > score:
                    score, pid, start, end = score2, pid2, start2, end2
            good = int(score >= config.QUOTE_MIN_SCORE and len(m["quote"].split()) >= 5)
            ok += good
            conn.execute("UPDATE mentions SET quote_score=?, quote_passage=?, quote_start=?, quote_end=?, "
                         "quote_ok=? WHERE id=?", (round(score, 1), pid, start, end, good, m["id"]))
    log(f"  quotes verified: {ok}/{len(rows)}" + (f" ({ok / len(rows):.0%})" if rows else ""))
