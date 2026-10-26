"""
Downloads the Government of Canada's public list of employers found
non-compliant under the Temporary Foreign Worker Program / International
Mobility Program, and the public positive-LMIA employers list, and writes
cleaned JSON for the site to consume.

Source (non-compliant employers, updated by IRCC on an ongoing basis):
  https://www.canada.ca/content/dam/ircc/documents/json/non_compliant.json
Source page (for attribution / reason-code definitions):
  https://www.canada.ca/en/immigration-refugees-citizenship/services/work-canada/employers-non-compliant.html

Reused under the Canada.ca non-commercial terms and conditions
(https://www.canada.ca/en/transparency/terms.html): reproduced in part,
with attribution and a link back to the original.

Run: python scripts/import_data.py
"""
import json
import re
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_CACHE = DATA_DIR / "non_compliant.raw.json"

NON_COMPLIANT_URL = "https://www.canada.ca/content/dam/ircc/documents/json/non_compliant.json"

# Order matches the numbered reason list published on the source page.
REASON_TEXT = {
    1: "Could not show the information in the offer of employment was true",
    2: "Did not keep documents showing they met the conditions of employing a foreign national",
    3: "Could not pay the wages agreed to with a live-in caregiver",
    4: "Could not show the job description on the LMIA application was true",
    5: "Did not show up for a meeting with the inspector",
    6: "Did not give the inspector the documents requested",
    7: "Did not show up for an inspection, and did not cooperate",
    8: "Broke federal, provincial or territorial hiring/recruiting laws",
    9: "Pay or working conditions did not match the offer of employment, or job was different",
    10: "Live-in caregiver was not living in a private home / not providing unsupervised care",
    11: "Hiring did not create new jobs or job stability for Canadians/permanent residents",
    12: "Hiring did not result in Canadians/permanent residents gaining new or improved skills",
    13: "Did not hire or train Canadians/permanent residents as agreed",
    14: "Did not put in enough effort to hire or train Canadians/permanent residents",
    15: "Was not actively engaged in the business the foreign national was hired for",
    16: "Live-in caregiver did not receive private, furnished living space",
    17: "Did not put in enough effort to keep the workplace free of abuse or reprisal",
    18: "Stopped the foreign national from complying with an Emergencies Act / Quarantine Act order",
    19: "Stopped the foreign national from complying with a provincial public health law",
    20: "Did not pay wages substantially similar to the offer during required isolation/quarantine",
    21: "Did not provide separated accommodations / 2-metre distancing during quarantine",
    22: "Did not provide cleaning products for the accommodations",
    23: "Did not provide a separate bedroom and bathroom for a worker isolating with COVID-19 symptoms",
    24: "Did not provide adequate accommodations for a seasonal agricultural worker",
    25: "Did not provide information on the worker's rights in their chosen official language",
    26: "Did not make rights information available in both official languages",
    27: "Did not put in enough effort to provide access to health care when the worker was injured or ill",
    28: "Charged the foreign national fees related to their hiring (recruitment fees)",
    29: "Did not ensure the foreign national was not charged recruitment fees by a third party",
    30: "Did not obtain and pay for private health insurance covering emergency medical care",
}


def strip_html(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = text.replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", text).strip()


def parse_reason_codes(raw_reason_html: str) -> list[int]:
    return [int(n) for n in re.findall(r">(\d+)<", raw_reason_html)]


def fetch_non_compliant_raw() -> list[dict]:
    """Downloads the raw list, trying curl first (more reliable through some
    proxies/sandboxes than urllib), falling back to urllib, and caching the
    raw response to disk so the processing step can be re-run offline."""
    text = None
    try:
        result = subprocess.run(
            ["curl", "-s", "--max-time", "30", NON_COMPLIANT_URL],
            capture_output=True, text=True, timeout=40, check=True,
        )
        if result.stdout.strip():
            text = result.stdout
    except Exception:
        text = None

    if text is None:
        req = urllib.request.Request(
            NON_COMPLIANT_URL, headers={"User-Agent": "Mozilla/5.0 (data import script)"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8")

    RAW_CACHE.parent.mkdir(exist_ok=True)
    RAW_CACHE.write_text(text, encoding="utf-8")
    return json.loads(text)["list"]


def process_entry(entry: dict) -> dict:
    codes = parse_reason_codes(entry.get("reason", ""))
    return {
        "operating_name": strip_html(entry.get("bn_operating", "")),
        "legal_name": strip_html(entry.get("bn_legal", "")),
        "address": strip_html(entry.get("bn_address_en", "")),
        "reason_codes": codes,
        "reasons": [REASON_TEXT.get(c, f"Reason code {c}") for c in codes],
        "decision_date": entry.get("date", ""),
        "penalty": strip_html(entry.get("penalty_en", "")),
        "status": strip_html(entry.get("status_en", "")),
    }


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)

    raw = fetch_non_compliant_raw()
    processed = [process_entry(e) for e in raw]
    processed.sort(key=lambda e: e["decision_date"], reverse=True)

    out = {
        "source_url": "https://www.canada.ca/en/immigration-refugees-citizenship/services/work-canada/employers-non-compliant.html",
        "source_name": "Government of Canada - Employers who have been found non-compliant",
        "license_note": "Reproduced in part under Canada.ca's non-commercial reuse terms, with attribution and a link to the original.",
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(processed),
        "employers": processed,
    }

    out_path = DATA_DIR / "non_compliant_employers.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")

    reasons_path = DATA_DIR / "reason_codes.json"
    reasons_path.write_text(
        json.dumps(REASON_TEXT, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"Wrote {len(processed)} records to {out_path}")


if __name__ == "__main__":
    main()
