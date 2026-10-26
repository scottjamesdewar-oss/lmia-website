"""
Builds data/site.sqlite: a single indexed SQLite database containing both
the positive-LMIA employer detail records and the non-compliant-employers
list, plus an aggregated per-employer LMIA view. This replaces shipping a
66MB flat JSON blob to the browser - the site instead loads this .sqlite
file once (via sql.js/WASM) and runs indexed SQL queries (LIKE-based
search; the cdnjs sql.js build has no FTS5 module) against it client-side.

Run: python scripts/build_database.py
"""
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import import_data as nc_mod  # noqa: E402
import import_lmia as lmia_mod  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "site.sqlite"
GEOCODE_CACHE_DIR = DATA_DIR / "_geocode"
FSA_CENTROIDS_URL = "https://download.geonames.org/export/zip/CA.zip"

POSTAL_FSA_RE = re.compile(r"\b([A-Za-z]\d[A-Za-z])\s?\d[A-Za-z]\d\b")
# Explicit "operating as" markers - covers o/a, oa, d.b.a / dba / d b a
# (with periods, slashes, or spaces between the letters), c/o, and the
# spelled-out forms.
FRANCHISE_RE = re.compile(
    r"\b(?:o\s?/?\s?a|d[\.\s]*b[\.\s]*a|c\s?/\s?o|operating as|doing business as)\.?\s+(.+)$",
    re.IGNORECASE,
)
# A brand only needs this many *explicit* "o/a"-style matches before it's
# trusted enough to also catch via plain substring match (pass 2 below) -
# keeps noise out of the substring pass.
FRANCHISE_SUBSTRING_MIN_COUNT = 5
FRANCHISE_SUBSTRING_MAX_BRANDS = 150

# Generic qualifier words franchise naming sometimes tacks on (e.g.
# "Subway Restaurant", "A & W Restaurant", "Subway Sandwiches and Salads")
# that would otherwise split one brand into several buckets. Longest
# first so a multi-word suffix is stripped whole rather than partially.
FRANCHISE_GENERIC_SUFFIXES = [
    "SANDWICHES AND SALADS",
    "RESTAURANTS",
    "RESTAURANT",
    "SANDWICHES",
]


def canonical_brand_key(brand: str) -> str:
    """Collapses cosmetic variants of the same brand ("A & W Restaurant",
    "A&W Restaurant", "A & W") to one grouping key, without merging
    actually-different brands (e.g. never strips "Pizza" - that's core to
    "Boston Pizza" / "Pizza Hut" / "Little Caesars Pizza")."""
    s = brand.upper()
    s = s.replace("’", "'")
    s = re.sub(r"[.',]", "", s)
    s = re.sub(r"\s*&\s*", "&", s)
    s = re.sub(r"\s+", " ", s).strip()
    for suffix in FRANCHISE_GENERIC_SUFFIXES:
        if s.endswith(" " + suffix):
            s = s[: -(len(suffix) + 1)].strip()
            break
    return s


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
    is_noncompliant INTEGER NOT NULL DEFAULT 0,
    fsa TEXT,
    lat REAL,
    lng REAL
);

DROP TABLE IF EXISTS lmia_map_points;
CREATE TABLE lmia_map_points (
    id INTEGER PRIMARY KEY,
    fsa TEXT NOT NULL,
    period TEXT NOT NULL,
    province TEXT,
    lat REAL,
    lng REAL,
    employer_count INTEGER,
    total_approved_lmias INTEGER,
    total_approved_positions INTEGER
);

DROP TABLE IF EXISTS trend_stream;
CREATE TABLE trend_stream (
    period TEXT NOT NULL,
    stream TEXT NOT NULL,
    employer_count INTEGER,
    total_approved_lmias INTEGER,
    total_approved_positions INTEGER
);

DROP TABLE IF EXISTS trend_province;
CREATE TABLE trend_province (
    period TEXT NOT NULL,
    province TEXT NOT NULL,
    employer_count INTEGER,
    total_approved_lmias INTEGER,
    total_approved_positions INTEGER
);

