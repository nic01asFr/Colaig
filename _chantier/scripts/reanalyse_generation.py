"""Recompte les indicateurs de génération sur des réponses déjà stockées.

Pourquoi ce script existe
-------------------------
Deux règles de comptage se sont révélées fausses **après** que les mesures ont tourné.
Relancer le modèle pour les corriger coûterait une heure et changerait les réponses,
donc empêcherait toute comparaison. Les réponses étant stockées et la recherche
déterministe, on recompte sans rien réinterroger.

Les deux corrections
--------------------
**1. Une référence présente dans les passages n'est jamais un fantôme.** La métrique
comparait au seul corpus du Code de la commande publique. Or ce corpus est *un* code, et
les articles qu'il cite ne le sont pas : `L5132-4` et `L5213-13`, du code du travail,
figurent mot pour mot dans `L2113-13` et `L2113-12`. Le modèle relayait un renvoi, la
mesure l'accusait d'inventer.

**2. Une réponse tronquée ne peut pas être jugée sur ce qu'elle cite.** La règle
existait déjà pour le refus — `mp-044` avait été coupée à neuf caractères, au milieu de
sa formule de refus. Elle vaut identiquement pour les citations : `mp-063` se termine par
« (Article R2112- », `mp-107` par « **Article R2 ». Ce ne sont pas des citations, ce
sont des moignons, et le motif de reconnaissance les lit comme des articles courts.

C'est l'effet de bord d'un élargissement par ailleurs justifié : le motif accepte
désormais `L1` à `L6`, articles préliminaires bien réels et parmi les plus cités. Il ne
peut pas distinguer un article court d'une référence coupée — mais on sait *laquelle des
deux* on a, puisqu'on sait si la réponse a été tronquée.

Ce que la correction ne change pas
-----------------------------------
Les réponses tronquées ne disparaissent pas du rapport : elles sont comptées à part.
Une réponse coupée reste un défaut de service, simplement ce n'est pas un défaut de
fidélité.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(RACINE))

# Capturer les arguments AVANT d'ecraser sys.argv pour le module importe.
# reference_generation.py documente exactement ce piege — et je viens d'y retomber :
# sans cette ligne, le chemin du fichier de reponses valait « article », la strategie
# de decoupage posee plus bas.
_ARGS = list(sys.argv[1:])

_ns: dict = {
    "__name__": "_harnais",
    "__file__": str(RACINE / "_chantier" / "scripts" / "reference_l15.py"),
}
sys.argv = ["gen", "article"]
exec(  # noqa: S102
    compile((RACINE / "_chantier" / "scripts" / "reference_l15.py").read_text(encoding="utf-8")
            .replace("raise SystemExit(main())", "pass"), "reference_l15.py", "exec"),
    _ns, _ns,
)
decouper, embed, cle_albert = _ns["decouper"], _ns["embed"], _ns["cle_albert"]

from colaig.rag.faiss_store import FaissStore  # noqa: E402
from colaig.rag.garde_fou_reponse import appliquer  # noqa: E402
from colaig.rag.verification_citations import articles_cites  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))   # pour `nombres`

JEU = RACINE / "tests" / "golden" / "v1.jsonl"
MESURES = RACINE / "_chantier" / "mesures"

MARQUEURS_REFUS = (
    "ne figure pas", "ne figurent pas", "ne contient pas", "ne permet pas",
    "pas dans ce corpus", "pas dans le corpus", "n'y sont pas", "ne se déduit",
    "je ne dispose pas", "n'est pas dans", "ne relève pas", "aucun élément",
    "hors du corpus", "n'apparaît pas",
)


# Le motif d'origine etait `\d{1,3}( \d{3})+` : il exigeait un groupement par milliers
# ET N'EXIGEAIT PAS D'UNITE. Deux defauts OPPOSES, mesures le 28/08 :
#
#   - aveugle aux lettres — 89 % des grandeurs du corpus sont ecrites ainsi, et
#     « quarante-cinq mille euros » lui echappait entierement ;
#   - trop large sur les chiffres — « 25 000 » comptait comme un montant meme SANS
#     unite, donc « l'article 25 000 » aussi.
#
# `nombres.montants` exige l'unite et lit les lettres. MESURE, sur douze archives :
# l'indicateur `montants_inventes` NE BOUGE PAS (0,58 contre 0,58) — les deux defauts
# se compensaient. L'angle mort etait reel et VIDE.
#
# On corrige quand meme : un indicateur juste pour de mauvaises raisons cesse de l'etre
# au premier texte different. La valeur de reference (0,65 · plafond 3) reste valable
# telle quelle — cette correction n'oblige a AUCUNE remesure.
from nombres import montants  # noqa: E402,F401


def cite_reference_libre(texte: str, ref: str) -> bool:
    """La reponse cite-t-elle une reference NON codifiee — CCAG, annexe ?

    LE DEFAUT CORRIGE (28/08/2026). Onze cas positifs sur 113 attendent « CCAG
    Travaux 4 » ou « Annexe 2 — Seuils de procedure — texte 1 ». `articles_cites` ne
    connait que `L/R/D` + chiffres : il ne peut PAS produire ces references. Le
    plafond de `cite_attendu` n'etait donc pas 1.0 mais 0.903, et le modele qui repond
    « CCAG Travaux, Article 4.1 [Document 1] » — ce qui est juste — etait compte faux.

    LA RECONNAISSANCE EST ICI, PAS DANS `articles_cites`. Elargir l'extracteur a
    « Article 4 » creerait des faux positifs partout, y compris sur la detection de
    fantomes, ou un numero nu se confondrait avec une reference inventee.

    Deux formes :
      « CCAG Travaux 4 »   le document ET « article 4 » — ou 4.1, 4.2 : le CCAG
                           numerote ses subdivisions et un CCAP cite « art. 11.1 ».
      « … — texte 1 »      le document SEUL. Ces pseudo-articles expliquent comment
                           CHOISIR un CCAG et ne portent pas de numero citable ; le
                           projet voisin les exclut de son corpus pour cette raison.
                           Citer le document EST alors la citation.

    La frontiere apres le numero evite de confondre l'article 4 et l'article 41.
    """
    ref = (ref or "").strip()
    bas = texte.lower()
    pseudo = re.match(r"^(.*?)\s*[—–-]\s*texte\s+\d+$", ref, re.I)
    if pseudo:
        return pseudo.group(1).strip().lower() in bas
    m = re.match(r"^(.*?)\s+(\d+)$", ref)
    if not m:
        return ref.lower() in bas
    doc = re.sub(r"\s*[—–-]\s*$", "", m.group(1).strip()).split("—")[0].strip()
    if doc.lower() not in bas:
        return False
    return bool(re.search(rf"articles?\s+{m.group(2)}(?![\d])", texte, re.I))


def cites_attendus(texte: str, attendus: list[str]) -> tuple[bool, bool]:
    """Rend (au moins un attendu cite, TOUS les attendus cites).

    Le second est un indicateur AJOUTE, pas un remplacement. Onze cas attendent
    plusieurs articles et la notation historique accepte l'un d'eux — `mp-002` dit
    pourtant « la reponse exige l'article legislatif ET l'article reglementaire ».

    Le jeu dore n'est PAS modifie : encoder cette exigence reviendrait a inscrire une
    interpretation de onze justifications. Les deux lectures sont rendues cote a cote,
    et l'arbitrage de promouvoir la stricte reste humain.
    """
    trouves = articles_cites(texte)
    ok = [(a in trouves) or cite_reference_libre(texte, a) for a in attendus]
    return any(ok), all(ok)


def tronquee(reponse: str) -> bool:
    """Heuristique de troncature, appliquée au texte stocké.

    Le drapeau `finish_reason` n'a pas été enregistré avec les réponses ; il l'est
    désormais, mais pas rétroactivement. Une réponse qui ne se termine pas par une
    ponctuation forte est tenue pour coupée. C'est **prudent dans le bon sens** : on
    écarte du comptage plutôt que d'accuser à tort.
    """
    return not (reponse or "").rstrip().endswith((".", "!", "?", ":", "»", ")", "]"))


def main() -> int:
    if not _ARGS:
        raise SystemExit("usage : reanalyse_generation.py <fichier-reponses.json> [k]")
    fichier = Path(_ARGS[0])
    k = int(_ARGS[1]) if len(_ARGS) > 1 else 6

    reponses = {r["id"]: r for r in json.loads(fichier.read_text(encoding="utf-8"))}
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]
    cas = [c for c in cas if c["id"] in reponses]

    cle = cle_albert()
    # PERIMETRE : le meme que celui de la mesure, sinon le recomptage est faux.
    #
    # Cette ligne codait « article » en dur. Une mesure lancee sur un perimetre
    # restreint etait donc recomptee contre un corpus different, avec d'autres
    # passages : `fournis` n'etait pas celui que le modele avait recu, et les
    # citations hors contexte grimpaient de 22 a 55 sans raison.
    #
    # J'ai compare une reanalyse coherente a une reanalyse incoherente, et conclu que
    # restreindre le corpus degradait la provenance. C'etait faux. Le perimetre se
    # deduit desormais du nom du fichier de reponses, qui le porte deja.
    perimetre = "article-livre1" if "-livre1" in fichier.name else "article"
    print(f"perimetre deduit     : {perimetre}")
    chunks = decouper(perimetre)
    store = FaissStore()
    store.add(embed([c.text for c in chunks], cle), chunks)
    existants: set[str] = set()
    for ch in chunks:
        existants |= articles_cites(ch.text)
    vq = embed([c["question"] for c in cas], cle)

    # Ce que le garde-fou mecanique fait de chaque reponse. C'est la seule facon de
    # savoir si un regime plus rapide mais moins discipline reste utilisable : le
    # garde-fou est precisement ce qui rattrape une reference hors contexte.
    garde = {"rendue": 0, "annotée": 0, "remplacée": 0}
    n = coupees = fantomes = hors_ctx = inventes = cite_ok = positifs = 0
    cite_complet = 0
    positifs_jugeables = cite_ok_jugeables = 0
    refus_toujours = refus_parfois = refus_jamais = negatifs_jugeables = 0
    anomalies: list[str] = []

    for c, v in zip(cas, vq):
        passages = [r.chunk.text for r in store.search(v, k=k)]
        fournis: set[str] = set()
        for p in passages:
            fournis |= articles_cites(p)
        montants_fournis: set[str] = set()
        for p in passages:
            montants_fournis |= montants(p)

        textes = reponses[c["id"]]["reponses"] or [""]
        jugeables = [t for t in textes if not tronquee(t)]
        coupees += len(textes) - len(jugeables)
        n += 1

        for t in jugeables:
            cites = articles_cites(t)
            f = sorted(cites - existants - fournis)
            h = sorted((cites & existants) - fournis)
            m = sorted(montants(t) - montants_fournis - montants(c["question"]))
            if f:
                fantomes += 1
                anomalies.append(f"- **{c['id']}** — fantôme : {', '.join(f)}")
            if h:
                hors_ctx += 1
                anomalies.append(f"- **{c['id']}** — hors contexte : {', '.join(h)}")
            if m:
                inventes += 1
                anomalies.append(f"- **{c['id']}** — montant inventé : {', '.join(m)}")

        for t in jugeables:
            garde[appliquer(t, passages).action] += 1

        if c.get("attendu_refus"):
            if jugeables:
                negatifs_jugeables += 1
                refuse = [any(mk in t.lower() for mk in MARQUEURS_REFUS) for t in jugeables]
                if all(refuse):
                    refus_toujours += 1
                elif any(refuse):
                    refus_parfois += 1
                else:
                    refus_jamais += 1
        elif c.get("articles_attendus"):
            positifs += 1
            # Deux lectures, et il faut les deux. « Sur tous les cas » mesure ce que
            # l'utilisateur recoit — une reponse coupee ne lui donne pas la citation.
            # « Sur les cas jugeables » isole la fidelite de la troncature, sans quoi
            # une variante qui tronque davantage serait penalisee deux fois pour le
            # meme defaut, et la comparaison entre profondeurs serait faussee.
            if jugeables:
                positifs_jugeables += 1
                un, tous = cites_attendus(jugeables[0], c["articles_attendus"])
                if un:
                    cite_ok += 1
                    cite_ok_jugeables += 1
                if tous:
                    cite_complet += 1

    print(f"fichier              : {fichier.name}   (k={k})")
    print(f"cas                  : {n}")
    print(f"observations coupées : {coupees}  (écartées du comptage des citations)")
    print(f"fantômes             : {fantomes}")
    print(f"hors contexte        : {hors_ctx}")
    print(f"montants inventés    : {inventes}")
    print(f"cite l'attendu       : {cite_ok}/{positifs} (tous) · "
          f"{cite_ok_jugeables}/{positifs_jugeables} (reponses jugeables)")
    print(f"cite l'attendu complet : {cite_complet}/{positifs_jugeables} "
          f"(TOUS les articles attendus)")
    print(f"refus — toujours {refus_toujours} · parfois {refus_parfois} · "
          f"jamais {refus_jamais}  (sur {negatifs_jugeables} négatifs jugeables)")
    total_garde = sum(garde.values())
    print(f"garde-fou            : rendue {garde['rendue']} · annotée {garde['annotée']} "
          f"· remplacée {garde['remplacée']}  (sur {total_garde} réponses jugeables)")
    if anomalies:
        print("\n" + "\n".join(anomalies))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
