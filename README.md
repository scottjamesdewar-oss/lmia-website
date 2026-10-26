# TFW Program Violations Tracker

A static, searchable site mirroring the Government of Canada's public list
of employers found non-compliant under the Temporary Foreign Worker Program
(TFWP) and International Mobility Program (IMP).

## Why this list, not the general LMIA-approved list

The federal government also publishes a list of every employer approved for
a Labour Market Impact Assessment (positive LMIA). That list is **not**
reproduced here: approval alone is not evidence of wrongdoing, and
publishing it that way would be misleading and a real defamation risk. This
site only includes employers the government's own inspection process has
already found, and formally published, as non-compliant.

## Structure

- `scripts/import_data.py` — downloads and cleans the official data.
- `data/non_compliant_employers.json` — processed output the site reads.
- `data/reason_codes.json` — numeric reason-code → plain-English text map.
- `index.html` / `about.html` — the site.
- `css/style.css`, `js/app.js` — styling and client-side search/filter/sort.

## Updating the data

```bash
python scripts/import_data.py
```

This re-downloads the government's live JSON
(`https://www.canada.ca/content/dam/ircc/documents/json/non_compliant.json`)
and regenerates `data/non_compliant_employers.json`. The government updates
that file on an ongoing basis as new compliance decisions are finalized —
there's no fixed release schedule, so re-run this periodically (e.g.
weekly) rather than expecting a daily feed.

## Running locally

```bash
python -m http.server 8420
```

Then open `http://localhost:8420/`.

## Sourcing & licensing

Data source: [Government of Canada — Employers who have been found
non-compliant](https://www.canada.ca/en/immigration-refugees-citizenship/services/work-canada/employers-non-compliant.html).
Reproduced in part under [Canada.ca's non-commercial reuse
terms](https://www.canada.ca/en/transparency/terms.html), with attribution
and a link to the original. This project is independently run and is not
affiliated with, or endorsed by, the Government of Canada.
