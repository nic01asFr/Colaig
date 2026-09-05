"""
Colaig — extraction du contenu d'une réponse de chat completions.

STATUT: COMPLET
VERSION: 2026-08-23 - v1.0
LOT: L1.5

Un seul endroit pour une vérification que les quatre clients omettaient.

Le problème
-----------
Les quatre clients faisaient `return data["choices"][0]["message"]["content"]`, sans
regarder `finish_reason`. Avec un **modèle à raisonnement** — `qwen3-6-35b-moe`, la
cible de production D3 — le raisonnement consomme le même budget de tokens que la
réponse. Mesuré sur une question de rédaction avec six passages de contexte :

| `max_tokens` | `finish_reason` | raisonnement | réponse |
|---|---|---|---|
| 900 | `length` | 3 842 car. | **0 car.** |
| **2048** — défaut du Protocol | `length` | 6 532 car. | tronquée |
| 4000 | `stop` | 10 170 car. | 2 959 car. |

Soit **3,4× plus de raisonnement que de réponse**. En dessous d'environ mille tokens,
l'utilisateur recevait une **chaîne vide** ; à 2048, une phrase coupée. Sans erreur,
sans journal, sans rien. C'est l'échec le plus déroutant pour qui exploite l'instance :
le service répond, mais ne dit rien.
"""
from __future__ import annotations

import logging

from colaig.exceptions import LLMError

logger = logging.getLogger(__name__)


def extraire_contenu(reponse_json: dict, backend: str, max_tokens: int) -> str:
    """Contenu textuel d'une réponse chat completions, troncature signalée.

    Lève `LLMError` si la réponse est **vide par épuisement du budget** : mieux vaut
    une erreur explicite qu'une chaîne vide remontée jusqu'à l'utilisateur.
    Journalise un avertissement si elle est seulement tronquée — la réponse partielle
    peut rester utile, mais l'exploitant doit le savoir.
    """
    try:
        choix = reponse_json["choices"][0]
        contenu = choix["message"].get("content") or ""
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(f"Réponse {backend} inattendue : {e}") from e

    if choix.get("finish_reason") == "length":
        if not contenu.strip():
            raise LLMError(
                f"{backend} : réponse vide, budget de tokens épuisé "
                f"(max_tokens={max_tokens}). Un modèle à raisonnement peut consommer "
                "tout le budget avant d'émettre sa réponse — augmenter max_tokens."
            )
        logger.warning(
            "%s : réponse tronquée (max_tokens=%s atteint, %d caractères rendus)",
            backend, max_tokens, len(contenu),
        )
    return contenu
