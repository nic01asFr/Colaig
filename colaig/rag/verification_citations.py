"""
Colaig — vérification des citations d'une réponse.

STATUT: COMPLET
VERSION: 2026-08-23 - v1.0
LOT: L1.5

Contrôle **mécanique** de la provenance des références citées dans une réponse.

Pourquoi il faut un contrôle et pas une consigne
------------------------------------------------
Mesuré sur 45 cas du jeu doré, avec le prompt système de l'espace qui interdit déjà
d'inventer : **10 réponses citent un article réel absent des passages fournis**. Le RAG
est alors contourné — la réponse vient de l'entraînement du modèle, pas du corpus.

Trois observations de cette mesure :

- une réponse cite `L1414-3`, article du **Code général des collectivités territoriales**,
  dans un corpus qui ne contient que le Code de la commande publique ;
- un cas négatif cite trois références différentes à chaque exécution — signature d'une
  fabrication, pas d'une erreur ;
- une réponse cite **l'article exact attendu** alors qu'il n'était pas dans les passages.
  Bonne réponse, mauvaise provenance : juste aujourd'hui, faux le jour où le texte
  changera sans que le corpus ait été relu.

Ce dernier cas est décisif. **La justesse apparente d'une réponse ne prouve rien sur sa
provenance**, et seul un contrôle qui compare les citations aux passages le détecte.
Une consigne, même explicite, se respecte « la plupart du temps » — ce qui, sur du droit,
ne suffit pas.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# Un numéro d'article du Code de la commande publique, sous ses graphies usuelles :
# « L2113-10 », « L. 2113-10 », « article R. 2122-8 ».
_MOTIF_ARTICLE = re.compile(r"\b([LRD])\.?\s?(\d{4}-\d+(?:-\d+)?)\b")


def articles_cites(texte: str) -> set[str]:
    """Numéros d'article mentionnés dans un texte, normalisés (`L2113-10`)."""
    return {f"{lettre}{numero}" for lettre, numero in _MOTIF_ARTICLE.findall(texte or "")}


@dataclass
class Verification:
    """Résultat du contrôle de provenance d'une réponse."""

    citations: set[str] = field(default_factory=set)
    fournies: set[str] = field(default_factory=set)
    hors_contexte: set[str] = field(default_factory=set)

    @property
    def conforme(self) -> bool:
        """Toutes les citations proviennent-elles des passages fournis ?"""
        return not self.hors_contexte

    def avertissement(self) -> str:
        """Mention à joindre à la réponse quand elle ne l'est pas."""
        if self.conforme:
            return ""
        refs = ", ".join(sorted(self.hors_contexte))
        pluriel = "s" if len(self.hors_contexte) > 1 else ""
        return (
            f"\n\n---\n\n⚠️ **Référence{pluriel} non vérifiable{pluriel} : {refs}.** "
            f"{'Ces articles ne figurent pas' if len(self.hors_contexte) > 1 else 'Cet article ne figure pas'} "
            "dans les documents consultés pour cette réponse. "
            f"{'Ils proviennent' if len(self.hors_contexte) > 1 else 'Il provient'} "
            "de la mémoire du modèle et non du corpus : à vérifier avant tout usage."
        )


def verifier(reponse: str, passages: list[str]) -> Verification:
    """Compare les articles cités dans la réponse à ceux présents dans les passages."""
    citations = articles_cites(reponse)
    fournies: set[str] = set()
    for passage in passages:
        fournies |= articles_cites(passage)
    return Verification(
        citations=citations,
        fournies=fournies,
        hors_contexte=citations - fournies,
    )


def annoter(reponse: str, passages: list[str]) -> tuple[str, Verification]:
    """Réponse assortie d'un avertissement si elle cite hors des passages.

    **Annoter plutôt que supprimer.** Retirer la référence rendrait la réponse plus
    propre et moins vérifiable : l'utilisateur perdrait l'information qui lui permet de
    contrôler. Une mention visible lui laisse la décision, ce qui est le bon partage —
    c'est lui qui engage sa procédure, pas l'assistant.
    """
    verification = verifier(reponse, passages)
    return reponse + verification.avertissement(), verification
