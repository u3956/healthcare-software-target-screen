# Codex answers and the quote check

When the word rules cannot tell whether a company publishes health software, Codex reads the
website text (`code/classify.py`). It must answer in a fixed JSON format and copy one sentence
from the page. The script then looks for that sentence in the text it sent: if it is not
there, the answer is flagged with the note "the model's quote is not on the page", and the
company is listed for a person to check.

In the run of 26 October 2026, Codex read 441 websites the rules could not settle:

| Answer | Sentence found on the page | Sentence not found (flagged) |
|---|---:|---:|
| Not a publisher | 372 | 6 |
| Publisher | 53 | 3 |
| Unclear | 5 | 2 |

Three answers from that run, as Codex gave them (the prompt and the answers are in French):

**Kept: a publisher.**

```json
{"editeur_logiciel_sante": "oui",
 "type": "gestion de cabinet",
 "professions": ["Médecins"],
 "citation": "Doctolib Pro accompagne chaque jour des centaines de milliers de soignants dans la gestion de leur cabinet avec des solutions sur-mesure.",
 "raison": "Le texte présente Doctolib Pro comme un logiciel destiné aux professionnels de santé pour gérer leur cabinet."}
```
Check: the sentence is on the page, so the answer stands.

**Kept: not a publisher.** A pharmaceutical company seen at a medical congress.

```json
{"editeur_logiciel_sante": "non",
 "type": "pas un logiciel",
 "professions": ["Médecins", "Établissements"],
 "citation": "Nous prévenons et traitons les maladies grâce aux vaccins et médicaments de médecine générale et de spécialité.",
 "raison": "Le texte présente GSK comme une entreprise biopharmaceutique qui développe des vaccins et médicaments, sans indiquer qu’elle édite un logiciel pour les professionnels de santé."}
```
Check: the sentence is on the page; the company goes Out.

**Flagged: the sentence is not on the page.**

```json
{"editeur_logiciel_sante": "oui",
 "type": "pharmacie",
 "professions": ["Pharmacies"],
 "citation": "Nos expertises Logiciel Pharmacie",
 "raison": "Le texte indique qu’Astera propose un logiciel pour les pharmacies."}
```
Check: this "sentence" does not appear in the page text that was sent. The company is listed
on the Review sheet with the note "the model's quote is not on the page", for a person to
check before relying on the answer.
