"""
Builds data/site.sqlite: a single indexed SQLite database containing both
the positive-LMIA employer detail records and the non-compliant-employers
list, plus an aggregated per-employer LMIA view. This replaces shipping a
66MB flat JSON blob to the browser - the site instead loads this .sqlite
file once (via sql.js/WASM) and runs indexed SQL queries (LIKE-based
search; the cdnjs sql.js build has no FTS5 module) against it client-side.

Run: python scripts/build_database.py
"""
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import import_data as nc_mod  # noqa: E402
import import_lmia as lmia_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "site.sqlite"


SCHEMA = """
DROP TABLE IF EXISTS lmia_employers;
CREATE TABLE lmia_employers (
    id INTEGER PRIMARY KEY,
    employer TEXT NOT NULL,
    employer_key TEXT NOT NULL,
    address TEXT,
    province TEXT,
    streams TEXT,
    occupation_count INTEGER,
    total_approved_lmias INTEGER,
    total_approved_positions INTEGER,
    first_period TEXT,
    last_period TEXT,
    periods_seen INTEGER,
    is_noncompliant INTEGER NOT NULL DEFAULT 0
);

DROP TABLE IF EXISTS non_compliant;
CREATE TABLE non_compliant (
    id INTEGER PRIMARY KEY,
    operating_name TEXT,
    operating_name_key TEXT,
    legal_name TEXT,
    legal_name_key TEXT,
    address TEXT,
    reason_codes TEXT,
    reasons TEXT,
    decision_date TEXT,
    penalty TEXT,
    status TEXT
);

DROP TABLE IF EXISTS meta;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def build_non_compliant(conn: sqlite3.Connection) -> set[str]:
    print("Fetching non-compliant employers list...")
    raw = nc_mod.fetch_non_compliant_raw()
    rows = []
    keys: set[str] = set()
    for entry in raw:
        rec = nc_mod.process_entry(entry)
        op_key = lmia_mod.normalize_key(rec["operating_name"])
        legal_key = lmia_mod.normalize_key(rec["legal_name"])
        keys.add(op_key)
        keys.add(legal_key)
        rows.append(
            (
                rec["operating_name"],
                op_key,
                rec["legal_name"],
                legal_key,
                rec["address"],
                ",".join(str(c) for c in rec["reason_codes"]),
                "; ".join(rec["reasons"]),
                rec["decision_date"],
                rec["penalty"],
                rec["status"],
            )
        )
    conn.executemany(
        """INSERT INTO non_compliant
           (operating_name, operating_name_key, legal_name, legal_name_key,
            address, reason_codes, reasons, decision_date, penalty, status)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    print(f"  inserted {len(rows)} non-compliant records")
    return keys


def build_lmia(conn: sqlite3.Connection, noncompliant_keys: set[str]) -> None:
    DATA_DIR.mkdir(exist_ok=True)
    lmia_mod.RAW_DIR.mkdir(exist_ok=True)

    resources = lmia_mod.list_quarterly_resources()
    print(f"Found {len(resources)} LMIA periods to import.")

    detail_rows = []
    for res in resources:
        period, url = res["period"], res["url"]
        cache_path = lmia_mod.RAW_DIR / url.split("/")[-1]
        if cache_path.exists():
            raw = cache_path.read_bytes()
        else:
            print(f"  downloading {period}")
            raw = lmia_mod.fetch_bytes(url)
            if not raw:
                print(f"  WARNING: empty response for {period}, skipping")
                continue
            cache_path.write_bytes(raw)

        try:
            if url.lower().endswith(".csv"):
                records = lmia_mod.parse_csv_bytes(raw, period)
            else:
                tmp = lmia_mod.RAW_DIR / f"_tmp_{cache_path.name}"
                records = lmia_mod.parse_xlsx_bytes(raw, period, tmp)
        except Exception as exc:
            print(f"  WARNING: failed to parse {period} ({url}): {exc}")
            continue

        print(f"  {period}: {len(records)} rows")
        detail_rows.extend(records)

    print(f"Total raw LMIA rows: {len(detail_rows)}")

    aggregated: dict[tuple[str, str], dict] = {}
    for rec in detail_rows:
        employer = rec.get("employer", "")
        address = rec.get("address", "")
        employer_key = lmia_mod.normalize_key(employer)
        approved_lmias = lmia_mod.parse_int(rec.get("approved_lmias")) or 1
        approved_positions = lmia_mod.parse_int(rec.get("approved_positions"))

        key = (employer_key, lmia_mod.normalize_key(address))
        bucket = aggregated.setdefault(
            key,
            {
                "employer": employer,
                "employer_key": employer_key,
                "address": address,
                "province": rec.get("province", ""),
                "streams": set(),
                "occupations": set(),
                "periods": set(),
                "total_approved_lmias": 0,
                "total_approved_positions": 0,
            },
        )
        if rec.get("stream"):
            bucket["streams"].add(rec["stream"])
        if rec.get("occupation"):
            bucket["occupations"].add(rec["occupation"])
        bucket["periods"].add(rec["period"])
        bucket["total_approved_lmias"] += approved_lmias
        bucket["total_approved_positions"] += approved_positions
        if not bucket["province"] and rec.get("province"):
            bucket["province"] = rec["province"]

    employer_insert = []
    for bucket in aggregated.values():
        periods_sorted = sorted(bucket["periods"])
        is_nc = 1 if bucket["employer_key"] in noncompliant_keys else 0
        employer_insert.append(
            (
                bucket["employer"],
                bucket["employer_key"],
                bucket["address"],
                bucket["province"],
                ", ".join(sorted(bucket["streams"])),
                len(bucket["occupations"]),
                bucket["total_approved_lmias"],
                bucket["total_approved_positions"],
                periods_sorted[0] if periods_sorted else "",
                periods_sorted[-1] if periods_sorted else "",
                len(periods_sorted),
                is_nc,
            )
        )

    print(f"Inserting {len(employer_insert)} aggregated employer rows...")
    conn.executemany(
        """INSERT INTO lmia_employers
           (employer, employer_key, address, province, streams, occupation_count,
            total_approved_lmias, total_approved_positions, first_period,
            last_period, periods_seen, is_noncompliant)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        employer_insert,
    )


def build_indexes(conn: sqlite3.Connection) -> None:
    print("Building indexes...")
    conn.executescript(
        """
        CREATE INDEX idx_lmia_employers_key ON lmia_employers(employer_key);
        CREATE INDEX idx_lmia_employers_province ON lmia_employers(province);
        CREATE INDEX idx_lmia_employers_last_period ON lmia_employers(last_period);
        CREATE INDEX idx_lmia_employers_noncompliant ON lmia_employers(is_noncompliant);

        CREATE INDEX idx_nc_op_key ON non_compliant(operating_name_key);
        CREATE INDEX idx_nc_legal_key ON non_compliant(legal_name_key);
        CREATE INDEX idx_nc_date ON non_compliant(decision_date);
        """
    )


def main() -> None:
    from datetime import datetime, timezone

    DATA_DIR.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    noncompliant_keys = build_non_compliant(conn)
    build_lmia(conn, noncompliant_keys)
    conn.commit()

    build_indexes(conn)

    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('built_at', ?)",
        (datetime.now(timezone.utc).isoformat(),),
    )
    conn.commit()

    print("Vacuuming database...")
    conn.execute("VACUUM")
    conn.close()

    size_mb = DB_PATH.stat().st_size / (1024 * 1024)
    print(f"Wrote {DB_PATH} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
