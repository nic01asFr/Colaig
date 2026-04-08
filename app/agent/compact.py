# SPDX-License-Identifier: MIT
"""
Compaction des résultats d'outils trop longs avant injection LLM.

Principe clé : la compaction s'applique aux **résultats de recherche/listing**
(centaines d'items dont on ne garde que les premiers), JAMAIS aux récupérations
ciblées (get_dataset_info, query_resource_data, etc.) où chaque champ compte.

La distinction se fait via le paramètre `compactable` du wrapper.

Trois stratégies, du moins coûteux au plus coûteux :

1. **Pass-through** : si non compactable OU ≤ MAX_INLINE chars.
2. **Troncature structurée** (≤ MAX_LLM_SUMMARY) : garde le head et la tail.
3. **Résumé LLM** (> MAX_LLM_SUMMARY, opt-in) : appel LLM intermédiaire léger.

Pour les listes triées (search_datasets retourne déjà par pertinence),
la troncature de tête suffit. Pour les détails d'un dataset, on garde tout.
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger("colaig.agent.compact")

def _cfg() -> dict:
    """Charge la config compaction depuis YAML (dynamique)."""
    from app.agent.config_loader import get_compaction_config
    return get_compaction_config()


def _get(key: str, default):
    return _cfg().get(key, default)


def compact_tool_result(result: str, compactable: bool = True) -> str:
    """Compacte un résultat d'outil si trop long.

    Args:
        result: Texte brut à compacter.
        compactable: Si False, le résultat n'est jamais compacté
            (sauf hard cap de sécurité).

    Returns:
        Le résultat tel quel si non compactable (avec hard cap si
        absurdement long), sinon une troncature structurée.
    """
    if not result:
        return result

    max_inline = _get("max_inline", 1500)
    max_hard_cap = _get("max_hard_cap", 50000)

    if not compactable:
        if len(result) > max_hard_cap:
            return result[:max_hard_cap] + (
                f"\n\n[... {len(result) - max_hard_cap} chars supplémentaires "
                "tronqués pour respecter la limite de contexte]"
            )
        return result

    if len(result) <= max_inline:
        return result

    return _truncate_structured(result)


async def compact_tool_result_async(
    result: str,
    config=None,
    use_llm_summary: bool = False,
    context_query: Optional[str] = None,
) -> str:
    """Variante async qui peut utiliser un résumé LLM pour les très gros résultats."""
    max_inline = _get("max_inline", 1500)
    max_llm_summary = _get("max_llm_summary", 8000)

    if not result or len(result) <= max_inline:
        return result

    if use_llm_summary and config and len(result) > max_llm_summary:
        try:
            summary = await _llm_summarize(result, config, context_query)
            if summary:
                return summary
        except Exception as e:
            logger.warning(f"[COMPACT] LLM summary échoué, fallback troncature: {e}")

    return _truncate_structured(result)


def _truncate_structured(result: str) -> str:
    """Troncature structurée : head + note + tail."""
    head_lines = _get("head_lines", 25)
    tail_lines = _get("tail_lines", 3)
    max_inline = _get("max_inline", 1500)

    lines = result.split("\n")
    total_lines = len(lines)

    if total_lines <= head_lines + tail_lines + 2:
        return result[:max_inline] + "\n\n[... résultat tronqué]"

    head = "\n".join(lines[:head_lines])
    tail = "\n".join(lines[-tail_lines:])
    omitted = total_lines - head_lines - tail_lines

    return (
        head
        + f"\n\n[... {omitted} lignes supplémentaires omises pour économiser le contexte. "
        + "Affine ta requête pour cibler un sous-ensemble si besoin.]\n\n"
        + tail
    )


async def _llm_summarize(
    result: str,
    config,
    context_query: Optional[str] = None,
) -> Optional[str]:
    """Demande à un mini-LLM de résumer un listing long en gardant la diversité.

    Le résumé doit :
    - Annoncer le nombre total de résultats
    - Identifier les catégories principales (par organisation, thème, type)
    - Donner 3-5 exemples notables avec leurs IDs
    - Préserver les identifiants pour permettre des appels get_* ultérieurs
    """
    from app.core_llm import generate

    user_context = (
        f"\n\nQuestion d'origine de l'utilisateur : « {context_query} »"
        if context_query else ""
    )

    system = (
        "Tu es un agrégateur de résultats. On te donne la sortie brute d'un "
        "outil de recherche qui peut contenir des dizaines voire centaines "
        "d'entrées. Ta mission : produire un résumé structuré qui :\n\n"
        "1. Indique le **nombre total** d'entrées trouvées (cherche-le dans le texte).\n"
        "2. Identifie 3-6 **catégories principales** (par organisation, thème, type) "
        "avec un comptage approximatif pour chacune.\n"
        "3. Liste 5-8 **entrées notables** (les plus diverses, pas seulement les premières) "
        "avec leur titre, organisation et **identifiant complet** (ID) pour permettre "
        "des recherches détaillées ultérieures.\n"
        "4. Termine par une note invitant à raffiner la recherche si trop de résultats.\n\n"
        "Format Markdown. Préserve TOUS les identifiants techniques (ID, codes). "
        "Maximum 1500 caractères."
        + user_context
    )

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": result[:20000]},
    ]

    summary = await generate(config, messages, force_norag=True)
    return summary or None

