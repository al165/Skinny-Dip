"""For each recall seed, the two closest mentions of its document, for judging recall by eye.

    python experiments/seed_candidates.py data-test4/args.sqlite
"""
import json
import sqlite3
import sys
from pathlib import Path

from rapidfuzz import fuzz

DOCS = {"techpolicy": "Most Researchers%", "mit": "We read%", "smallai": "Small AI%"}


def main(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    seeds = json.loads((Path(__file__).parent / "seeds.json").read_text())["seeds"]
    for s in seeds:
        ms = conn.execute("SELECT m.* FROM mentions m JOIN items i ON i.id=m.item_id WHERE i.title LIKE ?",
                          (DOCS[s["doc"]],)).fetchall()
        best = sorted(ms, key=lambda m: -fuzz.token_set_ratio(s["view"].lower(), m["statement"].lower()))[:2]
        print(f"\n[{s['doc']}] {s['holder']}: {s['view']}")
        for m in best:
            print(f"   {m['pass'][0].upper()} {m['holder'][:35]} [{m['voice']},{m['attribution_check']}] "
                  f"{m['statement'][:110]}")


if __name__ == "__main__":
    main(sys.argv[1])
