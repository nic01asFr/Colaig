"""
Lire un nombre — entièrement, ou pas du tout.

STATUT: COMPLET
VERSION: 2026-08-28 - v1.0
LOT: L1.5 (correction de la notation)

Le défaut mesuré
-----------------
`montants()` cherchait `\\d{1,3}( \\d{3})+` — « 25 000 » et rien d'autre. Mesuré le
28/08 sur le corpus doré :

    grandeurs en chiffres + unité :  130
    grandeurs en lettres  + unité : 1042      89 %
    vues par la métrique          :   42       4 %

L'indicateur `montants_inventes`, présenté comme le plus grave des sept, **couvrait
4 % de la surface**. Un montant fabriqué écrit « quarante-cinq mille euros » lui était
invisible.

Origine et dette
-----------------
Portage de `lireNombre` et `grandeurs`, de `Editeur/redacteur/src/coherence.js` — projet
voisin dont le corpus est assemblé depuis le nôtre. Leur mesure indépendante donnait
71 % de grandeurs en lettres sur un sous-ensemble de 399 sources ; nous trouvons 89 %
sur les 1021.

**Ce qui vaut d'être porté n'est pas la lecture des lettres, c'est le `None`.** Leur
en-tête le dit mieux qu'on ne l'écrirait : « quarante-cinq jours » lu « 5 jours » est
pire qu'un nombre non lu, et le motif naïf le fait **2 fois sur 146**. Un vérificateur
qui annonce 5 là où le texte dit 45 se disqualifie.

Ce que la fonction refuse de faire
-----------------------------------
Deviner. Un jeton qu'elle ne sait pas lire annule la lecture entière — elle ne rend
jamais une valeur partielle. C'est le contrat, et c'est tout l'intérêt.
"""
from __future__ import annotations

import re
import unicodedata

SIMPLES = {
    "zero": 0, "un": 1, "une": 1, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5,
    "six": 6, "sept": 7, "huit": 8, "neuf": 9, "dix": 10, "onze": 11, "douze": 12,
    "treize": 13, "quatorze": 14, "quinze": 15, "seize": 16,
}
DIZAINES = {"vingt": 20, "trente": 30, "quarante": 40, "cinquante": 50, "soixante": 60}

_ESPACES = "   "   # insécable, fine insécable, espace ordinaire


def _sans_accent(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", str(s or "").lower())
                   if unicodedata.category(c) != "Mn")


def _jetons(s: str) -> list[str]:
    """« quatre-vingt-dix-sept » → 4 jetons. Le trait d'union et « et » séparent."""
    return [j for j in re.split(r"[-\s]+|\bet\b", _sans_accent(s)) if j]


def lire_nombre(brut) -> float | None:
    """Le nombre, ou `None` s'il reste un jeton non consommé.

    Le `None` est le contrat : mieux vaut ne rien dire que dire faux.
    """
    t = str(brut or "").strip()
    if not t:
        return None

    # Forme chiffrée : espaces fins et insécables inclus, virgule décimale.
    if re.fullmatch(rf"[\d][\d{_ESPACES}]*(?:[.,]\d+)?", t):
        try:
            n = float(re.sub(rf"[{_ESPACES}]", "", t).replace(",", "."))
        except ValueError:
            return None
        return n

    j = _jetons(t)
    if not j:
        return None

    total = 0     # ce qui est acquis au-delà des centaines
    bloc = 0      # la centaine en cours
    vu = False
    i = 0
    while i < len(j):
        m = j[i]
        if m == "mille":
            total += (bloc or 1) * 1000
            bloc = 0
            vu = True
        elif m in ("cent", "cents"):
            bloc = (bloc or 1) * 100
            vu = True
        elif m == "quatre" and i + 1 < len(j) and j[i + 1] in ("vingt", "vingts"):
            # La seule forme où « quatre » ne vaut pas 4.
            bloc += 80
            i += 1
            vu = True
        elif m in DIZAINES:
            suite = j[i + 1] if i + 1 < len(j) else None
            if m == "soixante" and suite is not None and SIMPLES.get(suite, -1) >= 10:
                # soixante-dix, soixante-et-onze…
                bloc += 60 + SIMPLES[suite]
                i += 1
            else:
                bloc += DIZAINES[m]
            vu = True
        elif m in SIMPLES:
            bloc += SIMPLES[m]
            vu = True
        else:
            return None          # un jeton qu'on ne sait pas lire
        i += 1
    return float(total + bloc) if vu else None


