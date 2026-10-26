# TFW Program Violations Tracker

A static, searchable site built on two Government of Canada open datasets:

- **Violations** — employers officially found non-compliant under the
  Temporary Foreign Worker Program (TFWP) / International Mobility Program
  (IMP).
- **LMIA Database** — every employer approved for a positive Labour Market
  Impact Assessment since 2014, cross-referenced against the violations
  list.

## Why two separate lists

Approval for an LMIA is **not** evidence of wrongdoing — most employers in
the LMIA Database have no compliance issue at all. Only the Violations list
reflects a confirmed finding by the government's own inspection process.
Employers that appear on both are flagged in the LMIA Database, rather than
implying wrongdoing for the ~182,000 employers who aren't.

## Architecture

The LMIA dataset alone is ~400,000 historical records. Rather than ship
that as JSON (which hit ~66MB and was impractical to search client-side),
the site ships a single indexed **SQLite database**
(`data/site.sqlite`, ~36MB) that the browser downloads once and queries
directly with [sql.js](https://sql.js.org/) (SQLite compiled to
WebAssembly). The violations list lives in the same database. Search uses
indexed `LIKE` queries (the sql.js build served from cdnjs has no FTS5
module).

## Structure

- `scripts/import_data.py` — fetches & cleans the non-compliant-employers
  list (also used as a library by `build_database.py`).
- `scripts/import_lmia.py` — fetches & parses every quarterly/annual
  positive-LMIA release since 2014 (also used as a library).
- `scripts/build_database.py` — the actual build: runs both of the above
  and writes `data/site.sqlite` with indexes and a `lmia_employers`
  ↔ `non_compliant` cross-reference flag.
- `data/site.sqlite` — the one data file the site ships.
- `data/reason_codes.json` — small numeric reason-code → text map, used
  for the violations page's reason filter.
- `data/_lmia_raw/` — gitignored cache of downloaded source files, so
  re-running the build doesn't re-download everything.
- `index.html` (violations), `lmia.html` (LMIA database), `about.html`.
- `css/style.css`, `js/db.js` (shared sql.js loader), `js/app.js`,
  `js/lmia.js`.

## Updating the data

```bash
python scripts/build_database.py
```

Re-downloads anything not already cached in `data/_lmia_raw/` and rebuilds
`data/site.sqlite` from scratch. The non-compliant list updates on an
ongoing basis; the LMIA list releases roughly quarterly. There's no fixed
schedule for either, so re-run this periodically (e.g. weekly) rather than
expecting daily updates — and note that no public source publishes
pending/refused LMIA applications, only approved ones.

To force a fresh download instead of using the cache, delete
`data/_lmia_raw/` first.

## Running locally

```bash
python -m http.server 8420
```

Then open `http://localhost:8420/`.

## Sourcing & licensing

- Violations: [Government of Canada — Employers who have been found
  non-compliant](https://www.canada.ca/en/immigration-refugees-citizenship/services/work-canada/employers-non-compliant.html).
- LMIA Database: [Positive LMIA Employers List (open
  data)](https://open.canada.ca/data/en/dataset/90fed587-1364-4f33-a9ee-208181dc0b97).

Both reproduced in part under [Canada.ca's non-commercial reuse
terms](https://www.canada.ca/en/transparency/terms.html) / the Open
Government Licence – Canada, with attribution and a link to the original.
This project is independently run and is not affiliated with, or endorsed
by, the Government of Canada.
