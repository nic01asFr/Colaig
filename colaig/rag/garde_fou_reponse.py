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

from dataclasses import dataclass

from colaig.rag.verification_citations import Verification, verifier

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


def _est_un_refus(reponse: str) -> bool:
    """Le modèle a-t-il lui-même signalé l'absence d'information ?

    Liste volontairement large : mieux vaut reconnaître un refus authentique que le
    remplacer par un refus fabriqué, ce qui ferait perdre l'explication du modèle.
    """
    marqueurs = (
        "ne figure pas", "ne figurent pas", "ne contient pas", "ne permet pas",
        "pas dans ce corpus", "pas dans le corpus", "pas dans les passages",
        "pas dans les documents", "n'y sont pas", "ne se déduit", "ne relève pas",
        "je ne dispose pas", "n'est pas dans", "aucun élément", "hors du corpus",
        "n'apparaît pas", "ne mentionne pas", "ne précise pas", "n'est pas précisé",
    )
    minuscule = reponse.lower()
    return any(m in minuscule for m in marqueurs)


def appliquer(reponse: str, passages: list[str]) -> Decision:
    """Adapte la réponse à ce que les passages permettent réellement d'affirmer.

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
    verification = verifier(reponse, passages)

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
