"""
Module pour l'interaction avec les LLMs pour les commandes adaptées.
Implémente les fonctions nécessaires pour docquery_adapted et attachment_adapted.
"""

import asyncio
from dataclasses import dataclass
from typing import List, Dict, Any, Optional

from matrix_bot.config import logger

from app.core_llm import AlbertApiClient


@dataclass
class QueryPlan:
    """Plan de réponse à une requête documentaire."""
    needs_reasoning: bool = True
    factual_query: bool = True
    query_restatement: str = ""
    multi_part_response: bool = False
    needs_sources: bool = True


async def infer_query_plan(config: Any, query: str, contexts: List[str]) -> QueryPlan:
    """
    Détermine la stratégie de réponse en fonction de la requête et des contextes.
    
    Args:
        config: Configuration du bot
        query: Question de l'utilisateur
        contexts: Contextes documentaires pertinents
        
    Returns:
        Un plan d'inférence QueryPlan
    """
    logger.info(f"[LLM] Inférence du plan de réponse pour la requête: '{query}'")
    
    # Par défaut, on considère que la plupart des requêtes nécessitent:
    # - Un raisonnement
    # - Une réponse factuelle
    # - Des sources
    return QueryPlan(
        needs_reasoning=True,
        factual_query=True,
        query_restatement=query,
        multi_part_response=False,
        needs_sources=True
    )


async def format_answer(
    config: Any,
    query: str,
    contexts: List[str],
    query_plan: QueryPlan
) -> str:
    """
    Génère une réponse à partir des contextes documentaires et du plan de requête.
    
    Args:
        config: Configuration du bot
        query: Question de l'utilisateur
        contexts: Contextes documentaires pertinents
        query_plan: Plan d'inférence pour orienter la réponse
        
    Returns:
        La réponse formatée
    """
    logger.info(f"[LLM] Génération de réponse pour la requête: '{query}'")
    
    try:
        # Utilisation du même style que core_llm
        api_key = config.albert_api_token
        url = config.albert_api_url
        model = config.albert_model
        
        # Créer le client
        client = AlbertApiClient(base_url=url, api_key=api_key)
        
        # Construire le prompt
        system_prompt = """Tu es Albert, un assistant documentaire. Tu dois répondre à la question de l'utilisateur en te basant uniquement sur les extraits fournis.
Sois factuel et précis dans ta réponse. Si tu ne trouves pas l'information dans les extraits, indique-le clairement.
N'invente jamais d'informations qui ne sont pas dans les extraits.

Instructions de formatage importantes:
- Pour structurer ta réponse, utilise des titres avec des caractères **gras** plutôt que des numéros
- Quand tu présentes plusieurs points sur un sujet, utilise toujours des listes à puces avec le symbole "-" 
- N'utilise jamais de chiffres suivis d'un point (comme "1.") au début des lignes car cela sera interprété comme une liste numérotée
- Évite de débuter des paragraphes par des nombres suivis d'un point
- Si tu dois mentionner un nombre au début d'un paragraphe, ajoute du texte avant ou utilise une puce"""
        
        # Préparer le contexte
        context_text = "\n\n---\n\n".join([f"Extrait {i+1}:\n{context}" for i, context in enumerate(contexts)])
        
        # Construire les messages
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"""Voici ma question : {query}

Voici les extraits de documents pertinents :

{context_text}

Réponds à ma question en te basant uniquement sur ces extraits."""}
        ]
        
        # Générer la réponse
        response = await client.generate(
            model=model,
            messages=messages,
            temperature=0.3,
            max_tokens=1000
        )
        
        # Fermer le client
        await client.close()
        
        return response
        
    except Exception as e:
        logger.error(f"[LLM] Erreur lors de la génération de la réponse: {str(e)}")
        return f"""Je ne parviens pas à générer une réponse complète en ce moment.
Voici néanmoins les extraits pertinents que j'ai trouvés:

{contexts[0][:300]}...""" 