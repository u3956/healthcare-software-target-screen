"""
matching.py
===========
One record per company, whatever the spelling, matched to the right company in the
French register.

1. Names     one comparable form per name (legal forms and common words dropped) and a
             similarity score between two names.
2. Merge     one record per company across the sources; a congress or PDF name that is the
             name of one publisher's software joins that publisher.
3. Register  each name is searched in the free government company register. Candidates are
             scored; a weak match or a tie goes to review with the runner-up. A SIREN checked
             by hand in the company's legal notice replaces the search.
4. Same SIREN  two names matched with confidence to the same SIREN become one row.
"""
# ---------------------------------------------------------------------------
# Abridged copy from the private repository, gathered from three modules
# (normalize.py, pipeline.py and sources/register.py). The matching logic is
# shown in full; the register request and the bookkeeping of each source's rows
# are marked [omitted], and source names are replaced by generic terms. Fields
# starting with tt_ come from the teletransmission market-share data.
# ---------------------------------------------------------------------------
import re
import unicodedata
from collections import Counter, defaultdict
from difflib import SequenceMatcher


class config:  # excerpt of config.py
    MERGE_SIMILARITY = 0.92      # two spellings above this are the same company
    REGISTER_MIN_SCORE = 0.82    # a register match below this goes to review
    REGISTER_TIE_GAP = 0.03      # two register candidates this close = ambiguous
    SURE_MATCHES = ("matched", "set by hand")
    # activity codes (NAF) of software publishers and IT services
    SOFTWARE_NAF = {"58.21Z", "58.29A", "58.29B", "58.29C", "62.01Z", "62.02A", "62.02B",
                    "62.03Z", "62.09Z", "63.11Z", "63.12Z"}


# ================================================================== 1. names
LEGAL_FORMS = {"SA", "SAS", "SASU", "SARL", "EURL", "SNC", "SCOP", "SCS", "SCA", "SE", "GIE", "SELARL",
               "SC", "SCI", "LTD", "LIMITED", "GMBH", "AG", "INC", "LLC", "BV", "NV", "SPA", "SRL", "SL", "PLC"}
# Words that many publishers share; dropped for matching unless nothing else is left.
GENERIC = {"FRANCE", "GROUPE", "GROUP", "SOCIETE", "STE", "LES", "LA", "LE", "L", "DE", "DU", "DES", "D", "ET",
           "THE", "AND", "SOLUTIONS", "SOLUTION", "SYSTEMES", "SYSTEMS", "INFORMATIQUE", "LOGICIELS", "LOGICIEL",
           "EDITIONS", "EDITION", "DEVELOPPEMENT", "TECHNOLOGIES", "TECHNOLOGY", "SOFTWARE", "SAS", "HOLDING",
           "INTERNATIONAL", "SERVICES", "SERVICE", "CONSEIL"}


def strip_accents(text):
    text = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in text if not unicodedata.combining(c))


def plain(text):
    """Lower case, no accents, single spaces: the form used for keyword searches."""
    t = strip_accents(text).lower().replace("’", "'")
    return re.sub(r"\s+", " ", t).strip()


