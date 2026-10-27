"""Adds a trend_numbered_occupation table to data/site.sqlite: total
approved positions/LMIAs per occupation, restricted to employers whose
name is a bare numbered company ("1234567 Alberta Ltd.", etc. - the
same pattern js/company_info.js uses to decide when to show a
registry-lookup link instead of a real name).

This is a standalone aggregate over the existing built database, not
part of the main import (which re-downloads and re-parses the raw
government files) - it just needs employer_occupation_year, which
build_database.py already populates.

Run: python scripts/build_numbered_company_occupations.py
"""
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "site.sqlite"

# Matches js/company_info.js's NUMBERED_COMPANY_RE exactly.
NUMBERED_COMPANY_RE = re.compile(
    r"^([\d-]{6,10})\s+([A-Za-zÀ-ÿ.& ]+?)\s+(Inc\.?|Ltd\.?|Limited|Corp\.?|Corporation)\b",
    re.IGNORECASE,
)


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("SELECT id, employer FROM lmia_employers")
    numbered_ids = {
        employer_id
        for employer_id, employer in cur.fetchall()
        if NUMBERED_COMPANY_RE.match(employer or "")
    }
    print(f"{len(numbered_ids):,} numbered-company employers")

    conn.executescript("DROP TABLE IF EXISTS _numbered_ids; CREATE TEMP TABLE _numbered_ids (id INTEGER PRIMARY KEY);")
    conn.executemany("INSERT INTO _numbered_ids (id) VALUES (?)", [(i,) for i in numbered_ids])

    cur.execute(
        """
        SELECT o.title, SUM(eoy.total_approved_lmias), SUM(eoy.total_approved_positions)
        FROM employer_occupation_year eoy
        JOIN occupations o ON o.id = eoy.occupation_id
        JOIN _numbered_ids n ON n.id = eoy.employer_id
        GROUP BY o.title
        """
    )
    rows = cur.fetchall()
    print(f"{len(rows):,} distinct occupations among numbered companies")

    conn.executescript(
        """
        DROP TABLE IF EXISTS trend_numbered_occupation;
        CREATE TABLE trend_numbered_occupation (
            occupation TEXT PRIMARY KEY,
            total_approved_lmias INTEGER,
            total_approved_positions INTEGER
        );
        """
    )
    conn.executemany(
        "INSERT INTO trend_numbered_occupation (occupation, total_approved_lmias, total_approved_positions) VALUES (?,?,?)",
        rows,
    )
    conn.commit()
    print(f"Wrote trend_numbered_occupation ({len(rows):,} rows) to {DB_PATH}")


if __name__ == "__main__":
    main()
