"""Adds a trend_ordinary_employer table to data/site.sqlite: the top
employers by approved positions, restricted to the "ordinary named
business" category - i.e. employers excluded from the other three
name-pattern buckets already surfaced elsewhere on the site:

  - numbered companies (e.g. "1234567 Alberta Ltd.") - no real name at
    all; see js/company_info.js's NUMBERED_COMPANY_RE and the
    "Top occupations at numbered companies" chart.
  - generic/opaque-named companies ("X Holdings Ltd.", "X Enterprises
    Inc.", "X Group", "X Capital", ...) - a real name that says
    nothing about what the business does.
  - explicit trade-name (DBA) employers ("Legal Name Inc. o/a Trade
    Name") - already a distinct naming pattern.

What's left is the ~70% of employers whose legal name is itself
informative (e.g. "Smith's Bakery", "ABC Trucking"). This surfaces the
biggest users of the program within just that group - real, checkable
company names, distinct from both the numbered-company chart and the
existing franchise-brand chart (which groups by recognized multi-
location brand, a different axis - a franchise location can itself be
a numbered or DBA employer, and isn't excluded from the brand chart on
that basis).

Standalone aggregate over the already-built site.sqlite (same pattern
as build_numbered_company_occupations.py), not part of the main import.

Run: python scripts/build_ordinary_named_employers.py
"""
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "site.sqlite"

TOP_N = 200

NUMBERED_COMPANY_RE = re.compile(
    r"^([\d-]{6,10})\s+([A-Za-zÀ-ÿ.& ]+?)\s+(Inc\.?|Ltd\.?|Limited|Corp\.?|Corporation)\b",
    re.IGNORECASE,
)
FRANCHISE_RE = re.compile(
    r"\b(?:o\s?/?\s?a|d[\.\s]*b[\.\s]*a|c\s?/\s?o|operating as|doing business as)\.?\s+(.+)$",
    re.IGNORECASE,
)
GENERIC_WORDS_RE = re.compile(r"\b(Holdings?|Enterprises?|Group|Investments?|Capital|Ventures?|Management)\b", re.I)


def is_ordinary_named(employer: str) -> bool:
    if NUMBERED_COMPANY_RE.match(employer or ""):
        return False
    if FRANCHISE_RE.search(employer or ""):
        return False
    if GENERIC_WORDS_RE.search(employer or ""):
        return False
    return True


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        "SELECT employer, employer_key, province, total_approved_lmias, total_approved_positions FROM lmia_employers"
    )
    rows = cur.fetchall()
    ordinary = [r for r in rows if is_ordinary_named(r[0])]
    print(f"{len(ordinary):,} of {len(rows):,} employers are 'ordinary named' ({100 * len(ordinary) / len(rows):.1f}%)")

    ordinary.sort(key=lambda r: r[4] or 0, reverse=True)
    top = ordinary[:TOP_N]

    conn.executescript(
        """
        DROP TABLE IF EXISTS trend_ordinary_employer;
        CREATE TABLE trend_ordinary_employer (
            employer TEXT,
            employer_key TEXT,
            province TEXT,
            total_approved_lmias INTEGER,
            total_approved_positions INTEGER
        );
        """
    )
    conn.executemany(
        "INSERT INTO trend_ordinary_employer (employer, employer_key, province, total_approved_lmias, total_approved_positions) VALUES (?,?,?,?,?)",
        top,
    )
    conn.commit()
    print(f"Wrote trend_ordinary_employer (top {len(top)}) to {DB_PATH}")


if __name__ == "__main__":
    main()
