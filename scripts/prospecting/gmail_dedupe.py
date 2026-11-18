"""Gmail-tarkistus: onko prospektia jo kontaktoitu.

  python gmail_dedupe.py batches exports/prospektit [--size 25]
      -> tulostaa Gmail-hakukyselyt (domain-ryhmät), aja ne Gmailissa / Gmail-MCP:llä.
      Jo tarkistetut avaimet (gmail_checked.txt) jätetään pois; osumat -> gmail_contacted.txt.
  python gmail_dedupe.py finalize exports/prospektit --contacted osumat.txt [--limit 1000]
      -> kirjoittaa prospektit_ei_kontaktoitu.csv ilman osumia (domain tai osoite per rivi).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from crawl_prospects import FREE_MAIL, validate_row, write_csv  # noqa: E402


def load(out: Path) -> list[dict]:
    rows = [json.loads(l) for l in (out / "prospektit_raw.jsonl").read_text().splitlines() if l.strip()]
    return [v for r in rows if (v := validate_row(r))]


def key(r: dict) -> str:
    d = r["sahkoposti"].split("@")[1]
    return r["sahkoposti"] if d in FREE_MAIL else r["domain"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["batches", "finalize"])
    ap.add_argument("out")
    ap.add_argument("--size", type=int, default=25)
    ap.add_argument("--contacted")
    ap.add_argument("--limit", type=int, default=1000)
    a = ap.parse_args()
    rows = load(Path(a.out))
    if a.cmd == "batches":
        checked_path = Path(a.out) / "gmail_checked.txt"
        checked = set(checked_path.read_text().split()) if checked_path.exists() else set()
        keys = [k for k in dict.fromkeys(key(r) for r in rows) if k not in checked]
        for i in range(0, len(keys), a.size):
            part = keys[i:i + a.size]
            print(" OR ".join(f"to:{k} OR from:{k}" for k in part))
        return
    contacted = set()
    if not a.contacted and (Path(a.out) / "gmail_contacted.txt").exists():
        a.contacted = str(Path(a.out) / "gmail_contacted.txt")
    if a.contacted:
        contacted = {l.strip().lower() for l in Path(a.contacted).read_text().splitlines() if l.strip()}
    checked_path = Path(a.out) / "gmail_checked.txt"
    checked = {l.strip().lower() for l in checked_path.read_text().splitlines()} if checked_path.exists() else set()
    unchecked = [r for r in rows if key(r).lower() not in checked]
    rows = [r for r in rows if key(r).lower() in checked]
    if unchecked:
        print(f"{len(unchecked)} riviä odottaa Gmail-tarkistusta (ei mukana)")
    keep = [r for r in rows if key(r).lower() not in contacted and r["domain"] not in contacted
            and r["sahkoposti"].lower() not in contacted]
    write_csv(keep[: a.limit], Path(a.out) / "prospektit_ei_kontaktoitu.csv")
    print(f"{len(rows)} prospektia, kontaktoituja {len(rows) - len(keep)}, listalle {min(len(keep), a.limit)}")


if __name__ == "__main__":
    main()
