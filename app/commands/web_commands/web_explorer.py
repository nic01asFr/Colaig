"""
Commande d'exploration de liens web avec extraction de contenu enrichie.
"""

import asyncio
import time
from typing import Optional, Dict, Any

from app.matrix_bot.config import logger
from app.core_llm import AlbertApiClient
from app.services.web_cache import WebCache

# Import de l'extraction enrichie pour supporter le contenu dynamique
from app.services.enhanced_browser_extraction import extract_web_content_enhanced as extract_web_content

async def explore_link_impl(link, matrix_client, room_id, event_id, config, webdav_service=None, is_silent=False):
    """
    Implémentation de la commande explorer_lien qui extrait et résume le contenu d'un site web.
    Utilise browser-use pour l'extraction avec navigation complète.
    
    Args:
        link: URL à explorer
        matrix_client: Client Matrix connecté
        room_id: ID du salon où la commande a été lancée
        event_id: ID de l'événement de la commande
        config: Configuration de l'application
        webdav_service: Service WebDAV préconfiguré (optionnel)
        is_silent: Si True, n'envoie pas de messages de progression
    """
    try:
        # Si WebDAV n'est pas fourni, l'initialiser
        if webdav_service is None:
            from app.services.webdav import WebDAVService, initialize_webdav_service
            webdav_service = await initialize_webdav_service(config)
            
        # 1. Vérifier le format de l'URL
        if not link.startswith(('http://', 'https://')):
            link = 'https://' + link
            
        # Vérifier si le lien existe dans le cache
        web_cache = WebCache(webdav_service)
        cached_result = await web_cache.get_cached_result(link)
        
        if cached_result:
            logger.info(f"Utilisation du résultat en cache pour {link}")
            if not is_silent:
                await matrix_client.react_to_event(room_id, event_id, "✅")
                await matrix_client.send_markdown_message(
                    room_id, 
                    f"**Exploration de {link}**\n\n{cached_result}",
                    msgtype="m.notice"
                )
            return cached_result
        
        # 2. Message de démarrage
        if not is_silent:
            await matrix_client.react_to_event(room_id, event_id, "🔍")
            await matrix_client.send_markdown_message(
                room_id, 
                f"Exploration du lien {link}...",
                msgtype="m.notice"
            )
            
        # 3. Extraire le contenu avec browser-use
        logger.info(f"Extraction du contenu de {link} avec browser-use")
        try:
            # Setup de l'agent browser-use
            response = await extract_web_content(link, config)
            
            content = response.get("content", "")
            if not content:
                raise ValueError("Aucun contenu extrait")
                
            # Si le contenu est trop long, le tronquer
            if len(content) > 12000:
                content = content[:12000] + "...\n\n[Contenu tronqué car trop long]"
                
            # 4. Générer un résumé avec Albert
            prompt = f"""Tu es un assistant qui résume efficacement le contenu d'une page web.
            
Voici le contenu extrait de {link} :

---
{content}
---

Fais-en un résumé clair et concis qui capture les informations essentielles.
Organise le résumé en sections si c'est pertinent.
Ton résumé ne doit pas dépasser 500 mots. Ne mentionne pas que tu es un modèle d'IA."""
            
            # Générer le résumé
            aclient = AlbertApiClient(base_url=config.albert_api_url, api_key=config.albert_api_token)
            summary = await aclient.generate(
                model=config.albert_model,
                messages=[{"role": "user", "content": prompt}]
            )
            
            # 5. Mettre en cache le résultat
            await web_cache.cache_result(link, summary)
            
            # 6. Envoyer le résumé
            if not is_silent:
                await matrix_client.react_to_event(room_id, event_id, "✅")
                await matrix_client.send_markdown_message(
                    room_id, 
                    f"**Exploration de {link}**\n\n{summary}",
                    msgtype="m.notice"
                )
            
            # Nettoyage
            await aclient.close()
            return summary
            
        except Exception as e:
            logger.error(f"Erreur lors de l'extraction avec browser-use: {str(e)}")
            logger.error(f"Erreur browser-use: {str(e)}")
            
            if not is_silent:
                await matrix_client.react_to_event(room_id, event_id, "❌")
                await matrix_client.send_markdown_message(
                    room_id,
                    f"⚠️ Erreur lors de l'exploration du lien: {str(e)}",
                    msgtype="m.notice"
                )
            return None

    except Exception as e:
        logger.error(f"Erreur lors de l'exploration du lien: {str(e)}")
        if not is_silent:
            await matrix_client.react_to_event(room_id, event_id, "❌")
            await matrix_client.send_markdown_message(
                room_id,
                f"⚠️ Erreur lors de l'exploration du lien: {str(e)}",
                msgtype="m.notice"
            )
        return None 