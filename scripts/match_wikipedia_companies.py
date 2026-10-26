"""Matches LMIA Database employer names against Wikipedia company
articles, as a bulk-friendly alternative to scraping corporate
registries (see about.html for why we don't do that).

Wikipedia's API is explicitly built for reuse (CC BY-SA, generous
documented rate limits for a well-identified client), unlike the
provincial corporate registries, so this is a legitimate automated
pass rather than a terms-of-use violation.

For every distinct, non-numbered employer name in data/site.sqlite:
  1. Search Wikipedia for the name (two attempts: full name, then with
     the trailing legal suffix like "Inc."/"Corporation" stripped).
  2. Fetch the top candidate's intro paragraph.
  3. Accept it only if the intro text contains a business-indicating
     keyword (company, restaurant, chain, retailer, ...) AND the
     employer name and article title are textually similar enough
     (guards against opensearch matching an unrelated article that
     happens to share a word).
  4. Store accepted matches, keyed by employer_key, with the intro
     text (trimmed) as the description and the article URL as source.

Progress is appended to wikipedia_match_cache.jsonl as it runs, so the
script can be stopped and resumed (already-processed employer_keys are
skipped on the next run). Re-run build_company_info_auto() separately,
or just re-run this whole script, to regenerate js/company_info_auto.js
from the cache at any point.

Usage:
    python scripts/match_wikipedia_companies.py            # run the match
    python scripts/match_wikipedia_companies.py --rebuild  # only rebuild
                                                             # the JS file
                                                             # from the
                                                             # existing cache
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "site.sqlite"
CACHE_PATH = ROOT / "scripts" / "wikipedia_match_cache.jsonl"
OUTPUT_JS = ROOT / "js" / "company_info_auto.js"

USER_AGENT = "lmia-website-research/1.0 (https://github.com/scottjamesdewar-oss/lmia-website; contact: scott.james.dewar@gmail.com)"
API_URL = "https://en.wikipedia.org/w/api.php"

NUMBERED_COMPANY_RE = re.compile(r"^[\d-]{6,10}\s+[A-Za-zÀ-ÿ.& ]+?\s+(Inc\.?|Ltd\.?|Limited|Corp\.?|Corporation)\b", re.I)
LEGAL_SUFFIX_RE = re.compile(
    r"\s+(Inc\.?|Ltd\.?|Limited|Corp\.?|Corporation|ULC|LP|LLP|L\.P\.|GP|Co\.?|Company|Group|Holdings?)\.?\s*$",
    re.I,
)

BUSINESS_KEYWORDS = [
    "company", "corporation", "corp.", "chain", "restaurant", "retailer",
    "retail chain", "hotel", "franchise", "manufactur", "brand",
    "enterprise", "bank", "airline", "supermarket", "grocery", "firm",
    "business", "subsidiary", "headquartered", "founded in", "founded by",
    "owns and operates", "operates", "is a canadian", "is an american",
    "is a privately held", "publicly traded", "stock exchange",
]

MIN_SIMILARITY = 0.45

# Minimum total_approved_positions for an employer to be worth checking.
# Wikipedia essentially never has an article on a business this small,
# so this keeps the run tractable without materially reducing coverage
# of the employers readers are likely to look up.
MIN_POSITIONS = 3

# Shared across threads: a simple interval-based rate limiter so we
# never fire requests faster than this, no matter how many workers are
# running concurrently.
class RateLimiter:
    def __init__(self, min_interval: float):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._next_ok = 0.0

    def wait(self):
        with self._lock:
            now = time.monotonic()
            delay = max(0.0, self._next_ok - now)
            self._next_ok = max(now, self._next_ok) + self.min_interval
        if delay:
            time.sleep(delay)


rate_limiter = RateLimiter(0.08)  # ~12.5 req/s across all threads
session = requests.Session()
session.headers.update({"User-Agent": USER_AGENT})
cache_lock = threading.Lock()


def api_get(params: dict) -> dict | None:
    params = {**params, "format": "json"}
    for attempt in range(3):
        rate_limiter.wait()
        try:
            resp = session.get(API_URL, params=params, timeout=15)
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException:
            pass
        time.sleep(1.5 * (attempt + 1))
    return None


def normalize_for_compare(name: str) -> str:
    name = LEGAL_SUFFIX_RE.sub("", name)
    name = re.sub(r"[^\w\s]", "", name, flags=re.UNICODE)
    return re.sub(r"\s+", " ", name).strip().upper()


def search_candidate_title(query: str) -> str | None:
    data = api_get({"action": "query", "list": "search", "srsearch": query, "srlimit": 1, "srnamespace": 0})
    if not data:
        return None
    hits = data.get("query", {}).get("search", [])
    return hits[0]["title"] if hits else None


def fetch_intro(title: str) -> tuple[str | None, str | None, bool]:
    """Returns (resolved_title, intro_text, is_disambiguation)."""
    data = api_get(
        {
            "action": "query",
            "prop": "extracts|pageprops",
            "exintro": 1,
            "explaintext": 1,
            "redirects": 1,
            "titles": title,
        }
    )
    if not data:
        return None, None, False
    pages = data.get("query", {}).get("pages", {})
    for page in pages.values():
        if "missing" in page:
            return None, None, False
        is_disambig = "disambiguation" in page.get("pageprops", {})
        return page.get("title"), page.get("extract", ""), is_disambig
    return None, None, False


def truncate_sentences(text: str, max_len: int = 320) -> str:
    text = text.strip().split("\n")[0]
    if len(text) <= max_len:
        return text
    cut = text[:max_len]
    last_period = cut.rfind(". ")
    if last_period > 80:
        return cut[: last_period + 1]
    return cut.rsplit(" ", 1)[0] + "…"


def match_employer(employer: str) -> dict:
    queries = [employer]
    stripped = LEGAL_SUFFIX_RE.sub("", employer).strip()
    if stripped and stripped.upper() != employer.upper():
        queries.append(stripped)

    seen_titles: set[str] = set()
    for q in queries:
        title = search_candidate_title(q)
        if not title or title in seen_titles:
            continue
        seen_titles.add(title)

        resolved_title, extract, is_disambig = fetch_intro(title)
        if not resolved_title or not extract or is_disambig:
            continue

        lower_extract = extract.lower()
        if not any(kw in lower_extract for kw in BUSINESS_KEYWORDS):
            continue
        # Every employer in this dataset is a business operating in
        # Canada (that's the precondition for an LMIA). Requiring the
        # article to mention Canada/Canadian rejects same-named but
        # unrelated foreign companies (e.g. a US "Smith Enterprises"
        # that happens to share a name with a Canadian one).
        if "canad" not in lower_extract:
            continue

        similarity = SequenceMatcher(
            None, normalize_for_compare(employer), normalize_for_compare(resolved_title)
        ).ratio()
        if similarity < MIN_SIMILARITY:
            continue

        return {
            "status": "matched",
            "title": resolved_title,
            "url": "https://en.wikipedia.org/wiki/" + resolved_title.replace(" ", "_"),
            "description": truncate_sentences(extract),
            "similarity": round(similarity, 3),
        }

    return {"status": "no_match"}


def load_processed_keys() -> set[str]:
    if not CACHE_PATH.exists():
        return set()
    keys = set()
    with CACHE_PATH.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                keys.add(rec["employer_key"])
            except (json.JSONDecodeError, KeyError):
                continue
    return keys


def append_cache(record: dict):
    with cache_lock:
        with CACHE_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def build_company_info_auto():
    """Rebuilds js/company_info_auto.js from the latest cache state
    (last record per employer_key wins, so re-runs can correct earlier
    results)."""
    latest: dict[str, dict] = {}
    if CACHE_PATH.exists():
        with CACHE_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                latest[rec["employer_key"]] = rec

    matched = {k: v for k, v in latest.items() if v.get("status") == "matched"}

    lines = [
        "// AUTO-GENERATED — do not hand-edit. Produced by",
        "// scripts/match_wikipedia_companies.py from Wikipedia's API: a",
        "// reuse-licensed (CC BY-SA), bulk-friendly source, matched against",
        "// employer names in data/site.sqlite with a conservative similarity +",
        "// keyword filter (see the script for the exact rules). Regenerate by",
        "// re-running that script; entries here are overridden by any matching",
        "// key in js/company_info.js's hand-curated MANUAL_COMPANY_INFO.",
        "const COMPANY_INFO_AUTO = {",
    ]
    for key in sorted(matched):
        rec = matched[key]
        desc = json.dumps(f"{rec['description']} (via Wikipedia)")
        url = json.dumps(rec["url"])
        lines.append(f"  {json.dumps(key)}: {{ description: {desc}, url: {url} }},")
    lines.append("};")
    OUTPUT_JS.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print(f"Wrote {len(matched):,} matched companies to {OUTPUT_JS}")


def main():
    if "--rebuild" in sys.argv:
        build_company_info_auto()
        return

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        SELECT employer_key, MAX(employer) AS employer, SUM(total_approved_positions) AS positions
        FROM lmia_employers
        GROUP BY employer_key
        HAVING positions >= ?
        ORDER BY positions DESC
        """,
        (MIN_POSITIONS,),
    )
    rows = cur.fetchall()
    conn.close()

    candidates = [(key, employer) for key, employer, _ in rows if not NUMBERED_COMPANY_RE.match(employer or "")]
    processed = load_processed_keys()
    todo = [(key, employer) for key, employer in candidates if key not in processed]

    print(f"{len(candidates):,} candidate employers (>= {MIN_POSITIONS} positions, non-numbered)")
    print(f"{len(processed):,} already processed, {len(todo):,} remaining")

    count = 0
    matched_count = 0
    start = time.monotonic()
    lock = threading.Lock()

    def worker(item):
        nonlocal count, matched_count
        key, employer = item
        result = match_employer(employer)
        result["employer_key"] = key
        result["employer"] = employer
        append_cache(result)
        with lock:
            count += 1
            if result["status"] == "matched":
                matched_count += 1
            if count % 200 == 0:
                elapsed = time.monotonic() - start
                rate = count / elapsed if elapsed else 0
                remaining = len(todo) - count
                eta_hr = (remaining / rate / 3600) if rate else float("inf")
                print(
                    f"[{count:,}/{len(todo):,}] matched={matched_count:,} "
                    f"rate={rate:.1f}/s eta={eta_hr:.1f}h",
                    flush=True,
                )
                build_company_info_auto()

    with ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(worker, todo))

    build_company_info_auto()
    print(f"Done. {matched_count:,} matched out of {count:,} processed this run.")


if __name__ == "__main__":
    main()
