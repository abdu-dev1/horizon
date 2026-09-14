"""
Rebuild data/people_aliases.csv  ->  run:  python build_people_aliases.py

What problem this solves
------------------------
`rsd` and `am` are free text typed by hand over several years, so ONE person
appears under several spellings: "Scott" (117 rows), "Scott B" (14) and
"Scott Brendamour" (53) are the same rep. Left alone that fragments every
per-person view. Measured on 2026-09-11, ALL EIGHT of the worst-retention
Account Managers on the Overview chart were bare-first-name fragments of
people who appeared again, healthier, further down the same chart -- e.g.
"Lindsey" showed 3% retention on one group while "Lindsey Richter" showed 59%
on four. A reader would conclude someone was failing when their real book
renews fine.

This script writes the alias table that real_mode._person() applies at DISPLAY
time. It is safe to normalise there because neither column is a model feature
(train_real.py's CATEGORICAL is product/carrier/state), so no alias can move a
prediction or oblige a retrain.

Re-run it after an ETL that brings in new names. It is idempotent, and it
PRESERVES any row a human added by hand (see "manual rows" below).

What it will and will not merge
-------------------------------
Only mechanical cases, judged over the union of real_scored_book.csv and
real_history.csv (judging on the current window alone is not safe -- it would
call 'Stephanie' -> 'Stephanie C' unique while 'Stephanie Caraway' exists
elsewhere in the book):

  MERGED   a bare first name, or an initialled form ("Scott B"), that matches
           exactly ONE full name in the same column
  MERGED   one-letter surname misspellings of an otherwise identical name
           ("Theresa Gratten" -> "Theresa Grattan")

  LEFT     an initial that does not match the candidate's surname. 'David K'
           is NOT 'David Barrett' -- different surname initial, so probably a
           different David. This constraint exists because an earlier version
           of this script made exactly that wrong merge.
  LEFT     two different surnames sharing a first name ("Fiona Allen" vs
           "Fiona Burnett") -- a name change or two people; only a human knows
  LEFT     nicknames ("Lou Moeller" vs "Louis Moeller", "Bri Calhoun" vs
           "Brianne Calhoun") -- almost certainly one person each, but that is
           a judgment about humans, not about spelling
  LEFT     entries naming TWO people ("Alyssa Warsh / Buzz Hannum") or a
           handoff ("Nicoletta Torres to Melissa Miller"). These are a
           data-entry question, not a spelling one, and they are also excluded
           when deciding what a bare first name resolves to -- not doing that
           was what made 'Alyssa' look ambiguous on the first pass.

Everything in the LEFT list is printed at the end so a human can decide and,
if they want, add the row by hand.

Manual rows
-----------
Any row whose note starts with "manual:" is carried through untouched. That is
the intended way to record a decision this script deliberately will not make:

    am,Lou,Louis Moeller,manual: Lou is Louis (confirmed 2026-09-11)
"""

from __future__ import annotations

import collections
import csv
import re
from pathlib import Path

import pandas as pd

BACKEND = Path(__file__).resolve().parent
DATA = BACKEND / "data"
OUT = DATA / "people_aliases.csv"
SOURCES = ("real_scored_book.csv", "real_history.csv")
FIELDS = ("rsd", "am")

BLANK = {"—", "nan", "", "None"}

# A label naming two people, or carrying a parenthetical aside, is not one
# identity: never merged, and never used to judge another label either.
_COMPOUND = re.compile(r"[/+(]|\bto\b", re.I)
_INITIAL = re.compile(r"^[A-Z]\.?$")


def is_compound(s: str) -> bool:
    if _COMPOUND.search(s):
        return True
    # "Nicoletta Torres John Rawlings" -- four capitalised tokens, i.e. two
    # full names jammed together with no separator at all.
    toks = s.split()
    return len(toks) >= 4 and all(t[:1].isupper() for t in toks)


def is_initial(tok: str) -> bool:
    return bool(_INITIAL.match(tok))


def surname_key(s: str) -> str:
    """Fuzzy surname identity, so Grattan/Gratten and McGlinchey/McGlincey
    collapse: letters only, doubles squeezed, vowels dropped after the first
    character (which is kept so Baker and Booker stay apart)."""
    t = re.sub(r"[^a-z]", "", s.lower())
    t = re.sub(r"(.)\1+", r"\1", t)
    return t[:1] + re.sub(r"[aeiou]", "", t[1:])


