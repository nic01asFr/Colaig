"""
context/conversation_memory.py — Mémoire conversationnelle contextualisée.

Remplace la récupération naïve "derniers N messages" par une récupération
sémantique : les 3 derniers messages (contexte récent) + top-N messages
les plus proches de la requête courante par similarité cosinus.

Si aucun service d'embedding n'est fourni, retombe sur le comportement
existant (derniers max_messages messages).

Stockage : JSON via StorageProtocol, chemin .colaig/conversations/{id}.json
(identique à layers.py — pas de nouvelle structure de fichier).
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
from datetime import datetime

from colaig import paths

logger = logging.getLogger(__name__)

# Au-dela, les verrous des conversations inactives sont relaches.
_MAX_VERROUS = 256

# Toujours inclure les N derniers messages pour le contexte récent
ALWAYS_INCLUDE_RECENT = 3


def _sanitize_id(identifier: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", identifier)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Similarité cosinus entre deux vecteurs."""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class ConversationMemory:
    """Mémoire conversationnelle avec récupération sémantique optionnelle.

    Usage :
        memory = ConversationMemory(storage, albert_client)
        history = await memory.load_relevant_history(
            "/espace/", "room-123", "procédure de validation", max_messages=10
        )
        updated = await memory.save_turn(
            "/espace/", "room-123", "question", "réponse", history
        )

    Si embedding_service est None (ou si l'historique tient dans max_messages),
    retourne simplement les derniers max_messages messages (comportement identique
    à load_conversation_history() dans layers.py).
    """

    def __init__(
        self,
        storage,
        embedding_service=None,  # EmbeddingServiceProtocol (objet avec .embed_text())
        max_stored: int = 100,
        max_retrieved: int = 10,
    ) -> None:
        self._storage = storage
        self._embedding_service = embedding_service
        self._max_stored = max_stored
        self._max_retrieved = max_retrieved
        # Un verrou PAR CONVERSATION — voir `save_turn`.
        self._verrous: dict[str, asyncio.Lock] = {}

    # ------------------------------------------------------------------
    # API publique

    async def load_relevant_history(
        self,
        workspace_path: str,
        conversation_id: str,
        current_query: str,
        max_messages: int | None = None,
    ) -> list[dict]:
        """Charge l'historique contextuellement pertinent.

        Stratégie :
        1. Charge l'historique complet depuis le storage.
        2. Toujours inclure les ALWAYS_INCLUDE_RECENT derniers messages.
        3. Si embedding_service disponible ET historique > limit :
           - Embed current_query
           - Score les messages restants par similarité cosinus
           - Sélectionne top (limit - ALWAYS_INCLUDE_RECENT) plus pertinents
           - Fusionne + déduplique + trie chronologiquement
        4. Sinon : derniers limit messages (comportement actuel).

        Args:
            workspace_path: Chemin du workspace dans le storage.
            conversation_id: Identifiant de la conversation.
            current_query: Requête courante (pour la recherche sémantique).
            max_messages: Nombre max de messages à retourner (défaut : max_retrieved).

        Returns:
            Liste de messages triés chronologiquement.
        """
        limit = max_messages if max_messages is not None else self._max_retrieved
        full_history = await self._load_full_history(workspace_path, conversation_id)

        if not full_history:
            return []

        if len(full_history) <= limit:
            # Tout tient — pas besoin de filtrer
            return full_history

        # Séparation : récents (toujours inclus) et anciens (filtrés)
        recent = full_history[-ALWAYS_INCLUDE_RECENT:]
        older = full_history[:-ALWAYS_INCLUDE_RECENT]

        remaining_slots = limit - len(recent)

        if remaining_slots <= 0 or not older:
            return recent

        # Récupération sémantique si embedding disponible et requête non vide
        if self._embedding_service and current_query.strip():
            selected = await self._semantic_select(older, current_query, remaining_slots)
        else:
            # Pas d'embedding : derniers N messages des anciens
            selected = older[-remaining_slots:]

        # Fusion, déduplication, tri chronologique
        combined = self._merge_deduplicate(selected, recent, full_history)
        return combined[:limit]

    async def save_turn(
        self,
        workspace_path: str,
        conversation_id: str,
        user_message: str,
        assistant_response: str,
        existing_history: list[dict],
    ) -> list[dict]:
        """Sauvegarde un tour de conversation.

        Ajoute user + assistant à l'historique, tronque à max_stored, sauvegarde.

        Args:
            workspace_path: Chemin du workspace.
            conversation_id: Identifiant de la conversation.
            user_message: Message de l'utilisateur.
            assistant_response: Réponse de l'assistant.
            existing_history: Historique récupéré avant ce tour.

        Returns:
            Historique mis à jour.
        """
        now_ts = datetime.utcnow().isoformat()
        new_messages = [
            {"role": "user", "content": user_message, "ts": now_ts},
            {"role": "assistant", "content": assistant_response, "ts": now_ts},
        ]

        # LECTURE-MODIFICATION-ÉCRITURE, sous verrou (L2.6).
        #
        # Sans lui, deux tours simultanés dans la même conversation chargent tous deux
        # le même historique, ajoutent chacun le leur, et le second écrase : **un tour
        # disparaît**. Deux messages envoyés coup sur coup y suffisent.
        #
        # Le verrou est PAR CONVERSATION : un verrou global ferait qu'une conversation
        # lente retienne toutes les autres — ce qui échangerait une perte de données
        # contre une panne de débit.
        #
        # Il vit ICI et non dans le gestionnaire de messages, parce que c'est ici qu'est
        # la course : le planificateur de tâches appelle aussi `save_turn`.
        #
        # LIMITE ASSUMÉE — le verrou est propre au PROCESSUS. Le déploiement Helm pose
        # `replicaCount: 1`, donc il suffit aujourd'hui. À deux répliques, la course
        # reviendrait **sans que rien ne le signale** : il faudrait alors un contrôle
        # optimiste par etag (`StorageProtocol.get_etag` existe) plutôt qu'un verrou.
        # TODO-HAUTE : si `replicaCount` passe à plus de 1, ce verrou ne protège plus.
        cle = f"{workspace_path}::{conversation_id}"
        verrou = self._verrous.get(cle)
        if verrou is None:
            verrou = self._verrous.setdefault(cle, asyncio.Lock())

        async with verrou:
            # Reconstituer depuis l'historique complet pour ne pas perdre l'historique
            # tronqué
            full_history = await self._load_full_history(workspace_path, conversation_id)
            updated = full_history + new_messages

            # Tronquer aux max_stored derniers messages
            if len(updated) > self._max_stored:
                updated = updated[-self._max_stored:]

            await self._save_full_history(workspace_path, conversation_id, updated)

        # Les verrous des conversations inactives sont relâchés : un dictionnaire qui ne
        # se vide jamais est une fuite lente sur une instance qui sert des milliers de
        # salons. On ne retire QUE les verrous libres — jamais un verrou derrière lequel
        # quelqu'un attend.
        if len(self._verrous) > _MAX_VERROUS:
            for autre, v in list(self._verrous.items()):
                if autre != cle and not v.locked():
                    del self._verrous[autre]

        return updated

    # ------------------------------------------------------------------
    # Internals

    async def _load_full_history(self, workspace_path: str, conversation_id: str) -> list[dict]:
        """Charge l'historique complet depuis le storage (sans limite)."""
        if not workspace_path:
            return []
        safe_id = _sanitize_id(conversation_id)
        history_path = paths.conversation_file(workspace_path, safe_id)
        try:
            content = await self._storage.download(history_path)
            data = json.loads(content.decode("utf-8"))
            if isinstance(data, list):
                return data
        except Exception:
            pass
        return []

    async def _save_full_history(
        self, workspace_path: str, conversation_id: str, messages: list[dict]
    ) -> None:
        """Sauvegarde l'historique complet sur le storage."""
        if not workspace_path:
            return
        safe_id = _sanitize_id(conversation_id)
        conv_dir = paths.conversations_dir(workspace_path)
        history_path = f"{conv_dir}{safe_id}.json"
        try:
            await self._storage.mkdir(conv_dir)
            content = json.dumps(messages, ensure_ascii=False, indent=2).encode("utf-8")
            await self._storage.upload(history_path, content)
        except Exception:
            logger.exception("impossible de sauvegarder l'historique: %s", history_path)

    async def _semantic_select(
        self, candidates: list[dict], query: str, k: int
    ) -> list[dict]:
        """Sélectionne les k messages les plus proches de la requête.

        Embed la requête + tous les candidats en un seul appel batch (embed_texts),
        puis score par similarité cosinus. L'EmbeddingService met en cache les
        vecteurs → aucun ré-appel Albert si le contenu n'a pas changé.

        Graceful degradation : si l'embedding échoue → last-k candidats.
        """
        try:
            query_vec = await self._embedding_service.embed_text(query)
        except Exception:
            logger.warning("embedding échoué pour requête, fallback last-k")
            return candidates[-k:]

        texts = [f"{msg.get('role', '')}: {msg.get('content', '')}" for msg in candidates]
        try:
            msg_vecs = await self._embedding_service.embed_texts(texts)
        except Exception:
            logger.warning("embedding batch échoué pour historique, fallback last-k")
            return candidates[-k:]

        scored: list[tuple[int, float]] = [
            (i, _cosine_similarity(query_vec, vec))
            for i, vec in enumerate(msg_vecs)
        ]

        # Tri par score décroissant, sélection top-k
        scored.sort(key=lambda x: x[1], reverse=True)
        top_indices = {idx for idx, _ in scored[:k]}
        return [candidates[i] for i in sorted(top_indices)]

    def _merge_deduplicate(
        self, selected: list[dict], recent: list[dict], full_history: list[dict]
    ) -> list[dict]:
        """Fusionne les messages sélectionnés et récents, déduplique, trie chronologiquement."""
        # Calculer les indices dans full_history pour chaque message (pour tri chron.)
        index_map: dict[int, dict] = {}
        id_to_index: dict[int, int] = {id(m): i for i, m in enumerate(full_history)}

        for msg in selected + recent:
            idx = id_to_index.get(id(msg))
            if idx is not None:
                index_map[idx] = msg

        return [index_map[i] for i in sorted(index_map.keys())]
