// Curated, hand-researched background on larger/recognizable employers
// that show up repeatedly in the LMIA data under a legal name that
// doesn't make clear what the business is (holding companies, numbered
// companies' trade names, corporate groups). Entries are added
// deliberately, one at a time, each sourced below — this is not a bulk
// lookup or scrape (see about.html for why). Not finding a company here
// means we haven't researched it, not that there's nothing to find.
//
// Key: the employer name exactly as stored in lmia_employers.employer_key
// (i.e. normalize_key() from scripts/import_lmia.py: trimmed, collapsed
// whitespace, upper-cased). Add new entries with that same normalization.
const COMPANY_INFO = {
  "NORTHLAND PROPERTIES CORPORATION": {
    description:
      "Privately held Vancouver hospitality group founded 1963, owned by the Gaglardi family. Owns Sandman Hotels, Sutton Place Hotels, Denny's Canada, Moxies, Chop Steakhouse & Bar, Shark Club, Grouse Mountain, Revelstoke Mountain Resort, and the NHL's Dallas Stars.",
    url: "https://en.wikipedia.org/wiki/Northland_Properties",
  },
  "RECIPE UNLIMITED CORPORATION": {
    description:
      "Canada's largest full-service restaurant company (formerly Cara Operations), owned by Fairfax Financial since 2022. Operates Swiss Chalet, Harvey's, St-Hubert, Montana's, Kelseys, East Side Mario's, The Keg, New York Fries, and others.",
    url: "https://en.wikipedia.org/wiki/Recipe_Unlimited",
  },
  "ORIGINAL JOE'S FRANCHISE GROUP INC.": {
    description:
      "Calgary-based casual restaurant and bar franchisor (Original Joe's, State & Main, Elephant & Castle), majority-owned by Recipe Unlimited since 2016, concentrated in Western Canada.",
    url: "https://www.originaljoes.ca/en/franchising.html",
  },
  "CLUBLINK CORPORATION ULC": {
    description:
      "Canada's largest golf course operator, with 40+ member, daily-fee and resort courses concentrated in Ontario and Quebec. Based in King City, ON; subsidiary of TSX-listed TWC Enterprises.",
    url: "https://en.wikipedia.org/wiki/Clublink",
  },
};

function companyInfoFor(employerKey) {
  if (!employerKey) return null;
  const key = employerKey.trim().replace(/\s+/g, " ").toUpperCase();
  return COMPANY_INFO[key] || null;
}

// Provincial/territorial numbered-company suffixes mapped to their free
// official corporate-registry search tool. "Canada" gets the federal
// search; anything else we don't recognize falls back to a plain web
// search so the link still works without us guessing at (and possibly
// breaking) every jurisdiction's URL.
const REGISTRY_LINKS = {
  CANADA: "https://ised-isde.canada.ca/cc/lgcy/fdrlCrpSrch.html?locale=en_CA",
  ONTARIO: "https://www.ontario.ca/page/ontario-business-registry",
  "BRITISH COLUMBIA": "https://www.bcregistry.gov.bc.ca/",
  BC: "https://www.bcregistry.gov.bc.ca/",
  MANITOBA: "https://companiesoffice.gov.mb.ca/",
  "NOVA SCOTIA": "https://rjsc.novascotia.ca/",
};
const NUMBERED_COMPANY_RE = /^([\d-]{6,10})\s+([A-Za-zÀ-ÿ.& ]+?)\s+(Inc\.?|Ltd\.?|Limited|Corp\.?|Corporation)\b/i;

function registryLinkFor(employer) {
  const m = employer.match(NUMBERED_COMPANY_RE);
  if (!m) return null;
  const [, number, jurisdiction] = m;
  const key = jurisdiction.trim().toUpperCase();
  const url = REGISTRY_LINKS[key];
  if (url) {
    return { url, label: `Look up #${number} on the official ${jurisdiction.trim()} registry →` };
  }
  const query = encodeURIComponent(`"${number}" "${jurisdiction.trim()}" corporate registry`);
  return {
    url: `https://www.google.com/search?q=${query}`,
    label: `Search for #${number} (${jurisdiction.trim()}) on a public registry →`,
  };
}
