"""
Downloads and aggregates every available quarterly/annual release of the
Government of Canada's "Employers Who Were Issued a Positive Labour Market
Impact Assessment (LMIA)" open dataset (2014-present), and writes one
cleaned, aggregated JSON file for the site to consume.

IMPORTANT: this is the POSITIVE LMIA list only. It shows employers whose
LMIA application was approved. The federal government does not publish a
list of pending or refused LMIA applications (that information is not
public), so there is no "application status" beyond "approved" available
from any public source. See data/non_compliant_employers.json (built by
import_data.py) for the separate, official list of employers found to have
actually violated program conditions.

Dataset landing page:
  https://open.canada.ca/data/en/dataset/90fed587-1364-4f33-a9ee-208181dc0b97
Reused under the Canada.ca / Open Government Licence - Canada non-commercial
terms, with attribution and a link back to the original.

Run: python scripts/import_lmia.py
"""
import csv
import io
import json
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "_lmia_raw"

PACKAGE_ID = "90fed587-1364-4f33-a9ee-208181dc0b97"
PACKAGE_API = f"https://open.canada.ca/data/api/3/action/package_show?id={PACKAGE_ID}"

HEADER_MAP = {
    "province/territory": "province",
    "province": "province",
    "program stream": "stream",
    "stream": "stream",
    "employer": "employer",
    "address": "address",
    "occupation": "occupation",
    "occupations under noc 2011": "occupation",
    "occupations under noc 2021": "occupation",
    "noc 2011": "occupation",
    "noc 2021": "occupation",
    "incorporate status": "incorporate_status",
    "incorporation status": "incorporate_status",
    "approved lmias": "approved_lmias",
    "positions": "approved_positions",
    "positions approved": "approved_positions",
    "approved positions": "approved_positions",
}


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, timeout=60)
    return result.stdout


def fetch_bytes(url: str) -> bytes:
    out = run(["curl", "-sL", "--max-time", "45", url])
    if out:
        return out
    import urllib.request

    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=45) as resp:
        return resp.read()


def list_quarterly_resources() -> list[dict]:
    raw = fetch_bytes(PACKAGE_API)
    payload = json.loads(raw)
    resources = payload["result"]["resources"]

    # English-only, one resource per period label (dedupe repeated uploads).
    by_period: dict[str, dict] = {}
    for r in resources:
        url = r.get("url", "")
        name = r.get("name", "")
        if not (url.endswith("_en.csv") or url.endswith("_en.xlsx") or url.endswith("_EN.xlsx")):
            continue
        period_match = re.match(r"^(\d{4}(?:Q\d(?:Q\d)?)?)", name)
        period = period_match.group(1) if period_match else name[:20]
        # Prefer shorter/cleaner canonical open.canada.ca URLs over mirrors.
        if period not in by_period or len(url) < len(by_period[period]["url"]):
            by_period[period] = {"period": period, "url": url, "name": name}

    return sorted(by_period.values(), key=lambda r: r["period"])


def norm_header_cell(cell) -> str:
    text = str(cell or "").strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def clean_text(value) -> str:
    text = str(value or "").replace("\xa0", " ").strip()
    return re.sub(r"\s+", " ", text)


def parse_int(value) -> int:
    text = clean_text(value).replace(",", "")
    match = re.search(r"\d+", text)
    return int(match.group()) if match else 0


FOOTER_MARKERS = {"total", "other employers", "grand total"}


def is_footer_marker(employer_text: str) -> bool:
    text = employer_text.strip().lower()
    if text in FOOTER_MARKERS:
        return True
    if text in ("notes:", "note:"):
        return True
    return False


def parse_rows(rows: list[list], period: str) -> list[dict]:
    """Generic parser for the various table layouts used across years:
    some files have a Province/Stream column on every row, others only
    print it once per section (forward-fill), and the oldest files (2014,
    2015) group employers under a bare province-name row, with a fresh
    'Employer | Address | Positions' header repeated under every province,
    instead of a Province column at all."""
    records = []
    header_keys: list[str | None] = []
    has_province_col = False
    last_values: dict[int, str] = {}
    province_section = ""
    prev_nonblank: list[str] | None = None

    for row in rows:
        cells = [clean_text(c) for c in row]
        if not any(cells):
            continue

        is_header = any(
            norm_header_cell(c) == "employer" or norm_header_cell(c).startswith("employer ")
            for c in row
        )
        if is_header:
            # The oldest files print a lone province name on its own row
            # immediately before each section's header row.
            if prev_nonblank is not None:
                prev_non_empty = [c for c in prev_nonblank if c]
                if len(prev_non_empty) == 1:
                    province_section = prev_non_empty[0]
            header_keys = [HEADER_MAP.get(norm_header_cell(c)) for c in row]
            has_province_col = "province" in header_keys
            last_values = {}
            prev_nonblank = cells
            continue

        if not header_keys:
            prev_nonblank = cells
            continue  # title row(s) before the first header

        record: dict[str, str] = {}
        for idx, key in enumerate(header_keys):
            if key is None:
                continue
            val = cells[idx] if idx < len(cells) else ""
            if not val and idx in last_values:
                val = last_values[idx]
            if val:
                last_values[idx] = val
            record[key] = val

        if not record.get("employer"):
            prev_nonblank = cells
            continue
        if is_footer_marker(record["employer"]):
            # "Total" / "Other employers" / "Notes:" / numbered footnotes -
            # everything from here to the end of the file is boilerplate,
            # not employer data.
            break
        if not record.get("province") and not has_province_col:
            record["province"] = province_section
        record["period"] = period
        records.append(record)
        prev_nonblank = cells

    return records


