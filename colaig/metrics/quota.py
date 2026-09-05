"""
Colaig — point de passage unique du contrôle de quota LLM.

STATUT: COMPLET
VERSION: 2026-08-25 - v1.0
LOT: L2.6

Le défaut que ce module ferme
------------------------------
`docs/SECURITE.md` §8 annonce comme mitigation du déni de service et du coût : « quotas
journaliers par tenant ». Mesuré en D46, le contrôle n'existait que dans
`integrations/albert.py` — zéro dans `openai_client`, `azure_client` et `ollama_client`.

Or la cible de production est **SSPCloud, endpoint OpenAI-compatible** (`CLAUDE.md` §3).
**Le quota ne s'appliquait pas là où il compte.**

Quatrième occurrence du motif « écrit et non branché » dans ce dépôt, après
`sanitize_description` définie et jamais appelée, `storage_readonly` honoré par un site
sur vingt, et `TaskExecutor` jamais branché sur le chemin Matrix.

Pourquoi un point unique et non quatre copies
-----------------------------------------------
Recopier le contrôle produirait quatre versions qui divergeront. Ce chantier a mesuré
cinq fois ce que coûte une fonction dupliquée — cinq copies d'un motif d'en-tête, chacune
ayant produit une mesure fausse avant d'être trouvée.

Un test de portée dépôt refuse qu'un client LLM existe sans passer par ici, et un autre
refuse qu'un client garde une copie privée.

Une échappatoire assumée
--------------------------
Sans suivi d'usage configuré, rien ne bloque. Contrairement aux quatre gardes recensées
en D44, celle-ci ne protège pas un **accès** mais un **coût** : un déploiement qui n'a
pas configuré de quota n'a exprimé aucune limite à faire respecter. L'analogie s'arrête
là, et c'est pour cela qu'elle est écrite.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def verifier_quota(tracker, client_id: str) -> None:
    """Refuse l'appel si le quota du tenant est dépassé.

    Args:
        tracker: un `UsageTracker`, ou `None` si aucun suivi n'est configuré.
        client_id: le tenant. Vide pour un déploiement mono-client.

    Raises:
        QuotaExceededError: quota dépassé — le motif rendu par le tracker est repris,
            pour que l'exploitant sache **laquelle** des limites a été atteinte.
    """
    if tracker is None:
        return
    autorise, motif = tracker.check_quota(client_id)
    if autorise:
        return

    from colaig.exceptions import QuotaExceededError

    raise QuotaExceededError(f"client '{client_id or 'default'}': {motif}")


def enregistrer_usage(tracker, client_id: str, donnees: dict | None) -> None:
    """Comptabilise les jetons consommés par une réponse OpenAI-compatible.

    Un compteur qui tombe ne doit **jamais** faire tomber la réponse à l'utilisateur :
    ce serait échanger un incident de métrique contre une panne de service. L'échec est
    donc journalisé et avalé.
    """
    if tracker is None:
        return
    try:
        tracker.record_from_usage(client_id, (donnees or {}).get("usage"))
    except Exception as exc:  # noqa: BLE001
        logger.debug("suivi d'usage indisponible (%s) — l'appel n'en dépend pas", exc)
