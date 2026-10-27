"""Adds a trend_employer_churn table to data/site.sqlite: for each
calendar year, how many approved positions (and how many employers)
came from employers using the LMIA program for the first time that
year, versus employers who had already appeared in an earlier year.

Uses employer_occupation_year, which only has occupation-level detail
from 2016 onward (2014-2015 releases didn't break out occupations) -
so an employer's "first year" here means first year with occupation
detail, not necessarily their true first LMIA ever if that was a
2014/2015 application. This likely slightly overstates "new" employers
in 2016 itself (some are really repeat users first captured in 2016);
all effect fades out from 2017 onward since 2016 is excluded as a
first-year baseline.

Standalone aggregate over the existing built database, same pattern as
build_numbered_company_occupations.py.

Run: python scripts/build_employer_churn.py
"""
import sqlite3
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "site.sqlite"


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute(
        """
        SELECT employer_id, year, SUM(total_approved_positions)
        FROM employer_occupation_year
        GROUP BY employer_id, year
        """
    )
    rows = cur.fetchall()
    print(f"{len(rows):,} employer-year rows")

    by_employer = defaultdict(list)
    for employer_id, year, positions in rows:
        by_employer[employer_id].append((year, positions or 0))

    first_year = {
        employer_id: min(y for y, _ in years) for employer_id, years in by_employer.items()
    }

    # 2016 is the first year with occupation detail at all, so everyone
    # there is trivially "new" by this method - exclude it from the chart.
    totals = defaultdict(lambda: {"new_positions": 0, "new_employers": 0, "returning_positions": 0, "returning_employers": 0})
    for employer_id, years in by_employer.items():
        fy = first_year[employer_id]
        for year, positions in years:
            if year == "2016":
                continue
            bucket = totals[year]
            if year == fy:
                bucket["new_positions"] += positions
                bucket["new_employers"] += 1
            else:
                bucket["returning_positions"] += positions
                bucket["returning_employers"] += 1

    out_rows = [
        (
            year,
            v["new_positions"],
            v["new_employers"],
            v["returning_positions"],
            v["returning_employers"],
        )
        for year, v in sorted(totals.items())
    ]

    conn.executescript(
        """
        DROP TABLE IF EXISTS trend_employer_churn;
        CREATE TABLE trend_employer_churn (
            year TEXT PRIMARY KEY,
            new_positions INTEGER,
            new_employers INTEGER,
            returning_positions INTEGER,
            returning_employers INTEGER
        );
        """
    )
    conn.executemany(
        "INSERT INTO trend_employer_churn VALUES (?,?,?,?,?)",
        out_rows,
    )
    conn.commit()
    for row in out_rows:
        print(row)
    print(f"Wrote trend_employer_churn ({len(out_rows)} years) to {DB_PATH}")


if __name__ == "__main__":
    main()
