"""
tiering.py
==========
Tiers: plain rules, written out so that every tier comes with its reason.

Tier 1   publisher with a real position in at least one profession (500+ users, or 2%+ of
         a teletransmission table with 100+ users in it), a confident register match, 10 to
         249 staff (or €2-50m revenue), no group named in the teletransmission data and no
         company among its officers (so probably founder-owned).
Tier 2   a real position but part of a group or run by a holding, of unknown size, or with a
         register match that is not sure; or a smaller position (100+ users) or seen at two
         congresses or more.
Tier 3   publisher with a small or unknown position.
Large    250+ staff, or a mid-sized or large company in the official category (often a small
         company inside a larger group): a buyer or a competitor rather than a target.
Out      not a company, not a software publisher, seen at a congress or in an uploaded PDF
         only with no website and no match in the French register, or the same company as
         another row (same SIREN).
Review   a person has to look (unclear website, weak register match, quote not found).
Not checked  collected but not yet matched to the register (a run with --limit).
"""
# ---------------------------------------------------------------------------
# Abridged copy from the private repository. This module is shown in full; the
# thresholds come from config.py (excerpt below) and source names are replaced
# by generic terms. Fields starting with tt_ come from the teletransmission
# market-share data: users and share of each publisher, by profession.
# ---------------------------------------------------------------------------


class config:  # excerpt of config.py
    TIER1_MIN_PS = 500           # professionals using the software...
    TIER1_MIN_SHARE = 0.02       # ...or at least 2% of one profession,
    TIER1_MIN_SHARE_PS = 100     # with 100 users or more in it (2% of a table of 300 is 6 users)
    TIER2_MIN_PS = 100
    TIER2_MIN_CONGRESSES = 2     # or seen at two congresses or more
    SURE_MATCHES = ("matched", "set by hand")    # 'set by hand': a SIREN checked in a legal notice
    BANDS = {"NN": "no employees", "00": "0", "01": "1-2", "02": "3-5", "03": "6-9", "11": "10-19",
             "12": "20-49", "21": "50-99", "22": "100-199", "31": "200-249", "32": "250-499",
             "41": "500-999", "42": "1,000-1,999", "51": "2,000-4,999", "52": "5,000-9,999", "53": "10,000+"}
    SME_BANDS = {"11", "12", "21", "22", "31"}            # 10 to 249 employees
    LARGE_BANDS = {"32", "41", "42", "51", "52", "53"}    # 250 and more
    LARGE_CATEGORIES = {"ETI", "GE"}                     # official company category: mid-sized, large


ORDER = ["Tier 1", "Tier 2", "Tier 3", "Review", "Large", "Not checked", "Out"]
UNSURE = {"ambiguous": "the register match is a tie", "weak": "the register match is weak",
          "none": "it was not found in the register"}


def _seen(c, congress="at a congress"):
    """Where a name that the teletransmission data does not list was seen."""
    where = ([congress] if c.get("congresses") else []) + (["in an uploaded PDF"] if c.get("pdf_files") else [])
    return " and ".join(where) or congress


def tier(c):
    """c: one company (dict). Returns (tier, reason, review)."""
    if c.get("not_a_company"):
        return "Out", "not a company (name contains '%s')" % c["not_a_company"], ""
    if "register_status" not in c:
        return "Not checked", "not checked yet (run: python screen.py enrich)", ""
    if c.get("duplicate_of"):
        return "Out", "same company as %s (same SIREN)" % c["duplicate_of"], ""
    review = []
    if c.get("register_status") in ("weak", "ambiguous", "none", "error"):
        review.append("register match %s%s" % (c["register_status"], " (%s)" % c["register_note"] if c.get("register_note") else ""))
    if c.get("review"):
        review.append(c["review"])
    if c.get("register_check"):
        review.append(c["register_check"])
    listed = c.get("tt_listed")
    decision = c.get("decision") or ""
    if not listed and c.get("register_out"):
        return "Out", c["register_out"], ""
    if not listed and not c.get("website") and c.get("register_status") in ("none", "weak", "error"):
        return "Out", "seen %s only, with no website found and no match in the French register" % _seen(c), ""
    if not listed and decision != "oui":
        if decision == "non":
            return "Out", "its website does not read as a software publisher", ""
        return "Review", "could not tell from the website", "; ".join(review) or "check the website"
    if listed and decision == "non":
        review.append("listed in the teletransmission data but the website reads otherwise")
    band, category = c.get("band") or "", c.get("category") or ""
    if band in config.LARGE_BANDS:
        return "Large", "too large to buy (%s staff%s): a buyer or a competitor" % (
            config.BANDS[band], ", " + category if category else ""), "; ".join(review)
    if category in config.LARGE_CATEGORIES:
        size = "; this company: %s staff" % config.BANDS[band] if band in config.BANDS else ""
        return "Large", "part of a larger group (official category %s%s): a buyer or a competitor" % (category, size), "; ".join(review)
    ps = c.get("tt_max_ps") or 0
    revenue = c.get("revenue") or 0
    tables = c.get("tt_tables") or []
    # a real position: 500+ users in a table, or 2%+ of a table with 100+ users in it
    strong = next((t for t in tables if (t["n_ps"] or 0) >= config.TIER1_MIN_PS or
                   ((t["share"] or 0) >= config.TIER1_MIN_SHARE and (t["n_ps"] or 0) >= config.TIER1_MIN_SHARE_PS)), None)
    sme = band in config.SME_BANDS or 2e6 <= revenue <= 50e6
    owner = "; ".join(x for x in ("group %s (teletransmission data)" % c["tt_group"] if c.get("tt_group") else "",
                                   c.get("corporate_officers") or "") if x)
    status = c.get("register_status")

    def where(t):
        if not t:
            return "users not published"
        return "%s (%s%s)" % (t["profession"], "{:,} users".format(t["n_ps"]) if t["n_ps"] else "users not published",
                              ", %.1f%% share" % (100 * t["share"]) if t["share"] else "")

    if strong and sme and not owner and status in config.SURE_MATCHES:
        size = config.BANDS.get(band, "") + " staff" if band in config.SME_BANDS else "revenue %.1fm EUR" % (revenue / 1e6)
        return "Tier 1", "real position in %s; %s; no company among the officers" % (where(strong), size), "; ".join(review)
    if strong:
        if status not in config.SURE_MATCHES and not c.get("tt_group"):
            unsure = UNSURE.get(status, "the register search failed")
            if status == "ambiguous" and c.get("register_note"):
                unsure = "the register match is not sure (%s)" % c["register_note"]
            why = "%s, so its size and officers are not sure" % unsure
        elif owner:
            why = "part of a group or run by a company (%s)" % owner
        else:
            why = "size unknown or under 10 staff"
        return "Tier 2", "real position in %s, but %s" % (where(strong), why), "; ".join(review)
    biggest = max(tables, key=lambda t: t["n_ps"] or 0) if tables else None
    if ps >= config.TIER2_MIN_PS:
        return "Tier 2", "smaller position: %s" % where(biggest), "; ".join(review)
    if len(c.get("congresses") or []) >= config.TIER2_MIN_CONGRESSES:
        return "Tier 2", "seen at %d congresses" % len(c["congresses"]), "; ".join(review)
    if listed:
        return "Tier 3", "small position: %s" % where(biggest), "; ".join(review)
    return "Tier 3", "publisher seen %s, not in the teletransmission data" % _seen(c, "at one congress"), "; ".join(review)
