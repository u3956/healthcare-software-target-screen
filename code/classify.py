"""
classify.py
===========
Is the company a health-software publisher, and for which professions?

Two passes:
1. Rules count software words and profession words on the website and keep the best
   sentence as evidence. Clear cases stop here. A page with no French software word is
   never ruled out (it is often in English): Codex reads it.
2. Codex reads the unclear ones (or all of them, with --llm-all): the Codex CLI with the
   ChatGPT login, locked down (no tools, no web, an answer that must follow a JSON schema).
   It must copy one sentence from the page. The script then checks that the sentence
   really is on the page; when it is not, the answer goes to review.

Answers are kept in data/llm_answers.jsonl, so a re-run never asks Codex the same thing
twice.

describe() asks Codex, the same way, for one sentence on what a company in the PDF sells
and to whom, with the sentence of the page it rests on; the description is kept only when
that sentence is on the page. Those answers are kept in data/descriptions.jsonl.
"""
# ---------------------------------------------------------------------------
# Abridged copy from the private repository. Word lists come from config.py
# (excerpt below). Source names are replaced by generic terms, and the parts
# that depend on one machine are marked [omitted]. The remaining code is
# unchanged.
# ---------------------------------------------------------------------------
import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from .matching import plain          # lower case, no accents, single spaces


class config:  # excerpt of config.py
    DATA = Path("data")
    # Words that show a website is about software... (the e-claims network's own name and
    # its approval label are also in the list [omitted])
    SOFTWARE_TERMS = ["logiciel", "logiciels", "editeur", "teletransmission", "feuille de soins electronique",
                      "feuilles de soins electroniques", "fse", "dossier patient", "dossier medical", "dossier de soins",
                      "agenda en ligne", "prise de rendez-vous", "facturation", "tiers payant", "gestion de cabinet",
                      "gestion du cabinet", "saas", "application mobile", "solution logicielle", "progiciel", "dmp",
                      "ordonnance numerique", "e-prescription", "homologue", "logiciel metier", "interface", "cloud"]
    # ...and words that show it is something else
    OTHER_TERMS = ["distributeur", "fabricant", "materiel medical", "mobilier", "dispositif medical",
                   "dispositifs medicaux", "laboratoire pharmaceutique", "complement alimentaire",
                   "complements alimentaires", "mutuelle", "assurance", "banque", "credit", "organisme de formation",
                   "formation continue", "syndicat", "federation", "ordre des", "magazine", "revue", "recrutement",
                   "interim", "cosmetique", "semelles", "orthese", "fauteuil", "instruments", "consommables", "implant"]
    # One name per profession, with keywords matched at the start of a word (3 of the 21 shown)
    PROFESSIONS = {
        "Infirmiers": ["infirmier", "infirmiere", "idel"],
        "Médecins": ["medecin", "medecins", "medecine generale", "cabinet medical", "generaliste", "specialiste"],
        "Pharmacies": ["pharmacie", "pharmacies", "pharmacien", "officine", "officinal"],
        # [omitted] the other 18 professions
    }


PROMPT_VERSION = "v1"
CODEX_MODEL = "gpt-6-luna"
CODEX_EFFORT = os.environ.get("CODEX_EFFORT", "medium")
CODEX_TIMEOUT = 5 * 60
# The model reads text from websites, so it gets no tool at all: not the Codex
# desktop config, not the ChatGPT account's connectors, no shell, browser or sub-agents. The
# ChatGPT login is still used.
CODEX_LOCKDOWN = ["--ignore-user-config", "-c", 'web_search="disabled"'] + [
    arg for feature in ("apps", "plugins", "remote_plugin", "browser_use", "browser_use_external", "computer_use",
                        "in_app_browser", "multi_agent", "image_generation", "skill_search", "tool_suggest",
                        "shell_tool", "unified_exec")
    for arg in ("--disable", feature)]