def load_counts(field: str) -> collections.Counter:
    counts: collections.Counter = collections.Counter()
    for name in SOURCES:
        path = DATA / name
        if not path.exists():
            continue
        df = pd.read_csv(path, low_memory=False)
        if field not in df.columns:
            continue
        vals = df[field].dropna().astype(str).str.strip()
        counts.update(v for v in vals if v not in BLANK)
    return counts


def propose(field: str) -> tuple[list[tuple[str, str, str, str]], list[str]]:
    counts = load_counts(field)
    labels = [l for l in counts if l not in BLANK]
    simple = [l for l in labels if not is_compound(l)]
    full = [l for l in simple if len(l.split()) >= 2 and not is_initial(l.split()[-1])]

    # One identity per (first name, fuzzy surname); its most-used spelling wins.
    ident: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    for b in full:
        ident[(b.split()[0].lower(), surname_key(b.split()[-1]))].append(b)
    canon_of = {k: max(v, key=lambda c: counts[c]) for k, v in ident.items()}

    rows: list[tuple[str, str, str, str]] = []
    left: list[str] = []

    # (a) spelling variants of one identity
    for k, group in ident.items():
        for g in group:
            if g != canon_of[k]:
                rows.append((field, g, canon_of[k],
                             f"spelling variant ({counts[g]} rows -> {counts[canon_of[k]]})"))

    # (b) bare first names and initialled forms
    for a in sorted(simple):
        if a in full:
            continue
        toks = a.split()
        cands = {k: c for k, c in canon_of.items() if k[0] == toks[0].lower()}
        if not cands:
            continue
        if len(toks) > 1 and is_initial(toks[-1]):
            ini = toks[-1][0].lower()
            cands = {k: c for k, c in cands.items() if c.split()[-1][:1].lower() == ini}
            if not cands:
                left.append(f"{a!r} ({counts[a]} rows) -- initial matches no known surname")
                continue
        if len(cands) == 1:
            canon = next(iter(cands.values()))
            kind = "first name only" if len(toks) == 1 else "initialled form"
            rows.append((field, a, canon, f"{kind} ({counts[a]} rows -> {counts[canon]})"))
        else:
            left.append(f"{a!r} ({counts[a]} rows) -- could be "
                        + " or ".join(repr(c) for c in sorted(set(cands.values()))))
    return rows, left


def existing_manual() -> list[tuple[str, str, str, str]]:
    """Hand-added decisions, kept across regenerations."""
    if not OUT.exists():
        return []
    with OUT.open(encoding="utf-8", newline="") as fh:
        return [(r["field"], r["alias"], r["canonical"], r["note"])
                for r in csv.DictReader(fh)
                if str(r.get("note", "")).strip().lower().startswith("manual:")]


def main() -> None:
    manual = existing_manual()
    generated: list[tuple[str, str, str, str]] = []
    leftovers: dict[str, list[str]] = {}
    for field in FIELDS:
        rows, left = propose(field)
        generated += rows
        leftovers[field] = left

    # A manual row always wins over a generated one for the same alias.
    manual_keys = {(f, a.lower()) for f, a, _, _ in manual}
    generated = [r for r in generated if (r[0], r[1].lower()) not in manual_keys]
    out = sorted(manual + generated, key=lambda r: (r[0], r[1].lower()))

    with OUT.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["field", "alias", "canonical", "note"])
        w.writerows(out)

    print(f"wrote {OUT.relative_to(BACKEND)}  ({len(out)} aliases: "
          f"{len(generated)} generated, {len(manual)} manual)\n")
    for field, rows in (("rsd", [r for r in out if r[0] == "rsd"]),
                        ("am", [r for r in out if r[0] == "am"])):
        print(f"  {field}: {len(rows)} aliases")
    print("\nLEFT FOR A HUMAN (not merged -- add a 'manual:' row to decide one):")
    for field in FIELDS:
        for line in leftovers[field]:
            print(f"  {field:4s} {line}")
    print("\nRestart the app to pick up the new table (real_mode caches it).")


if __name__ == "__main__":
    main()
