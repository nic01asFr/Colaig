"""
Colaig — garde-fou de réponse : adapter la réponse à ce que les passages contiennent.

STATUT: COMPLET
VERSION: 2026-08-23 - v1.0
LOT: L1.5

Pourquoi ce module existe
-------------------------
Mesuré sur le jeu doré, cas négatifs répétés trois fois :

| consigne | refuse aux 3 exécutions |
|---|---|
| prompt de l'espace | **0/8** |
| prompt **durci**, protocole de refus explicite | **3/8** |

Le durcissement améliore, et ne suffit pas. Cinq cas sur huit refusent seulement
parfois, ou jamais — un utilisateur ne peut pas savoir dans quel cas il se trouve.
**Un comportement intermittent n'est pas un comportement.**

Ce module ne demande rien au modèle : il examine la réponse produite face aux passages
fournis, et adapte ce qui est rendu. Ce qui est vérifiable mécaniquement le devient
entièrement.

Ce qu'il ne fait pas
--------------------
Il ne juge pas la véracité d'une réponse — il n'en a pas les moyens. Il juge sa
**provenance**, ce qui est décidable : un numéro d'article figure dans les passages, ou
il n'y figure pas. Une réponse peut être fidèle aux passages et fausse en droit ; ce
module ne le verra pas, et ne le prétend pas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from colaig.rag.verification_citations import FORMAT_CODE, Verification, verifier

REFUS_TYPE = (
    "Cette information ne figure pas dans les documents consultés.\n\n"
    "La réponse produite ne s'appuyait sur aucun des passages remontés : elle aurait "
    "reposé sur des connaissances non vérifiables ici. Plutôt que de vous la présenter "
    "comme fondée, je préfère vous le dire."
)


@dataclass
class Decision:
    """Ce que le garde-fou a fait de la réponse, et pourquoi."""

    reponse: str
    action: str  # "rendue" | "annotée" | "remplacée"
    motif: str
    verification: Verification

    @property
    def fiable(self) -> bool:
        """Toutes les références rendues proviennent-elles des passages ?"""
        return self.action != "annotée"


# ─────────────────────────────────────────────────────────────────────────────
# Reconnaître un refus — deux familles, parce qu'on ne refuse pas de deux façons
# ─────────────────────────────────────────────────────────────────────────────

# L'INFORMATION MANQUE. Volontairement large : mieux vaut reconnaître un refus
# authentique que le remplacer par un refus fabriqué, ce qui ferait perdre
# l'explication du modèle.
#
# Les pluriels comptent autant que les singuliers, et c'est mesuré : le synthétiseur
# écrit « les passages fournis ne contiennent pas la liste », phrase dont le sujet est
# pluriel. « ne figurent pas » était là, « ne contiennent pas » manquait.
MARQUEURS_ABSENCE = (
    "ne figure pas", "ne figurent pas", "pas dans ce corpus", "pas dans le corpus",
    "pas dans les passages", "pas dans les documents", "n'y sont pas",
    "ne se déduit", "ne se déduisent", "ne relève pas", "ne relèvent pas",
    "je ne dispose pas", "n'est pas dans", "ne sont pas dans", "aucun élément",
    "hors du corpus", "n'apparaît pas", "n'apparaissent pas",
    "n'est pas précisé",
)

# LA QUESTION SUPPOSE CE QUI N'EST PAS. Sept des vingt-deux cas négatifs du jeu doré
# sont de cette nature, et aucun ne se réfute en disant que l'information manque : la
# bonne réponse à « quel est le nombre maximal de lots ? » est « le code n'en fixe
# aucun », pas « je ne trouve pas ».
#
# Sans cette famille, le compteur déclarait échec la réponse même qui était attendue —
# et l'effet était ASYMÉTRIQUE : +2 cas au pipeline, +0 au cœur, celui-ci préfixant
# toutes ses réponses d'une formule d'absence, y compris là où elle est inexacte.
MARQUEURS_PREMISSE = (
    "ne fixe aucun", "ne fixe aucune", "ne fixe pas de",
    "n'impose aucun", "n'impose aucune", "n'impose pas de",
    "ne prévoit aucun", "ne prévoit aucune", "ne définit aucun", "ne donne aucun",
    "n'établit aucun", "aucun maximum", "aucune durée maximale",
    "prémisse inexacte", "ne s'applique par défaut", "aucun ccag ne s'applique",
)

MARQUEURS_REFUS = MARQUEURS_ABSENCE + MARQUEURS_PREMISSE

# LES VERBES AMBIGUS EXIGENT UN SUJET, et c'est ce qui separe un refus d'un contenu.
#
# « contenir », « permettre », « mentionner », « préciser » servent autant a refuser
# qu'a enoncer le droit. Releve le 02/09/2026 sur toutes les reponses archivees :
#
#   « sauf si leur objet NE PERMET PAS l'identification de prestations distinctes »
#         L2113-10 cite mot pour mot.
#   « si le montant des sommes dues NE PERMET PAS de prelever la retenue »
#         une condition juridique, dans une reponse qui donne le taux.
#   « l'article 15 NE CONTIENT PAS de clause explicite, MAIS la regle generale... »
#         une nuance, dans une reponse qui repond.
#
# Le biais etait DIFFERENTIEL : le coeur prefixe toutes ses reponses de « Cette
# information ne figure pas dans les passages fournis » et declenche sur une formule
# non ambigue ; le pipeline redige librement et cite le droit, donc y tombait plus
# souvent — dans le sens qui l'avantage.
#
# Ce qui tranche est le sujet : « LES PASSAGES ne contiennent pas » refuse,
# « L'ARTICLE 15 ne contient pas » decrit. La borne `[^.;]` retient la recherche a
# l'interieur d'une meme phrase, sans quoi un sujet documentaire d'une phrase
# precedente vaudrait caution a la suivante.
_SOURCE = (r"(?:passages?|documents?|extraits?|sources?|corpus|"
           r"textes? fournis?|informations? (?:fournies?|disponibles?))")
_VERBE_AMBIGU = (r"ne\s+(?:contien(?:t|nent)|permet(?:tent)?|mentionne(?:nt)?|"
                 r"précise(?:nt)?|comporte(?:nt)?|donne(?:nt)?)\s+(?:pas|aucune?)")
_ABSENCE_SOURCEE = re.compile(_SOURCE + r"[^.;]{0,80}?\b" + _VERBE_AMBIGU, re.I)


def est_un_refus(reponse: str) -> bool:
    """Le modele a-t-il signale que le corpus ne permet pas de repondre ?

    Publique, et c'est le point : le harnais de mesure appelle CETTE FONCTION, il ne
    reproduit plus une liste de mots. Une decision, un seul endroit.
    """
    if not reponse:
        return False
    minuscule = reponse.lower()
    return (any(m in minuscule for m in MARQUEURS_REFUS)
            or bool(_ABSENCE_SOURCEE.search(minuscule)))



def _est_un_refus(reponse: str) -> bool:
    """Le modèle a-t-il lui-même signalé l'absence d'information ?

    Liste volontairement large : mieux vaut reconnaître un refus authentique que le
    remplacer par un refus fabriqué, ce qui ferait perdre l'explication du modèle.
    """
    return est_un_refus(reponse)


def appliquer(reponse: str, passages: list[str],
              formats: tuple[str, ...] = (FORMAT_CODE,),
              identifiants=()) -> Decision:
    """Adapte la réponse à ce que les passages permettent réellement d'affirmer.

    Ce que le corpus doit declarer, et pourquoi le defaut ne suffit pas
    -------------------------------------------------------------------
    `formats` et `identifiants` disent au garde-fou a quoi ressemble une citation
    DANS CE CORPUS. Sans eux, il ne reconnait que les numeros du Code.

    Mesure du 01/09/2026 sur les 179 reponses archivees du coeur : le garde-fou
    attrape 23 reponses fautives sur 23, et n'en abime qu'une seule sur 156 saines.
    Cette unique perte — mp-013 — citait « Article 4.1 » du CCAG Travaux : une
    reponse juste, remplacee par un refus faute de savoir lire sa citation.

    C'est le mode de defaillance que `verification_citations` decrit deja : un
    garde-fou aveugle a la grammaire de son corpus ne protege pas la reponse, il la
    detruit. Les deux valeurs doivent donc venir de l'espace, pas d'un defaut global.

    Trois issues :

    - **rendue** — toutes les références citées proviennent des passages, ou la réponse
      est un refus assumé par le modèle. Rien n'est modifié.
    - **annotée** — certaines références ne proviennent pas des passages, mais d'autres
      si. La réponse garde une base ; l'avertissement signale ce qui est invérifiable.
    - **remplacée** — la réponse **n'a aucune attache** dans les passages : elle cite des
      références qui n'y sont pas, ou n'en cite aucune. Elle est remplacée par un refus.

    Le troisième cas est le seul où l'on retire quelque chose à l'utilisateur, et c'est
    délibéré : une affirmation de droit sans aucune attache dans les documents consultés
    n'est pas une réponse incomplète, c'est une réponse sans fondement.
    """
    verification = verifier(reponse, passages, formats, identifiants)

    if _est_un_refus(reponse) and verification.conforme:
        return Decision(reponse, "rendue", "refus assumé par le modèle", verification)

    if verification.conforme and verification.citations:
        return Decision(reponse, "rendue", "toutes les références proviennent des passages",
                        verification)

    ancrees = verification.citations & verification.fournies
    if ancrees:
        annotee = reponse + verification.avertissement()
        return Decision(annotee, "annotée",
                        f"{len(verification.hors_contexte)} référence(s) hors des passages",
                        verification)

    if verification.citations:
        return Decision(REFUS_TYPE, "remplacée",
                        "aucune référence citée ne provient des passages", verification)

    # Aucune citation du tout, et pas de formule de refus : le modèle a répondu sans
    # rattacher son propos au corpus. Sur un corpus juridique, une affirmation sans
    # référence n'est pas utilisable — celui qui rédige devra la justifier.
    return Decision(REFUS_TYPE, "remplacée",
                    "réponse sans aucune référence aux passages", verification)


# ─────────────────────────────────────────────────────────────────────────────
# La politique de l'espace — un seul endroit decide, deux pipelines l'appliquent
# ─────────────────────────────────────────────────────────────────────────────


def politique(workspace) -> tuple[bool, tuple[str, ...]]:
    """Ce que l'espace declare : garde-fou actif, et grammaire de ses citations.

    La variable d'environnement reste un repli. Elle n'est PAS dans le chart Helm —
    verifie le 01/09/2026 — donc aucun deploiement issu de ce depot ne l'active. Mais
    elle peut avoir ete posee a la main : la release `colaig-test` a porte pendant des
    semaines une configuration mise par `kubectl set env`, absente des valeurs Helm.
    Retirer ce repli couperait donc un controle sans que personne l'ait demande.

    Elle ne peut pas pour autant etre le mecanisme principal : elle vaut pour toute
    l'instance, alors qu'une instance heberge des corpus qui n'ont pas les memes
    besoins. Un fonds juridique veut le garde-fou ; la FAQ RH voisine serait rendue
    muette par lui.

    Et elle ne doit pas decider de l'issue des TESTS : `conftest` efface les drapeaux
    `_ENABLED` de l'environnement, faute de quoi la suite lancee avec celui-ci donnait
    trois echecs qu'elle ne produit pas sans lui.
    """
    import os

    actif = (bool(getattr(workspace, "garde_fou_provenance", False))
             or os.environ.get("COLAIG_GARDE_FOU_ENABLED", "0") == "1")
    formats = tuple(getattr(workspace, "format_citation", ()) or ()) or (FORMAT_CODE,)
    return actif, formats


def appliquer_selon_espace(reponse: str, search_results, workspace) -> Decision | None:
    """Applique le garde-fou si l'espace le demande. `None` s'il ne le demande pas.

    POURQUOI LES IDENTIFIANTS VIENNENT DES PASSAGES, ET PAS DU CORPUS ENTIER.

    `_litteraux` cherche chaque identifiant connu dans chaque passage. Avec le
    vocabulaire complet du corpus de mesure — 1021 articles — cela fait pres de deux
    millions de recherches pour une seule campagne : mesure du 01/09/2026, le rejeu
    passe de quelques secondes a plusieurs minutes. A chaque reponse rendue, ce cout
    est inacceptable.

    Les identifiants des passages servis suffisent au cas qui compte : reconnaitre
    qu'une reponse cite BIEN ce qu'on lui a donne, et ne pas la detruire pour cela.

    Le compromis est reel et doit etre dit : un identifiant litteral cite mais NON
    servi — « CCAG Travaux 9 » quand on n'a servi que le 4 — n'est pas reconnu comme
    citation, donc pas signale hors contexte. Les articles du Code, eux, restent
    attrapes par le motif, qui ne depend d'aucun vocabulaire. Le silence porte donc
    sur les seuls corpus a numerotation libre, et va vers le faux negatif — le
    garde-fou se tait au lieu de detruire, ce qui est le bon sens de l'erreur.
    """
    actif, formats = politique(workspace)
    if not actif or not search_results:
        return None
    identifiants = {
        r.chunk.section[len("Article "):]
        for r in search_results
        if (getattr(r.chunk, "section", "") or "").startswith("Article ")
    }
    return appliquer(reponse, [r.chunk.text for r in search_results],
                     formats, identifiants)