DROP TABLE IF EXISTS trend_occupation;
CREATE TABLE trend_occupation (
    period TEXT NOT NULL,
    occupation TEXT NOT NULL,
    total_approved_lmias INTEGER,
    total_approved_positions INTEGER
);

DROP TABLE IF EXISTS trend_franchise;
CREATE TABLE trend_franchise (
    period TEXT NOT NULL,
    brand TEXT NOT NULL,
    employer_count INTEGER,
    total_approved_lmias INTEGER,
    total_approved_positions INTEGER
);

-- Drill-down support: occupations dictionary (small, reused by id to
-- avoid repeating title text across ~370k rows) and the per
-- employer/occupation/year detail that backs the Trends chart
-- drill-downs (Other -> occupation -> employer list by postal code).
DROP TABLE IF EXISTS occupations;
CREATE TABLE occupations (
    id INTEGER PRIMARY KEY,
    title TEXT UNIQUE NOT NULL
);

DROP TABLE IF EXISTS employer_occupation_year;
CREATE TABLE employer_occupation_year (
    id INTEGER PRIMARY KEY,
    employer_id INTEGER NOT NULL,
    occupation_id INTEGER NOT NULL,
    year TEXT NOT NULL,
    total_approved_lmias INTEGER,
    total_approved_positions INTEGER
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


def load_fsa_centroids() -> dict[str, tuple[float, float, str]]:
    """Returns {FSA: (lat, lng, province)} from GeoNames' Canadian postal
    code dataset (CC BY 4.0 - https://www.geonames.org/)."""
    import io
    import zipfile

    GEOCODE_CACHE_DIR.mkdir(exist_ok=True)
    cache_path = GEOCODE_CACHE_DIR / "CA.txt"
    if cache_path.exists():
        text = cache_path.read_text(encoding="utf-8")
    else:
        print("Downloading FSA centroid data (GeoNames)...")
        raw = lmia_mod.fetch_bytes(FSA_CENTROIDS_URL)
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            text = zf.read("CA.txt").decode("utf-8")
        cache_path.write_text(text, encoding="utf-8")

    centroids: dict[str, tuple[float, float, str]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        fsa, province, lat, lng = cols[1], cols[3], cols[9], cols[10]
        centroids[fsa.upper()] = (float(lat), float(lng), province)
    print(f"  loaded {len(centroids)} FSA centroids")
    return centroids


def extract_fsa(address: str) -> str:
    m = POSTAL_FSA_RE.search(address)
    return m.group(1).upper() if m else ""


def fetch_lmia_detail_rows() -> list[dict]:
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
    return detail_rows


def build_lmia_employers(
    conn: sqlite3.Connection,
    detail_rows: list[dict],
    noncompliant_keys: set[str],
    fsa_centroids: dict[str, tuple[float, float, str]],
) -> dict[tuple[str, str], int]:
    """Returns {(employer_key, address_key): lmia_employers.id} - the ids
    are SQLite's auto-assigned rowids, which for a fresh INTEGER PRIMARY
    KEY table filled via a single ordered executemany are exactly
    1..N in insertion order, so we can compute them here without a
    round-trip query."""
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
        fsa = extract_fsa(bucket["address"])
        centroid = fsa_centroids.get(fsa)
        lat, lng = (centroid[0], centroid[1]) if centroid else (None, None)
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
                fsa,
                lat,
                lng,
            )
        )

    print(f"Inserting {len(employer_insert)} aggregated employer rows...")
    conn.executemany(
        """INSERT INTO lmia_employers
           (employer, employer_key, address, province, streams, occupation_count,
            total_approved_lmias, total_approved_positions, first_period,
            last_period, periods_seen, is_noncompliant, fsa, lat, lng)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        employer_insert,
    )

    return {key: i + 1 for i, key in enumerate(aggregated.keys())}


def build_employer_occupation_year(
    conn: sqlite3.Connection,
    detail_rows: list[dict],
    employer_id_by_key: dict[tuple[str, str], int],
) -> None:
    """Backs the Trends page drill-downs: click a year's "Other" segment
    to see the occupations in it, click an occupation (whether from
    "Other" or one of the top-N bars directly) to see the employers
    behind it for that year, by postal code."""
    occupation_ids: dict[str, int] = {}

    def occupation_id_for(title: str) -> int:
        oid = occupation_ids.get(title)
        if oid is None:
            oid = len(occupation_ids) + 1
            occupation_ids[title] = oid
        return oid

    aggregated: dict[tuple[int, int, str], dict] = defaultdict(
        lambda: {"lmias": 0, "positions": 0}
    )
    skipped = 0
    for rec in detail_rows:
        key = (lmia_mod.normalize_key(rec.get("employer", "")), lmia_mod.normalize_key(rec.get("address", "")))
        employer_id = employer_id_by_key.get(key)
        occupation = rec.get("occupation", "").strip()
        if employer_id is None or not occupation:
            skipped += 1
            continue
        title = cleanOccupationTitle_py(occupation)
        oid = occupation_id_for(title)
        year = rec["period"][:4]
        bucket = aggregated[(employer_id, oid, year)]
        bucket["lmias"] += lmia_mod.parse_int(rec.get("approved_lmias")) or 1
        bucket["positions"] += lmia_mod.parse_int(rec.get("approved_positions"))

    print(f"  {skipped} detail rows skipped (no occupation or unmatched employer)")

    print(f"Inserting {len(occupation_ids)} occupation dictionary rows...")
    conn.executemany(
        "INSERT INTO occupations (id, title) VALUES (?, ?)",
        [(oid, title) for title, oid in occupation_ids.items()],
    )

    rows = [
        (employer_id, oid, year, b["lmias"], b["positions"])
        for (employer_id, oid, year), b in aggregated.items()
    ]
    print(f"Inserting {len(rows)} employer_occupation_year rows...")
    conn.executemany(
        """INSERT INTO employer_occupation_year
           (employer_id, occupation_id, year, total_approved_lmias, total_approved_positions)
           VALUES (?,?,?,?,?)""",
        rows,
    )


def cleanOccupationTitle_py(occupation: str) -> str:
    i = occupation.find("-")
    return occupation[i + 1 :] if i > 0 else occupation


def build_map_points(
    conn: sqlite3.Connection,
    detail_rows: list[dict],
    fsa_centroids: dict[str, tuple[float, float, str]],
) -> None:
    buckets: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"employers": set(), "lmias": 0, "positions": 0, "province": ""}
    )
    for rec in detail_rows:
        fsa = extract_fsa(rec.get("address", ""))
        if not fsa or fsa not in fsa_centroids:
            continue
        key = (fsa, rec["period"])
        b = buckets[key]
        b["employers"].add(lmia_mod.normalize_key(rec.get("employer", "")))
        b["lmias"] += lmia_mod.parse_int(rec.get("approved_lmias")) or 1
        b["positions"] += lmia_mod.parse_int(rec.get("approved_positions"))
        if not b["province"]:
            b["province"] = rec.get("province", "")

    rows = []
    for (fsa, period), b in buckets.items():
        lat, lng, province = fsa_centroids[fsa]
        rows.append(
            (
                fsa,
                period,
                b["province"] or province,
                lat,
                lng,
                len(b["employers"]),
                b["lmias"],
                b["positions"],
            )
        )

    print(f"Inserting {len(rows)} map point rows (FSA x period)...")
    conn.executemany(
        """INSERT INTO lmia_map_points
           (fsa, period, province, lat, lng, employer_count,
            total_approved_lmias, total_approved_positions)
           VALUES (?,?,?,?,?,?,?,?)""",
        rows,
    )


def build_franchise_assignments(detail_rows: list[dict]) -> list[str]:
    """Two-pass franchise-brand assignment, one brand (or '') per record,
    in the same order as detail_rows.

    Pass 1: explicit "o/a" / "d.b.a." / "c/o" markers only - high
    precision, but misses employers simply *named* after the brand
    ("Tim Hortons", "989240 Alberta Ltd Tim Hortons",
    "...Inc. (Tim Hortons)").

    Pass 2: for records pass 1 didn't match, fall back to a plain
    whole-word substring match against brands that pass 1 already found
    at least FRANCHISE_SUBSTRING_MIN_COUNT times. Restricting the
    substring pass to already-well-evidenced brands avoids false
    positives from one-off parenthetical fragments, store-location
    suffixes, etc. (see scripts/build_database.py history for examples
    of why a blanket "trailing parenthetical = brand" rule isn't safe).
    """
    explicit = [""] * len(detail_rows)
    counts: dict[str, int] = defaultdict(int)
    for i, rec in enumerate(detail_rows):
        m = FRANCHISE_RE.search(rec.get("employer", ""))
        if m:
            brand = m.group(1).strip().rstrip(".").strip().upper()
            if brand:
                explicit[i] = brand
                counts[brand] += 1

    known_brands = [b for b, c in counts.items() if c >= FRANCHISE_SUBSTRING_MIN_COUNT]
    known_brands.sort(key=lambda b: counts[b], reverse=True)
    known_brands = known_brands[:FRANCHISE_SUBSTRING_MAX_BRANDS]
    print(f"  {len(known_brands)} brands qualify for substring fallback matching")

    if not known_brands:
        return explicit

    combined = re.compile(
        "|".join(r"\b" + re.escape(b) + r"\b" for b in sorted(known_brands, key=len, reverse=True)),
        re.IGNORECASE,
    )
    assigned = explicit[:]
    fallback_hits = 0
    for i, rec in enumerate(detail_rows):
        if assigned[i]:
            continue
        m = combined.search(rec.get("employer", ""))
        if m:
            assigned[i] = m.group(0).upper()
            fallback_hits += 1
    print(f"  substring fallback matched {fallback_hits} additional records")

    # Pass 1's "everything after the marker" capture also grabs trailing
    # store/location qualifiers ("o/a Tim Hortons Okotoks" -> "TIM HORTONS
    # OKOTOKS"), fragmenting an otherwise well-evidenced brand into dozens
    # of one-off variants. Where the assigned text starts with a known
    # brand followed by more text, truncate it back to just the brand.
    known_brands_by_len = sorted(known_brands, key=len, reverse=True)
    truncated = 0
    for i, brand in enumerate(assigned):
        if not brand:
            continue
        for kb in known_brands_by_len:
            if brand == kb:
                break
            if brand.startswith(kb) and not brand[len(kb)].isalnum():
                assigned[i] = kb
                truncated += 1
                break
    print(f"  truncated {truncated} brand+location strings to their known brand prefix")
    return assigned


def build_trends(conn: sqlite3.Connection, detail_rows: list[dict]) -> None:
    stream_buckets: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"employers": set(), "lmias": 0, "positions": 0}
    )
    province_buckets: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"employers": set(), "lmias": 0, "positions": 0}
    )
    occupation_buckets: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"lmias": 0, "positions": 0}
    )
    franchise_buckets: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"employers": set(), "lmias": 0, "positions": 0}
    )

    print("Assigning franchise brands (two-pass)...")
    franchise_assignments = build_franchise_assignments(detail_rows)

    for rec, brand in zip(detail_rows, franchise_assignments):
        period = rec["period"]
        employer_key = lmia_mod.normalize_key(rec.get("employer", ""))
        lmias = lmia_mod.parse_int(rec.get("approved_lmias")) or 1
        positions = lmia_mod.parse_int(rec.get("approved_positions"))

        stream = rec.get("stream", "").strip()
        if stream:
            b = stream_buckets[(period, stream)]
            b["employers"].add(employer_key)
            b["lmias"] += lmias
            b["positions"] += positions

        province = rec.get("province", "").strip()
        if province:
            b = province_buckets[(period, province)]
            b["employers"].add(employer_key)
            b["lmias"] += lmias
            b["positions"] += positions

        occupation = rec.get("occupation", "").strip()
        if occupation:
            b = occupation_buckets[(period, occupation)]
            b["lmias"] += lmias
            b["positions"] += positions

        if brand:
            b = franchise_buckets[(period, brand)]
            b["employers"].add(employer_key)
            b["lmias"] += lmias
            b["positions"] += positions

    print(f"Inserting {len(stream_buckets)} trend_stream rows...")
    conn.executemany(
        "INSERT INTO trend_stream (period, stream, employer_count, total_approved_lmias, total_approved_positions) VALUES (?,?,?,?,?)",
        [
            (period, stream, len(b["employers"]), b["lmias"], b["positions"])
            for (period, stream), b in stream_buckets.items()
        ],
    )

    print(f"Inserting {len(province_buckets)} trend_province rows...")
    conn.executemany(
        "INSERT INTO trend_province (period, province, employer_count, total_approved_lmias, total_approved_positions) VALUES (?,?,?,?,?)",
        [
            (period, province, len(b["employers"]), b["lmias"], b["positions"])
            for (period, province), b in province_buckets.items()
        ],
    )

    print(f"Inserting {len(occupation_buckets)} trend_occupation rows...")
    conn.executemany(
        "INSERT INTO trend_occupation (period, occupation, total_approved_lmias, total_approved_positions) VALUES (?,?,?,?)",
        [
            (period, occupation, b["lmias"], b["positions"])
            for (period, occupation), b in occupation_buckets.items()
        ],
    )

    # Merge cosmetic spelling variants of the same brand ("A & W
    # Restaurant" / "A&W Restaurant" / "A&W") into one canonical bucket,
    # keeping whichever raw spelling has the most total positions as the
    # display label.
    canon_groups: dict[str, dict] = defaultdict(
        lambda: {"labels": defaultdict(int), "periods": defaultdict(
            lambda: {"employers": set(), "lmias": 0, "positions": 0}
        )}
    )
    for (period, raw_brand), b in franchise_buckets.items():
        canon = canonical_brand_key(raw_brand)
        group = canon_groups[canon]
        group["labels"][raw_brand] += b["positions"]
        pb = group["periods"][period]
        pb["employers"] |= b["employers"]
        pb["lmias"] += b["lmias"]
        pb["positions"] += b["positions"]

    def has_generic_suffix(label: str) -> bool:
        upper = label.upper()
        return any(upper.endswith(" " + suf) for suf in FRANCHISE_GENERIC_SUFFIXES)

    merged_rows = []
    for group in canon_groups.values():
        # Prefer a suffix-free spelling ("A&W" over "A&W Restaurant") when
        # one has reasonable support, falling back to the top overall
        # variant otherwise.
        clean = {k: v for k, v in group["labels"].items() if not has_generic_suffix(k)}
        pool = clean if clean else group["labels"]
        display = max(pool.items(), key=lambda kv: kv[1])[0]
        for period, pb in group["periods"].items():
            merged_rows.append((period, display, len(pb["employers"]), pb["lmias"], pb["positions"]))

    print(f"Inserting {len(merged_rows)} trend_franchise rows ({len(canon_groups)} canonical brands)...")
    conn.executemany(
        "INSERT INTO trend_franchise (period, brand, employer_count, total_approved_lmias, total_approved_positions) VALUES (?,?,?,?,?)",
        merged_rows,
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

        CREATE INDEX idx_lmia_employers_fsa ON lmia_employers(fsa);
        CREATE INDEX idx_map_points_period ON lmia_map_points(period);
        CREATE INDEX idx_map_points_fsa ON lmia_map_points(fsa);

        CREATE INDEX idx_trend_stream_period ON trend_stream(period);
        CREATE INDEX idx_trend_province_period ON trend_province(period);
        CREATE INDEX idx_trend_occupation_period ON trend_occupation(period);
        CREATE INDEX idx_trend_franchise_period ON trend_franchise(period);

        CREATE INDEX idx_eoy_occupation_year ON employer_occupation_year(occupation_id, year);
        CREATE INDEX idx_eoy_employer ON employer_occupation_year(employer_id);
        """
    )


def main() -> None:
    from datetime import datetime, timezone

    DATA_DIR.mkdir(exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)

    fsa_centroids = load_fsa_centroids()
    noncompliant_keys = build_non_compliant(conn)
    detail_rows = fetch_lmia_detail_rows()
    employer_id_by_key = build_lmia_employers(conn, detail_rows, noncompliant_keys, fsa_centroids)
    build_map_points(conn, detail_rows, fsa_centroids)
    build_trends(conn, detail_rows)
    build_employer_occupation_year(conn, detail_rows, employer_id_by_key)
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
