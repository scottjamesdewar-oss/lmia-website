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
