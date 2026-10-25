"""
websites.py
===========
Each company's website: found, checked, and its text kept as evidence.

Where a site comes from, in order: a site set by hand in config/websites.csv, a link
on a congress page, then a guess (www.<name>.fr / .com, from the company name, its
software names in the market-share data or its register name) that is kept only if the
page names the company or its software, is about health and is not a parked domain. A
guess that redirects to another site must still carry the company's own name, or its
group's, in the new address or the page title.
"""
# ---------------------------------------------------------------------------
# Abridged copy from the private repository. Shown in full except one word
# list entry (marked [omitted]); source names are replaced by generic terms.
# Every page is read through the screen's polite fetcher: robots.txt
# respected, one request per second per site, every answer cached.
# ---------------------------------------------------------------------------
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from .matching import GENERIC, key, plain

WEBSITE_MAX_CHARS = 12000    # text kept per website, for the classifier and as evidence (config.py)

PARKED = ("domain is for sale", "domaine est a vendre", "ce nom de domaine est", "buy this domain", "domain parking",
          "sedo", "site en construction", "under construction", "coming soon", "page par defaut", "default web page",
          "brandshelter", "is for sale")
# A guessed site for a publisher in the market-share data must use at least two different
# health words: vega.com (sensors for, among others, the pharmaceutical industry) or a fintech
# called FT Labs is not the health publisher of the same name. One is enough for a congress
# name: a wrong guess then reads as "not a publisher" and goes Out, instead of leaving no site.
# (The e-claims network's name is also a health word here [omitted].)
HEALTH = re.compile(r"(?<![a-z])(sante|medic|medecin|patient|soins?\b|pharmac|officine|dentaire|dentiste|"
                    r"kine|infirmi|clinique|hopita|ehpad|ordonnance|teleconsultation|orthophon|orthopt|podolog|opticien|"
                    r"audioproth|sages?-femmes?|radiolog|biolog|ambulanc|paramedical|therapeut)")
# First words too generic to be a company's domain on their own (pharma.fr, sante.fr...)
GENERIC_FIRST = {"pharma", "pharmacie", "pharmacies", "sante", "medical", "medicale", "medicaux", "centre", "centres",
                 "laboratoire", "laboratoires", "cabinet", "clinique", "dental", "dentaire", "optique", "audition",
                 "institut", "association", "federation", "union", "syndicat", "national", "nationale", "reseau",
                 "maison", "agence", "studio", "atelier", "docteur", "infirmier", "imagerie", "radiologie", "biologie",
                 "assurance", "assurances", "mutuelle", "banque", "caisse", "universite", "ecole", "academie",
                 "fondation", "hopital", "espace", "forum", "global", "digital", "smart", "health", "family", "europe",
                 "paris", "premiere", "premier", "conseils", "expert", "experts", "office", "store", "boutique",
                 "formation", "formations"}
FOLLOW = re.compile(r"logiciel|solution|produit|offre|fonctionnalit|qui-sommes|qui_sommes|a-propos|apropos|about|"
                    r"societe|entreprise|notre-histoire|professionnel", re.I)


