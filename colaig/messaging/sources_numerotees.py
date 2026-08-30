"""
Colaig — les sources en exposant, citees une seule fois a la fin.

CE QUE CELA REMPLACE
----------------------
Le modele ecrit ses citations en clair, dans le fil du texte :

    Un debriefing est organise [AccEvtGrave Support participants  septembre  2024.pdf]
    puis un suivi est propose [AccEvtGrave Support participants  septembre  2024.pdf].

Cinquante caracteres au milieu d'une phrase, deux fois. Rendu :

    Un debriefing est organise¹ puis un suivi est propose¹.

    ¹ AccEvtGrave Support participants  septembre  2024.pdf

LES TROIS CONTRAINTES QUI ONT DECIDE DE LA CONCEPTION
-------------------------------------------------------
**1. Le modele continue d'ecrire `[nom.pdf]`.** `citation_checker` s'ancre sur cette
forme pour verifier qu'une source citee a bien ete fournie. Si le modele produisait
directement des exposants, l'audit anti-hallucination perdrait sa prise. La
numerotation appartient donc au SYSTEME et s'execute APRES le controle — c'est D66,
« la presentation appartient au systeme, pas au modele ».

**2. L'historique garde la forme brute.** `_save_history` enregistre ce qu'on envoie ;
y mettre des exposants ferait recopier des exposants par le modele au tour suivant, et
le verificateur n'aurait plus rien a verifier. C'est **exactement** le defaut des
emojis de gestes, corrige le 30/08/2026 (« le modele recopiait les gestes depuis son
propre historique »). D'ou une fonction PURE : elle rend une chaine neuve, et
l'appelant n'ecrase pas `response.text`.

**3. Une citation sans source ne recoit pas de numero.** Lui en donner un la
maquillerait en reference verifiee : un nom invente prendrait l'apparence d'un document
reel. Elle reste visible telle quelle, et `citation_checker` continue de la signaler.

POURQUOI DES EXPOSANTS UNICODE ET NON DU `<sup>`
--------------------------------------------------
`matrix.py` envoie DEUX representations : `formatted_body` en HTML et `body` en texte
brut, pour les clients sans rendu riche. Un `<sup>` ne vivrait que dans la premiere ;
`_strip_markdown` le perdrait dans la seconde. Les exposants Unicode traversent les
deux sans dependre du convertisseur.
"""

from __future__ import annotations

import re
import unicodedata

# Meme grammaire que `citation_checker._CITATION_RE` : les deux modules doivent voir
# L'espace optionnel en tete n'appartient pas a la citation : il est absorbe quand
# on remplace, pour que l'appel de note s'attache au mot — « organise¹ puis » et
# non « organise ¹ puis ». Il est rendu tel quel quand on ne remplace pas.
_CITATION_RE = re.compile(r"[ \t]?\[([^\]\n]{2,120})\]")

# Meme critere que `citation_checker._REF_RE` : une extension ou un chemin. C'est ce
# qui distingue `[rapport.pdf]` de `[nom de l'espace]`, un espace reserve que Colaig
# ecrit dans ses propres consignes.
_REF_RE = re.compile(r"(\.[A-Za-z0-9]{1,5}$)|/")

_ESPACES = re.compile(r"\s+")

_CHIFFRES_EN_EXPOSANT = str.maketrans("0123456789", "\u2070\u00b9\u00b2\u00b3\u2074"
                                                    "\u2075\u2076\u2077\u2078\u2079")


def _norm(valeur: str) -> str:
    """Identique a `citation_checker._norm` — et ce n'est pas une duplication anodine.

    Les deux modules doivent s'accorder sur ce qu'est « le meme document », sinon le
    verificateur validerait une citation que la numerotation ne reconnaitrait pas. La
    regle : on compare ce qu'un LECTEUR verrait — suites d'espaces reduites, Unicode
    normalise en NFC.
    """
    base = unicodedata.normalize("NFC", valeur).strip().lower().rsplit("/", 1)[-1]
    return _ESPACES.sub(" ", base)


def _exposant(n: int) -> str:
    return str(n).translate(_CHIFFRES_EN_EXPOSANT)


def numeroter_les_sources(texte: str, sources: list[str]) -> str:
    """Remplace les citations par des exposants et liste les sources a la fin.

    Fonction PURE : `texte` n'est pas modifie. Voir la contrainte 2 de l'en-tete —
    l'historique doit conserver la forme `[nom.pdf]`.

    Args:
        texte: la reponse telle que le modele l'a ecrite.
        sources: les documents reellement fournis au modele.

    Returns:
        Le texte a envoyer. Inchange s'il ne cite aucune source connue.
    """
    if not texte or not sources:
        return texte

    par_norme = {_norm(s): s for s in sources}
    numeros: dict[str, int] = {}
    ordre: list[str] = []

    def _remplacer(m: re.Match) -> str:
        citation = m.group(1).strip()
        if not _REF_RE.search(citation):
            return m.group(0)          # espace reserve, pas une reference
        source = par_norme.get(_norm(citation))
        if source is None:
            return m.group(0)          # citation sans source : visible telle quelle
        if source not in numeros:
            numeros[source] = len(numeros) + 1
            ordre.append(source)
        return _exposant(numeros[source])

    corps = _CITATION_RE.sub(_remplacer, texte)
    if not ordre:
        return texte

    notes = "\n".join(f"{_exposant(numeros[s])} {s.rsplit('/', 1)[-1]}" for s in ordre)
    return f"{corps.rstrip()}\n\n{notes}"