def parse_csv_bytes(raw: bytes, period: str) -> list[dict]:
    for enc in ("cp1252", "utf-8-sig", "latin-1"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))
    return parse_rows(rows, period)


def parse_xlsx_bytes(raw: bytes, period: str, tmp_path: Path) -> list[dict]:
    tmp_path.write_bytes(raw)
    wb = openpyxl.load_workbook(tmp_path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    wb.close()
    return parse_rows(rows, period)


def normalize_key(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().upper())


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    RAW_DIR.mkdir(exist_ok=True)

    resources = list_quarterly_resources()
    print(f"Found {len(resources)} periods to import.")

    all_records: list[dict] = []
    for res in resources:
        period, url = res["period"], res["url"]
        cache_name = url.split("/")[-1]
        cache_path = RAW_DIR / cache_name
        if cache_path.exists():
            raw = cache_path.read_bytes()
        else:
            print(f"  downloading {period}: {url}")
            raw = fetch_bytes(url)
            if not raw:
                print(f"  WARNING: empty response for {period}, skipping")
                continue
            cache_path.write_bytes(raw)

        try:
            if url.lower().endswith(".csv"):
                records = parse_csv_bytes(raw, period)
            else:
                records = parse_xlsx_bytes(raw, period, RAW_DIR / f"_tmp_{cache_name}")
        except Exception as exc:
            print(f"  WARNING: failed to parse {period} ({url}): {exc}")
            continue

        print(f"  {period}: {len(records)} rows")
        all_records.extend(records)

    print(f"Total raw rows across all periods: {len(all_records)}")

    # Aggregate per (employer, address) so repeat quarters/occupations for
    # the same business location collapse into one row, while distinct
    # franchise/branch locations of the same brand stay separate.
    aggregated: dict[tuple[str, str], dict] = {}
    for rec in all_records:
        employer = rec.get("employer", "")
        address = rec.get("address", "")
        key = (normalize_key(employer), normalize_key(address))
        bucket = aggregated.setdefault(
            key,
            {
                "employer": employer,
                "address": address,
                "province": rec.get("province", ""),
                "streams": set(),
                "occupations": set(),
                "incorporate_statuses": set(),
                "periods": set(),
                "total_approved_lmias": 0,
                "total_approved_positions": 0,
                "record_count": 0,
            },
        )
        if rec.get("stream"):
            bucket["streams"].add(rec["stream"])
        if rec.get("occupation"):
            bucket["occupations"].add(rec["occupation"])
        if rec.get("incorporate_status"):
            bucket["incorporate_statuses"].add(rec["incorporate_status"])
        bucket["periods"].add(rec["period"])
        bucket["total_approved_lmias"] += parse_int(rec.get("approved_lmias")) or 1
        bucket["total_approved_positions"] += parse_int(rec.get("approved_positions"))
        bucket["record_count"] += 1
        if not bucket["province"] and rec.get("province"):
            bucket["province"] = rec["province"]

    employers = []
    for bucket in aggregated.values():
        periods_sorted = sorted(bucket["periods"])
        employers.append(
            {
                "employer": bucket["employer"],
                "address": bucket["address"],
                "province": bucket["province"],
                "streams": sorted(bucket["streams"]),
                "occupations": sorted(bucket["occupations"])[:10],
                "occupation_count": len(bucket["occupations"]),
                "incorporate_statuses": sorted(bucket["incorporate_statuses"]),
                "total_approved_lmias": bucket["total_approved_lmias"],
                "total_approved_positions": bucket["total_approved_positions"],
                "first_period": periods_sorted[0] if periods_sorted else "",
                "last_period": periods_sorted[-1] if periods_sorted else "",
                "periods_seen": len(periods_sorted),
            }
        )

    employers.sort(key=lambda e: e["last_period"], reverse=True)

    out = {
        "source_url": "https://open.canada.ca/data/en/dataset/90fed587-1364-4f33-a9ee-208181dc0b97",
        "source_name": "Government of Canada - Positive LMIA Employers List (open data)",
        "license_note": "Reproduced in part under the Open Government Licence - Canada / Canada.ca non-commercial reuse terms, with attribution and a link to the original.",
        "status_note": (
            "This list shows employers APPROVED for an LMIA ('positive' decisions) only. "
            "The federal government does not publish pending or refused LMIA applications; "
            "no public source provides that status. Appearing here is not evidence of any "
            "wrongdoing — see the separate non-compliant-employers list for confirmed violations."
        ),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "periods_covered": [r["period"] for r in resources],
        "raw_row_count": len(all_records),
        "employer_count": len(employers),
        "employers": employers,
    }

    out_path = DATA_DIR / "lmia_approved_employers.json"
    out_path.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    print(f"Wrote {len(employers)} aggregated employer records to {out_path}")


if __name__ == "__main__":
    main()