def visible_text(html):
    soup = BeautifulSoup(html, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    meta = soup.find("meta", attrs={"name": "description"})
    desc = (meta.get("content") or "") if meta else ""
    for tag in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        tag.decompose()
    text = re.sub(r"\n{2,}", "\n", soup.get_text("\n", strip=True))
    return title, desc, text, soup


def parked(text):
    p = plain(text[:3000])
    return any(w in p for w in PARKED)


def names_company(name, title, text):
    words = [w for w in plain(key(name)).split() if len(w) >= 4] or plain(key(name)).split()
    hay = plain(title + " " + text[:5000])
    squeezed = re.sub(r"[\s\-_.]", "", hay)
    return bool(words) and all(w in hay or w in squeezed for w in words[:2])


def about_health(text, words=2):
    return len({w.rstrip("s") for w in HEALTH.findall(plain(text[:6000]))}) >= words


def same_site(guess, final):
    """A guessed address that redirects must land on a site that keeps its name, or a
    distinctive part of it: www.dentalvia-medilor.fr may land on dentalvia.fr, but
    www.medilink.fr landing on dms-imaging.com is another company's page."""
    name = urlparse(guess).netloc.lower().removeprefix("www.").rsplit(".", 1)[0]
    host = urlparse(final).netloc.lower()
    parts = [w for w in name.split("-") if len(w) >= 4 and w not in GENERIC_FIRST and w.upper() not in GENERIC]
    return name.replace("-", "") in host.replace("-", "") or any(w in host for w in parts)


def keeps_name(own, final, title):
    """A guess that redirects to another site is kept when the new address or the page title
    still carries the first distinctive word of the company's own name, or of its group's:
    intellio.fr landing on orisha.com/fr/care for ORISHA HEALTHCARE, but not medilink.fr
    landing on dms-imaging.com for INTERNATIONAL CROSS TALK. A short word must be a whole
    word, or the start of the host ('icu' in icumed.com, not in 'particulier')."""
    host = urlparse(final).netloc.lower()
    labels = re.split(r"[.\-]", host)
    words = re.findall(r"[a-z0-9]+", plain(urlparse(final).path + " " + title))
    squeezed = re.sub(r"[^a-z0-9]", "", host + " ".join(words))
    for n in own:
        first = [w for w in re.sub(r"[^a-z0-9 ]", "", plain(key(n or ""))).split() if w not in GENERIC_FIRST][:1]
        if not first or len(first[0]) < 3:
            continue
        w = first[0]
        if w in words or any(lab.startswith(w) for lab in labels) or (len(w) >= 5 and w in squeezed):
            return True
    return False


def guesses(name):
    tokens = re.sub(r"[^a-z0-9 ]", "", plain(key(name))).split()
    if not tokens or (len(tokens) == 1 and len(tokens[0]) < 4):     # too short to guess safely
        return []
    stems = ["".join(tokens), "-".join(tokens)]
    if len(tokens) > 1 and len(tokens[0]) >= 5 and tokens[0] not in GENERIC_FIRST:   # 'Adsion Info Santé' -> adsion.fr
        stems.append(tokens[0])
    return ["https://www.%s%s" % (s, tld) for s in dict.fromkeys(stems) for tld in (".fr", ".com")]


def find(fetcher, names, hints, max_guesses=10, own=(), health_words=2):
    """names: the company name first, then other names it goes by (software, register name).
    hints: [(url, method)] from config/websites.csv or congress pages.
    own: the company's own names and its group's, which a guess that redirects to another
    site must still carry (default: the company name).
    health_words: how many different health words a guessed site must use.
    Returns (url, method, page title)."""
    for url, method in hints:
        if not url:
            continue
        rec = fetcher.get(url)
        if rec and rec.get("status") == 200 and rec.get("text") and not parked(rec["text"]):
            title = visible_text(rec["text"])[0]
            return rec.get("final_url") or url, method, title
    tried = []
    for n in names:
        for url in guesses(n):
            if url in tried or len(tried) >= max_guesses:
                continue
            tried.append(url)
            rec = fetcher.get(url)
            if not (rec and rec.get("status") == 200 and rec.get("text")):
                continue
            final = rec.get("final_url") or url
            title, desc, text, _ = visible_text(rec["text"])
            if not same_site(url, final) and not keeps_name(own or names[:1], final, title):
                continue
            page = title + " " + desc + " " + text
            if (any(names_company(x, title, desc + " " + text) for x in names) and about_health(page, health_words)
                    and not parked(text)):
                how = "guessed from the name" if n == names[0] else "guessed from " + n
                return final, how, title
    return "", "", ""


def page_text(fetcher, url):
    """Home page plus up to two pages about the products or the company.
    Returns (text, pages read)."""
    rec = fetcher.get(url)
    if not rec or rec.get("status") != 200 or not rec.get("text"):
        return "", []
    title, desc, text, soup = visible_text(rec["text"])
    parts, pages = ["[%s]\n%s\n%s\n%s" % (url, title, desc, text)], [url]
    host = urlparse(rec.get("final_url") or url).netloc
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(rec.get("final_url") or url, a["href"]).split("#")[0]
        if urlparse(href).netloc == host and FOLLOW.search(href) and href not in links and href.rstrip("/") != url.rstrip("/"):
            links.append(href)
    for href in links[:2]:
        sub = fetcher.get(href)
        if sub and sub.get("status") == 200 and sub.get("text"):
            t = visible_text(sub["text"])
            parts.append("[%s]\n%s\n%s" % (href, t[0], t[2]))
            pages.append(href)
    return "\n\n".join(parts)[:WEBSITE_MAX_CHARS], pages