LIMIT = re.compile(r"usage.?limit|rate.?limit|too many requests|\b429\b|quota|insufficient", re.I)
TYPES = ["gestion de cabinet", "télétransmission", "dossier patient", "agenda et téléconsultation", "imagerie",
         "laboratoire", "pharmacie", "établissement", "autre logiciel", "pas un logiciel"]

SYSTEM = ("Tu es analyste dans un fonds d'investissement. Tu classes des sociétés à partir du texte de leur "
          "site web, sans rien inventer. Réponds uniquement en JSON.")
USER = """Société : {name}
Professions possibles : {professions}

Texte du site (extraits) :
\"\"\"{text}\"\"\"

Question : cette société édite-t-elle elle-même un logiciel pour les professionnels de santé (gestion de cabinet ou d'officine, facturation et télétransmission des feuilles de soins, dossier patient, agenda, imagerie, laboratoire, établissement) ?

Réponds en JSON avec exactement ces clés :
{{"editeur_logiciel_sante": "oui" ou "non" ou "incertain",
  "type": une valeur parmi {types},
  "professions": [professions prises dans la liste ci-dessus],
  "citation": "une phrase copiée mot pour mot du texte, qui justifie la réponse",
  "raison": "une phrase"}}
Un distributeur, un fabricant de matériel ou un cabinet de conseil n'est pas un éditeur. Si le texte ne permet pas de conclure, réponds "incertain"."""

DESCRIBE_VERSION = "d1"
DESCRIBE = """Société : {name}

Texte du site (extraits) :
\"\"\"{text}\"\"\"

Question : d'après ce texte seulement, que vend cette société et à quels professionnels de santé ?

Réponds en JSON avec exactement ces clés :
{{"description": "une phrase en anglais de 25 mots au plus : le produit et ses clients, sans chiffre, sans superlatif, sans nom absent du texte ; vide si le texte ne le dit pas",
  "citation": "une phrase copiée mot pour mot du texte, sur laquelle repose la description"}}"""

# Words that settle the question alone (the e-claims network's own name is also in the list [omitted])
STRONG_TERMS = ("teletransmission", "feuille de soins electronique", "feuilles de soins electroniques")


def _count(text, term):
    return len(re.findall(r"(?<![a-z0-9])" + re.escape(term), text))


def sentences(text):
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [s.strip() for s in parts if 25 <= len(s.strip()) <= 320]


def professions_in(text_plain, threshold=2):
    found = []
    for prof, words in config.PROFESSIONS.items():
        if sum(_count(text_plain, w) for w in words) >= threshold:
            found.append(prof)
    return found


def guess_type(p):
    if any(t in p for t in STRONG_TERMS):
        return "télétransmission"
    for words, kind in ((("dossier patient", "dossier medical", "dossier de soins"), "dossier patient"),
                        (("agenda", "rendez-vous", "teleconsultation"), "agenda et téléconsultation"),
                        (("pacs", "imagerie"), "imagerie"), (("officine", "pharmacie"), "pharmacie"),
                        (("gestion de cabinet", "gestion du cabinet"), "gestion de cabinet")):
        if any(w in p for w in words):
            return kind
    return "autre logiciel"


def rules(text):
    """Returns decision (oui / non / incertain), type, professions, quote, and the counts."""
    p = plain(text)
    soft = sum(_count(p, t) for t in config.SOFTWARE_TERMS)
    other = sum(_count(p, t) for t in config.OTHER_TERMS)
    strong = any(t in p for t in STRONG_TERMS)
    best, best_score = "", 0
    for s in sentences(text):
        ps = plain(s)
        sc = 2 * sum(_count(ps, t) for t in config.SOFTWARE_TERMS)
        if sc:
            sc += sum(1 for words in config.PROFESSIONS.values() if any(_count(ps, w) for w in words))
        if sc > best_score:
            best, best_score = s, sc
    profs = professions_in(p)
    if best:
        profs = sorted(set(profs) | set(professions_in(plain(best), threshold=1)))
    if strong or (soft >= 6 and profs and best):
        decision = "oui"
    elif soft == 1 and other >= 2:
        decision = "non"
    else:       # among them, pages with no French software word (often in English): Codex reads them
        decision = "incertain"
    return {"decision": decision, "type": guess_type(p) if decision == "oui" else ("pas un logiciel" if decision == "non" else ""),
            "professions": profs, "quote": best, "software_words": soft, "other_words": other}


