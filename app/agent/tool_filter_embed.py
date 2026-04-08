# SPDX-License-Identifier: MIT
"""
Filtrage des outils par embeddings sémantiques.

Pour chaque outil, on calcule un embedding de sa description (cache permanent).
À chaque message utilisateur, on embedde la requête et on garde les top-K
outils par similarité cosine.

Avantages vs filtrage par mots-clés (P3) :
- Sémantique : "stats sur les communes" → datagouv__search_datasets
  (sans avoir besoin que "data.gouv" soit dans le message)
- Robuste aux variations de formulation
- Pas de maintenance manuelle de listes de mots-clés

Coûts :
- 1 appel embedding par session (~50-100 ms via Albert API)
- Cache des embeddings des outils en RAM (négligeable)

Le filtrage par mots-clés et par embeddings sont **complémentaires** :
on peut faire mots-clés d'abord (rapide, gratuit) et embeddings en fallback
si trop d'outils restent.
"""
from __future__ import annotations

import math
import logging
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger("colaig.agent.tool_filter_embed")


# Cache global des embeddings des outils
# Clé : tool_name → embedding (list[float])
_tool_embeddings_cache: Dict[str, List[float]] = {}


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Similarité cosine entre deux vecteurs."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


async def _get_embedding(text: str, config) -> Optional[List[float]]:
    """Récupère l'embedding d'un texte via Albert API."""
    try:
        from app.core_llm import AlbertApiClient
        aclient = AlbertApiClient(
            base_url=config.albert_api_url,
            api_key=config.albert_api_token,
        )
        try:
            embeddings = await aclient.get_embeddings(
                texts=[text],
                model=config.albert_model_embedding,
            )
            if embeddings and len(embeddings) > 0:
                return embeddings[0]
        finally:
            await aclient.close()
    except Exception as e:
        logger.debug(f"Embedding échoué: {e}")
    return None


async def precompute_tool_embeddings(tools: list, config) -> None:
    """Pré-calcule les embeddings de tous les outils non encore en cache.

    Args:
        tools: Liste de ToolDef.
        config: Config Colaig (pour Albert API).
    """
    to_compute: List[Tuple[str, str]] = []
    for tool in tools:
        if tool.name not in _tool_embeddings_cache:
            # Texte représentatif : nom + description
            text = f"{tool.name}: {tool.description}"
            to_compute.append((tool.name, text))

    if not to_compute:
        return

    try:
        from app.core_llm import AlbertApiClient
        aclient = AlbertApiClient(
            base_url=config.albert_api_url,
            api_key=config.albert_api_token,
        )
        try:
            texts = [t[1] for t in to_compute]
            embeddings = await aclient.get_embeddings(
                texts=texts,
                model=config.albert_model_embedding,
            )
            for (name, _), emb in zip(to_compute, embeddings):
                _tool_embeddings_cache[name] = emb
            logger.info(
                f"[TOOL-EMBED] Pré-calcul de {len(to_compute)} embeddings d'outils"
            )
        finally:
            await aclient.close()
    except Exception as e:
        logger.warning(f"[TOOL-EMBED] Échec pré-calcul: {e}")


def _get_gateway_tools() -> set:
    """Charge les gateway_tools depuis le YAML config (dynamique)."""
    from app.agent.config_loader import get_filtering_config
    return set(get_filtering_config().get("gateway_tools", []))


async def filter_tools_by_embeddings(
    message: str,
    tools: list,
    config,
    top_k: int = 6,
    min_similarity: float = 0.25,
) -> List[str]:
    """Filtre les outils par similarité sémantique avec le message.

    Args:
        message: Texte utilisateur.
        tools: Liste de ToolDef disponibles.
        config: Config Colaig.
        top_k: Nombre maximum d'outils à retourner.
        min_similarity: Seuil minimum de similarité (0-1).

    Returns:
        Liste des noms d'outils retenus, triés par pertinence.
        Garantit qu'au moins un outil de recherche (gateway) est inclus.
    """
    if not message or not tools:
        return [t.name for t in tools]

    # S'assurer que tous les outils ont un embedding
    await precompute_tool_embeddings(tools, config)

    # Embedding du message
    msg_embedding = await _get_embedding(message, config)
    if not msg_embedding:
        return [t.name for t in tools]

    # Calcul des similarités
    scored: List[Tuple[float, str]] = []
    for tool in tools:
        tool_emb = _tool_embeddings_cache.get(tool.name)
        if not tool_emb:
            scored.append((1.0, tool.name))
            continue
        sim = _cosine_similarity(msg_embedding, tool_emb)
        scored.append((sim, tool.name))

    # Tri décroissant
    scored.sort(reverse=True)

    # Garder top_k au-dessus du seuil
    kept = [name for sim, name in scored[:top_k] if sim >= min_similarity]

    # Failsafe si rien ne dépasse le seuil
    if not kept:
        kept = [name for _, name in scored[:top_k]]

    # GARANTIE : injecter les gateway tools s'ils sont dans la liste candidate
    # mais absents du résultat. Ils prennent la place des moins pertinents.
    gateway_tools = _get_gateway_tools()
    available_gateways = {t.name for t in tools} & gateway_tools
    missing_gateways = available_gateways - set(kept)
    if missing_gateways:
        # Évincer les outils les moins pertinents pour faire de la place
        for gw in missing_gateways:
            if gw not in kept:
                if len(kept) >= top_k:
                    kept.pop()  # Retire le moins pertinent
                kept.append(gw)

    logger.info(
        f"[TOOL-EMBED] {len(scored)} → {len(kept)} outils "
        f"(scores: {[(round(s, 2), n) for s, n in scored[:top_k]]})"
    )
    return kept
