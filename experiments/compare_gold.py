"""Scores a rerun against the hand-labelled gold sample of an earlier run.

    python experiments/compare_gold.py data-test3/reports/gold_sample.csv data-test4/args.sqlite

Mention ids change between runs, so each gold row is matched to the new mentions of the same
document whose quote overlaps the gold quote. The expected holder is the old holder where the
label said holder_ok=y, else the correction from the notes (EXPECTED below)."""
import csv
import sqlite3
import sys
from pathlib import Path

from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dipa.ingest.s4_verify import normalise  # noqa: E402

# Holder corrections taken from the notes column of the 2026-11-11 gold sample.
# "AUTHOR" = the document's author (author pass); None = should not be extracted at all.
EXPECTED = {
    1035: "AUTHOR", 894: "Gebru", 878: "AUTHOR", 1189: "Fruiting Bodies", 879: "AUTHOR", 905: "Gebru",
    890: "Gebru", 938: "Bender", 1011: "Boulding", 935: "Fitzgerald", 903: "Strubell", 1153: "Krishnan",
    1126: None, 932: "Le Roux", 975: "Fruiting Bodies",
}

# Holders that are as good as the gold one or better, by Claude's judgement (2026-11-11), not the
# labeller's. Reported as a second, looser score so the two stay apart.
ALSO_OK = {
    922: ["Gebru"], 926: ["Gebru"],            # "some researchers" was standing in for the paper's authors
    904: ["Strubell"], 903: ["Strubell"],
    888: ["Gebru"], 910: ["Gebru"],            # the paper's content / Gebru's own work, not the journalist's
    855: ["researchers"], 886: ["some people", "unspecified"],
    876: ["AGI proponents"], 871: ["Alignment discourse"],
    1174: ["Mising"],
}


def holder_matches(m, want: str) -> bool:
    if want == "AUTHOR":
        return m["pass"] == "author"
    return fuzz.partial_ratio(want.lower(), m["holder"].lower()) >= 85


def main(gold_csv, db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = list(csv.DictReader(open(gold_csv)))
    tally = {"author": [0, 0, 0, 0, 0], "voices": [0, 0, 0, 0, 0]}  # [rows, right, not found, author dup, right incl. ALSO_OK]
    lines = []
    for r in rows:
        gid = int(r["id"])
        want = EXPECTED.get(gid, "AUTHOR" if r["pass"] == "author" else r["holder"]) \
            if r["holder_ok (y/n)"] == "n" or gid in EXPECTED else ("AUTHOR" if r["pass"] == "author" else r["holder"])
        q = normalise(r["quote"])[0]
        cands = conn.execute("SELECT m.* FROM mentions m JOIN items i ON i.id=m.item_id WHERE i.title=?",
                             (r["document"],)).fetchall()
        hits = [m for m in cands if fuzz.partial_ratio(q, normalise(m["quote"])[0]) >= 85
                and min(len(q), len(m["quote"])) >= 15]
        t = tally[r["pass"]]
        t[0] += 1
        if want is None:
            ok = not hits
            also_a = False
            verdict = "gone ✓" if ok else "still extracted ✗"
        elif not hits:
            ok = False
            t[2] += 1
            verdict = "NOT FOUND"
            also_a = False
        else:
            ok = any(holder_matches(m, want) for m in hits)
            # the error the gold sample found most: someone else's content also credited to the author
            # (a rejected view next to the author's counter-claim is fine, so only reported/endorsed count)
            also_a = want != "AUTHOR" and r["voice"] not in ("rejected",) and any(m["pass"] == "author" for m in hits)
            t[3] += also_a
            verdict = ("✓" if ok else "✗") + (" +author dup" if also_a else "")
        t[1] += bool(ok and not (want is not None and hits and also_a))
        alt = (not ok and not also_a and hits and want is not None
               and any(holder_matches(m, w) for w in ALSO_OK.get(gid, []) for m in hits))
        t[4] += bool((ok and not (want is not None and hits and also_a)) or alt)
        if alt:
            verdict = "≈ (also ok)"
        got = "; ".join(f"{m['pass'][0].upper()}:{m['holder']} ({m['holder_type']}, {m['voice']})" for m in hits) or "-"
        mark = "" if r["holder_ok (y/n)"] == "y" else " [was wrong]"
        lines.append(f"{verdict:18} #{gid}{mark} want {want or 'nothing'} | now {got}")
    for p, (n, ok, nf, dup, ok2) in tally.items():
        print(f"{p:7} rows: holder right {ok}/{n} (with Claude's alternates {ok2}/{n}) · "
              f"also credited to the author {dup} · not found {nf}")
    print()
    print("\n".join(sorted(lines, key=lambda s: (s.startswith(("✓ ", "≈")), s))))


if __name__ == "__main__":
    main(*sys.argv[1:3])