def quote_found(quote, text):
    q = re.sub(r"\W+", " ", plain(quote)).strip()
    t = re.sub(r"\W+", " ", plain(text))
    return len(q) >= 20 and q in t


def _json_in(content):
    m = re.search(r"\{.*\}", content or "", re.S)
    return json.loads(m.group() if m else content)


# The answer Codex must give (its --output-schema): the keys the prompt asks for, nothing else
ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["editeur_logiciel_sante", "type", "professions", "citation", "raison"],
    "properties": {
        "editeur_logiciel_sante": {"type": "string", "enum": ["oui", "non", "incertain"]},
        "type": {"type": "string", "enum": TYPES},
        "professions": {"type": "array", "items": {"type": "string", "enum": list(config.PROFESSIONS)}},
        "citation": {"type": "string"},
        "raison": {"type": "string"},
    },
}
DESCRIPTION_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["description", "citation"],
    "properties": {"description": {"type": "string"}, "citation": {"type": "string"}},
}


class CodexLimit(Exception):
    """Codex refused: the ChatGPT usage limit is reached."""


def codex_exe():
    ...  # [omitted] finds the Codex CLI installed with npm, else the one on PATH


def codex_run(prompt, model, effort=CODEX_EFFORT, timeout=CODEX_TIMEOUT, answer_schema=ANSWER_SCHEMA):
    """One locked-down `codex exec` call, prompt on stdin, in an empty folder; returns its last message."""
    with tempfile.TemporaryDirectory(prefix="screen-codex-") as work:
        schema, out = Path(work) / "schema.json", Path(work) / "answer.json"
        schema.write_text(json.dumps(answer_schema, ensure_ascii=False), encoding="utf-8")
        cmd = [codex_exe(), "exec", *CODEX_LOCKDOWN, "--model", model, "-c", "model_reasoning_effort=%s" % effort,
               "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "--color", "never",
               "--output-schema", str(schema), "--output-last-message", str(out), "-"]
        if cmd[0].lower().endswith((".cmd", ".bat")):
            cmd = ["cmd", "/c"] + cmd
        r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=work, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0 or not out.exists():
            tail = ((r.stderr or "") + "\n" + (r.stdout or "")).strip()
            if LIMIT.search(tail[-3000:]):
                raise CodexLimit(tail[-300:])
            raise RuntimeError("codex exit %s: %s" % (r.returncode, tail[-300:]))
        return out.read_text(encoding="utf-8")