def clean(name):
    t = strip_accents(name or "").upper().replace("&", " ET ").replace("'", " ").replace("’", " ")
    t = re.sub(r"[^A-Z0-9]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def key(name):
    """'CompuGroup Medical Solutions SAS' and 'COMPUGROUP MEDICAL' give the same key."""
    tokens = [t for t in clean(name).split() if t not in LEGAL_FORMS]
    core = [t for t in tokens if t not in GENERIC]
    return " ".join(core or tokens)


def full_key(name):
    """Like key(), but keeps the generic words: 'COMPUGROUP MEDICAL FRANCE' and
    'COMPUGROUP MEDICAL SOLUTIONS' differ, 'DOCTOLIB SAS' and 'DOCTOLIB' do not."""
    return " ".join(t for t in clean(name).split() if t not in LEGAL_FORMS)


def similarity(a, b):
    """0 to 1. 1 = same key; 0.9 = one name contains the other (if long enough)."""
    ka, kb = key(a), key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    ta, tb = set(ka.split()), set(kb.split())
    seq = SequenceMatcher(None, ka, kb).ratio()
    compact = SequenceMatcher(None, ka.replace(" ", ""), kb.replace(" ", "")).ratio()
    tok = len(ta & tb) / len(ta | tb)
    shorter = ka if len(ka) <= len(kb) else kb
    contains = 0.9 if (ta <= tb or tb <= ta) and (len(shorter) >= 5 or len(shorter.split()) >= 2) else 0.0
    return round(max(seq, compact, tok, contains), 3)


# ================================================================== 2. merge
def _software_name(name):
    """A software name without its version number ('LGPI V3.2' -> 'LGPI')."""
    return re.sub(r"\b[vV]?\d+(?:[.,]\d+)*\b", " ", name or "").strip()


def merge(tt_rows, ex_rows, pdf_rows=()):
    """One record per company, whatever the spelling in each source. A congress or PDF name
    that is the name of one publisher's software in the teletransmission data joins that
    publisher: the exhibitor showed the product, the publisher is the company."""
    from .websites import GENERIC_FIRST     # first words too generic to be a company's own name
    by_key, index = {}, defaultdict(set)

    def find_or_add(name):
        k = key(name)
        if not k:
            return None
        if k in by_key:
            return by_key[k]
        near = sorted({other for t in k.split() for other in index[t]})
        best = max(near, key=lambda o: similarity(k, o), default=None)
        if best and similarity(k, best) >= config.MERGE_SIMILARITY:
            return by_key[best]
        rec = {"key": k, "names": Counter(), "tt_listed": False, "tt_rows": [], "congresses": [],
               "congress_professions": [], "pdf_files": [], "pdf_professions": [], "website_hints": [],
               "seen_as_software": []}
        by_key[k] = rec
        for t in k.split():
            index[t].add(k)
        return rec

    software = defaultdict(list)            # software name -> the publishers selling it
    for r in tt_rows:
        rec = find_or_add(r["editor"])
        if rec is None:
            continue
        s = key(_software_name(r["software"]))
        if len(s.replace(" ", "")) >= 4 and s.lower() not in GENERIC_FIRST and all(x is not rec for x in software[s]):
            software[s].append(rec)
        rec["names"][r["editor"]] += 5          # the publisher's name in the market-share data wins
        rec["tt_listed"] = True
        ...  # [omitted] keeps the row: profession, software, parent group, users and share

    def join(name):
        k = key(name)
        owners = software.get(k, []) if k not in by_key else []
        if len(owners) == 1:
            if name not in owners[0]["seen_as_software"]:
                owners[0]["seen_as_software"].append(name)
            return owners[0]
        return find_or_add(name)

    def seen(rec, name, fields, where, profession, website, hint):
        """fields: where the name was seen (congresses or pdf_files) and its professions."""
        rec["names"][name] += 1
        for field, value in zip(fields, (where, profession)):
            if value and value not in rec[field]:
                rec[field].append(value)
        if website and website not in [h[0] for h in rec["website_hints"]]:
            rec["website_hints"].append([website, hint])

    for r in ex_rows:
        rec = join(r["exhibitor"])
        if rec is not None:
            seen(rec, r["exhibitor"], ("congresses", "congress_professions"), r["congress"], r["profession_std"],
                 r.get("website"), "link on " + r["congress"])
    for r in pdf_rows:
        rec = join(r["name"])
        if rec is not None:
            seen(rec, r["name"], ("pdf_files", "pdf_professions"), r["file"], r.get("profession_std"), r.get("website"),
                 "in " + r["file"])
    out = []
    for rec in by_key.values():
        rec["name"] = rec["names"].most_common(1)[0][0]
        rec["names"] = sorted(rec["names"])
        ...  # [omitted] one line per market-share table (the publisher's products added up), its share,
        #      the parent group named in the data, and names that are not companies (associations, public bodies)
        out.append(rec)
    out.sort(key=lambda r: (not r["tt_listed"], -(r.get("tt_total_ps") or 0), -len(r["congresses"]), r["name"]))
    return out


# ================================================================== 3. register
# Words too common in software names to tell two companies apart
COMMON = {"SANTE", "MEDICAL", "MEDICALE", "PRO", "PLUS", "CABINET", "WEB", "ONLINE", "CLOUD", "MOBILE", "NET", "SOFT"}


def _not_a_business(result):
    """Association, foundation, public body, union or mutual, from the legal form
    ('nature juridique': 7xxx public law, 8xxx social security, mutuals, unions and
    professional orders, 92xx associations, 93xx foundations) or the register flags."""
    form = str(result.get("nature_juridique") or "")
    flags = result.get("complements") or {}
    if flags.get("est_association") or form.startswith("92"):
        return "association"
    if flags.get("est_administration") or flags.get("collectivite_territoriale") or form.startswith("7"):
        return "public body"
    if form.startswith("8"):
        return "union, mutual or professional order"
    if form.startswith("93"):
        return "foundation"
    return ""


def parse(result):
    siege = result.get("siege") or {}
    finances = result.get("finances") or {}
    year = max(finances) if finances else ""
    revenue = (finances.get(year) or {}).get("ca") if year else None
    net_income = (finances.get(year) or {}).get("resultat_net") if year else None
    officers = result.get("dirigeants") or []
    corporate = [o.get("denomination") for o in officers
                 if "morale" in (o.get("type_dirigeant") or "").lower() and o.get("denomination")
                 and "commissaire" not in (o.get("qualite") or "").lower()]       # auditors are not owners
    return {
        "siren": result.get("siren") or "",
        "name": result.get("nom_complet") or result.get("nom_raison_sociale") or "",
        "legal_name": result.get("nom_raison_sociale") or "",
        "acronym": result.get("sigle") or "",
        "naf": result.get("activite_principale") or "",
        "band": result.get("tranche_effectif_salarie") or "",
        "band_year": result.get("annee_tranche_effectif_salarie") or "",
        "category": result.get("categorie_entreprise") or "",
        "created": (result.get("date_creation") or "")[:10],
        "active": (result.get("etat_administratif") or "A") == "A",
        "city": siege.get("libelle_commune") or "",
        "postcode": siege.get("code_postal") or "",
        "revenue": revenue,
        "net_income": net_income,
        "revenue_year": year,
        "legal_form": str(result.get("nature_juridique") or ""),
        "not_a_business": _not_a_business(result),
        "corporate_officers": "; ".join(corporate),
    }


def score(name, cand, software_names=()):
    """Name similarity (0 to 1) plus small bonuses that break ties between companies of the
    same name: a software activity (+0.08), one of its software names in the register name
    (+0.05, for publishers in the market-share data) and employees (+0.02). A closed company
    loses 0.3."""
    sim = max(similarity(name, cand["name"]), similarity(name, cand["legal_name"]),
              similarity(name, cand["acronym"]) if cand["acronym"] else 0.0)
    bonus = 0.08 if cand["naf"] in config.SOFTWARE_NAF else 0.0
    own = set(key(name).split())
    words = set(key(cand["name"] + " " + cand["acronym"]).split()) - own - COMMON
    if any({w for w in key(s).split() if len(w) >= 3} & words for s in software_names):
        bonus += 0.05
    if cand["band"] not in ("", "NN", "00"):
        bonus += 0.02
    if not cand["active"]:
        bonus -= 0.3
    return round(sim + bonus, 3), sim


def _holds(cand, name):
    """The candidate's name holds every word of the name, and more."""
    words = set(key(name).split())
    return any(words <= set(key(n).split()) for n in (cand["name"], cand["legal_name"], cand["acronym"]) if n)


def _search(fetcher, params):
    ...  # [omitted] one cached request to the register API; returns (results or None, error)


def _candidates(fetcher, name, software_names, naf=None):
    """Register companies for a name, best first. Returns (candidates or None, error)."""
    params = {"q": name}
    if naf:
        params["activite_principale"] = ",".join(sorted(naf))
    results, err = _search(fetcher, params)
    if results is None:
        return None, err
    cands = []
    for res in results:
        c = parse(res)
        c["score"], c["name_similarity"] = score(name, c, software_names)
        cands.append(c)
    return sorted(cands, key=lambda c: c["score"], reverse=True), ""


def lookup(fetcher, name, software_names=()):
    """Best register match for a name. Returns a dict with status matched / ambiguous /
    weak / none / error, the best candidate's fields, and the runner-up for review.
    For a publisher in the market-share data (software_names given), software companies are
    searched first, so that a namesake in road freight or catering is not taken for it.
    When a closed company has exactly the name and the best match only a near spelling
    (ARAKIS closed, ARAKIX open), the match is ambiguous: the business may have moved. A best
    match that holds the name and more words (RECKITT BENCKISER FRANCE for RECKITT) is not a
    near spelling."""
    if len(full_key(name).replace(" ", "")) < 3:
        return {"status": "none", "note": "name too short to search"}
    cands, err = None, ""
    if software_names:
        cands, err = _candidates(fetcher, name, software_names, config.SOFTWARE_NAF)
        if cands is not None and (not cands or cands[0]["score"] < config.REGISTER_MIN_SCORE):
            cands = None        # no convincing software company: search all companies
    if cands is None:
        cands, err = _candidates(fetcher, name, software_names)
    if cands is None:
        return {"status": "not in cache" if err == "not in cache" else "error", "note": err}
    if not cands:
        return {"status": "none"}
    best = dict(cands[0])
    second = cands[1] if len(cands) > 1 else None
    closed_namesake = next((c for c in cands if not c["active"] and c["name_similarity"] >= 0.99
                            and c["siren"] != best["siren"]), None)
    if best["score"] < config.REGISTER_MIN_SCORE:
        best["status"] = "weak"
    elif closed_namesake and best["name_similarity"] < 0.99 and not _holds(best, name):
        best["status"], second = "ambiguous", closed_namesake
        best["note"] = "a closed company has exactly this name"
    elif second and second["siren"] != best["siren"] and best["score"] - second["score"] < config.REGISTER_TIE_GAP:
        best["status"] = "ambiguous"
    else:
        best["status"] = "matched"
    best["runner_up"] = "%s (%s, %.2f)" % (second["name"], second["siren"], second["score"]) if second else ""
    return best


def by_siren(fetcher, siren, note=""):
    """The company of a SIREN set by hand (config/sirens.csv), for a name the search matches to
    another company or to none. Status 'set by hand', with the note given; it may be closed
    (a business merged into another keeps its own SIREN here)."""
    siren = siren.replace(" ", "")
    results, err = _search(fetcher, {"q": siren})
    if results is None:
        return {"status": "not in cache" if err == "not in cache" else "error", "note": err}
    found = next((r for r in results if r.get("siren") == siren), None)
    if not found:
        return {"status": "none", "note": "SIREN %s set by hand is not in the register" % siren}
    return dict(parse(found), status="set by hand", note=note)


# ================================================================== 4. same SIREN
def merge_same_siren(companies):
    """Two names, one company: confident register matches to the same SIREN are merged into
    the record the market-share data lists (else the one seen at most congresses). Returns how many."""
    by_siren = defaultdict(list)
    for c in companies:
        c["duplicate_of"] = ""
        if c.get("register_status") in config.SURE_MATCHES and c.get("siren"):
            by_siren[c["siren"]].append(c)
    merged = 0
    for same in by_siren.values():
        keep = max(same, key=lambda c: (c["tt_listed"], c.get("tt_total_ps") or 0, len(c["congresses"])))
        for c in same:
            if c is keep or c["tt_listed"]:
                continue
            c["duplicate_of"] = keep["name"]
            keep["names"] = sorted(set(keep["names"]) | set(c["names"]))
            for f in ("congresses", "congress_professions", "pdf_files", "pdf_professions"):
                keep.setdefault(f, [])
                keep[f] += [x for x in c.get(f, []) if x not in keep[f]]
            keep["website_hints"] += [h for h in c["website_hints"] if h[0] not in [k[0] for k in keep["website_hints"]]]
            merged += 1
    return merged