# ── Les grandeurs d'un texte ────────────────────────────────────────────────
#
# Une grandeur est un nombre PORTANT UNE UNITÉ contractuelle. Un nombre nu n'en est pas
# une : « article 11 », « en trois exemplaires » ne se comparent pas d'un texte à
# l'autre, et les compter ferait du bruit.

_MOTS = ("zero|zéro|une?|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|onze|douze|"
         "treize|quatorze|quinze|seize|vingts?|trente|quarante|cinquante|soixante|"
         "cents?|mille")
_SUITE = rf"(?:{_MOTS})(?:[-\s]+(?:et[-\s]+)?(?:{_MOTS}))*"
_CHIFFRES = rf"\d[\d{_ESPACES}]*(?:[.,]\d+)?"

# « trente (30) jours » est courant en rédaction contractuelle : le chiffre entre
# parenthèses fait foi, et la redondance en lettres n'est pas une seconde grandeur.
_PAREN = rf"(?:\s*\(({_CHIFFRES})\))?"

UNITES = [
    ("duree", r"ann[ée]es?|ans?"),
    ("duree", r"mois"),
    ("duree", r"semaines?"),
    ("duree", r"jours?"),
    ("montant", r"euros?|€"),
    ("taux", r"%|pour cent"),
]


def _motif(unite: str) -> re.Pattern:
    # Anti-regard plutôt que `\b` : `\b` ne s'accroche pas après « % », qui n'est pas
    # un caractère de mot — « 5 % » ne matcherait pas. L'anti-regard empêche aussi
    # « mois » de mordre sur « moisissure ».
    return re.compile(rf"({_CHIFFRES}|{_SUITE}){_PAREN}\s*({unite})(?![a-zà-ÿ])",
                      re.IGNORECASE)


def grandeurs(texte: str) -> list[dict]:
    """Les nombres portant une unité, avec leur nature. `nombre` vaut None si illisible.

    Les grandeurs illisibles sont RENDUES, jamais tues : une valeur qu'on n'a pas su
    lire est une information, pas un silence.
    """
    t = str(texte or "")
    resultats: list[dict] = []
    pris: set[int] = set()

    for nature, unite in UNITES:
        for m in _motif(unite).finditer(t):
            etendue = range(m.start(), m.end())
            if any(k in pris for k in etendue):
                # « ans » précède « mois » dans UNITES : la première unité qui accroche
                # gagne, sinon « années » se ferait manger par un motif plus court.
                continue
            pris.update(etendue)
            brut_nombre = m.group(2) if m.group(2) is not None else m.group(1)
            resultats.append({
                "nature": nature,
                "nombre": lire_nombre(brut_nombre),
                "brut": re.sub(r"\s+", " ", m.group(0)).strip(),
                "unite": m.group(3),
            })
    return resultats


def montants(texte: str) -> set[str]:
    """Les MONTANTS d'un texte, sous leur forme normalisée pour comparaison.

    Remplace le motif `\\d{1,3}( \\d{3})+`, qui ne voyait que 4 % des grandeurs.

    Un montant illisible n'est PAS rendu : on ne peut pas comparer ce qu'on n'a pas su
    lire, et l'inventer serait exactement la faute qu'on traque.
    """
    return {f"{g['nombre']:.0f}" if float(g["nombre"]).is_integer() else str(g["nombre"])
            for g in grandeurs(texte)
            if g["nature"] == "montant" and g["nombre"] is not None}