# ------------------------------------------------------------------ the model
class Model:
    """'codex' (gpt-6-luna), 'codex:<model>' (e.g. codex:gpt-6-sol), or 'none' for the rules only.
    prompt, schema and version: the question (by default the classification) and its cache key."""

    def __init__(self, spec="none", cache_path=None, prompt=USER, schema=ANSWER_SCHEMA, version=PROMPT_VERSION):
        self.provider, self.model = "none", ""
        spec = (spec or "none").strip()
        if spec != "none":
            provider, _, model = spec.partition(":")
            if provider != "codex":
                raise ValueError("unknown model %r: use codex, codex:<model> or none" % spec)
            self.provider, self.model = "codex", model or CODEX_MODEL
        self.prompt, self.schema, self.version = prompt, schema, version
        self.cache_path = cache_path or (config.DATA / "llm_answers.jsonl")
        self.cache = {}                     # cache key -> {provider:model -> answer}
        if self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.cache.setdefault(rec["cache_key"], {})["%s:%s" % (rec["provider"], rec["model"])] = rec
        self.stopped = ""                   # why no more calls are made (the Codex usage limit)
        self._lock = threading.Lock()

    @property
    def label(self):
        return "%s:%s" % (self.provider, self.model) if self.provider != "none" else "none"

    def cache_key(self, name, text):
        return hashlib.sha1(("%s|%s|%s" % (self.version, name, text)).encode("utf-8")).hexdigest()

    def cached(self, name, text):
        """The answer this Codex model gave to this text; with 'none', any Codex answer kept."""
        answers = self.cache.get(self.cache_key(name, text), {})
        if self.provider == "none":
            return next((a for label, a in answers.items() if label.startswith("codex:")), None)
        return answers.get(self.label)

    def _call(self, system, user):
        return codex_run(system + "\n\n" + user, self.model, answer_schema=self.schema)

    def ask(self, name, text):
        """Returns the cached or new answer (dict), or None when no model is set or the call fails."""
        hit = self.cached(name, text)
        if hit:
            return hit
        if self.provider == "none":
            return None
        if self.stopped:
            return {"error": self.stopped}
        user = self.prompt.format(name=name, professions=", ".join(config.PROFESSIONS), types=", ".join(TYPES), text=text[:8000])
        try:
            answer = _json_in(self._call(SYSTEM, user))
        except CodexLimit:
            self.stopped = "Codex: the ChatGPT usage limit is reached"
            return {"error": self.stopped}
        except Exception as e:
            return {"error": type(e).__name__ + ": " + str(e)[:200]}
        rec = {"cache_key": self.cache_key(name, text), "company": name, "provider": self.provider, "model": self.model,
               "prompt_version": self.version, "asked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
               "answer": answer}
        with self._lock:
            self.cache.setdefault(rec["cache_key"], {})[self.label] = rec
            with open(self.cache_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return rec


def classify(name, text, model, ask_all=False):
    """Combines the rules and, when needed, the model. Returns one result dict."""
    if not text:
        return {"decision": "incertain", "type": "", "professions": [], "quote": "", "quote_check": "",
                "basis": "no website text", "reason": "", "review": "no website found or readable"}
    r = rules(text)
    out = dict(r, basis="rules", quote_check="from the page" if r["quote"] else "", reason="", review="")
    if model.provider != "none" or model.cached(name, text):
        if ask_all or r["decision"] == "incertain":
            rec = model.ask(name, text)
            if rec and "answer" in rec:
                a = rec["answer"]
                dec = plain(str(a.get("editeur_logiciel_sante", ""))).strip()
                dec = dec if dec in ("oui", "non", "incertain") else "incertain"
                quote = str(a.get("citation") or "").strip()
                found = quote_found(quote, text)
                profs = [p for p in (a.get("professions") or []) if p in config.PROFESSIONS]
                out.update(decision=dec, type=str(a.get("type") or ""), professions=profs or r["professions"],
                           quote=quote, quote_check="found on the page" if found else "NOT found on the page",
                           basis="%s:%s" % (rec["provider"], rec["model"]), reason=str(a.get("raison") or ""))
                if not found and dec != "incertain":
                    out["review"] = "the model's quote is not on the page"
            elif rec and rec.get("error"):
                out["review"] = "model call failed: " + rec["error"]
    if out["decision"] == "incertain" and not out["review"]:
        out["review"] = "unclear from the website"
    return out


def describer(spec="none"):
    """The Codex model that writes the PDF's descriptions, with its own cache."""
    return Model(spec, cache_path=config.DATA / "descriptions.jsonl", prompt=DESCRIBE, schema=DESCRIPTION_SCHEMA,
                 version=DESCRIBE_VERSION)


def describe(name, text, model):
    """One sentence on what the company sells and to whom, kept only when the sentence it rests
    on is on the page. Returns (description, note)."""
    if not text:
        return "", "no website text"
    rec = model.ask(name, text)
    if not rec:
        return "", "not asked (no model)"
    if "answer" not in rec:
        return "", "model call failed: " + rec.get("error", "")
    a = rec["answer"]
    description = str(a.get("description") or "").strip()
    if not description:
        return "", "the page does not say"
    if not quote_found(str(a.get("citation") or ""), text):
        return "", "the model's sentence is not on the page"
    return description, "rests on a sentence found on the page"
