"""
Commandes pour la recherche d'informations sur internet.

Ce module fournit des commandes pour:
- Rechercher des informations sur internet via browser-use
- Gérer une base de données de liens organisés par catégories
- Explorer et naviguer sur le web via des résumés de contenu
"""

import json
import asyncio
import aiohttp
import re
import time
from typing import Dict, List, Any
import urllib.parse

from app.matrix_bot.client import MatrixClient
from app.matrix_bot.config import logger
from app.matrix_bot.eventparser import EventParser
from nio.events.room_events import RoomMessageText

from app.commands.decorators import albert_command, albert_thread_command
from app.commands import get_unified_session_context, update_conversation_history
from app.services.web_links_manager import get_web_links_manager
from app.services.web_classification import classify_url_content, get_page_title, get_page_description
from app.services.web_search_cache import get_web_search_cache
from app.core_llm import AlbertApiClient
from app.services.browser_extraction import extract_web_content, extract_structured_web_data
from app.services.web_classification_browser import (
    classify_url_content_with_browser, 
    get_page_title_with_browser, 
    get_page_description_with_browser,
    extract_page_metadata_with_browser
)
from app.services.web_manager import WebManager

@albert_thread_command(
    thread_name="recherche_web",
    group="web",
    command="recherche_web",
    help_text="!recherche_web [question] - Rechercher des informations sur Internet",
    preserve_context=True,
    timeout=120.0  # 2 minutes maximum
)
async def web_search_command(ep: EventParser, matrix_client: MatrixClient):
    """Recherche des informations sur Internet via l'API Albert avec mise en cache des résultats."""
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Extraire la requête
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    command_parts = message_text.split(maxsplit=1)
    
    # Si le message est uniquement "!recherche_web" sans arguments
    if len(command_parts) <= 1:
        await matrix_client.send_markdown_message(
            room_id,
            """❓ **Comment utiliser !recherche_web**

```
!recherche_web Votre question ou recherche
```

Je rechercherai des informations sur Internet et vous fournirai une réponse.""",
            msgtype="m.notice"
        )
        return
    
    # Extraire la requête
    prompt = command_parts[1]
    
    # Envoyer un message de chargement
    await matrix_client.send_markdown_message(
        room_id,
        f"🔍 Recherche en cours pour : *{prompt}*",
        msgtype="m.notice"
    )
    
    try:
        # Vérifier d'abord dans le cache
        web_search_cache = await get_web_search_cache(config)
        cached_result = await web_search_cache.get(prompt)
        
        if cached_result:
            logger.info(f"Résultat trouvé dans le cache pour: {prompt}")
            
            # Utiliser le résultat en cache
            formatted_response = cached_result.get("response", "")
            sources = cached_result.get("sources", [])
            
            # Indiquer que c'est un résultat en cache (optionnel)
            cached_time = cached_result.get("created_at", time.time())
            time_diff = int((time.time() - cached_time) / 3600)  # Heures
            
            if time_diff > 0:
                formatted_response += f"\n\n*Résultat mis en cache il y a {time_diff} heure(s)*"
            
            # Envoyer le résultat
            await matrix_client.send_markdown_message(
                room_id,
                formatted_response,
                msgtype="m.notice"
            )
            
            # Enregistrer dans l'historique de conversation
            await update_conversation_history(
                config,
                room_id,
                sender,
                user_message=prompt,
                bot_response=formatted_response
            )
            
            return
            
        # Si pas dans le cache, effectuer une nouvelle recherche
        api_key = config.albert_api_token
        url = config.albert_api_url
        aclient = AlbertApiClient(base_url=url, api_key=api_key)
        
        # Créer la session
        session = aiohttp.ClientSession()
        
        # Utiliser le format exact de requête comme documenté dans l'exemple
        data = {
            "collections": ["internet"], 
            "k": 4, 
            "prompt": prompt,
            "method": "semantic"  # Préciser explicitement la méthode de recherche
        }
        
        # Définir l'en-tête d'autorisation explicitement
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Recherche ciblée avec logs détaillés
        logger.info(f"Recherche en cours pour l'URL {url}")
        logger.info(f"API URL: {url}/search")
        logger.info(f"Requête: {json.dumps(data)}")
        
        try:
            response = await session.post(
                url=f"{url}/search", 
                json=data,
                headers=headers
            )
            
            # Log du statut de la réponse
            logger.info(f"Statut de la réponse: {response.status}")
            
            # Vérifier le statut de la réponse
            if response.status != 200:
                error_text = await response.text()
                logger.error(f"Erreur API {response.status}: {error_text}")
                await matrix_client.send_markdown_message(
                    room_id,
                    f"⚠️ Erreur lors de la recherche: L'API a retourné une erreur {response.status}",
                    msgtype="m.notice"
                )
                return
                
            search_results = await response.json()
            
            # Vérifier que la réponse contient la clé 'data'
            if "data" not in search_results:
                logger.error(f"Réponse API inattendue: {search_results}")
                await matrix_client.send_markdown_message(
                    room_id,
                    f"⚠️ Erreur: La recherche n'a pas retourné de résultats valides",
                    msgtype="m.notice"
                )
                return
                
            # Extraire les chunks et sources
            # Vérifier la structure de chaque résultat avant d'accéder aux champs
            chunks_list = []
            sources_set = set()
            
            for result in search_results["data"]:
                if "chunk" in result and "content" in result["chunk"]:
                    chunks_list.append(result["chunk"]["content"])
                    
                if "chunk" in result and "metadata" in result["chunk"] and "document_name" in result["chunk"]["metadata"]:
                    sources_set.add(result["chunk"]["metadata"]["document_name"])
            
            # Vérifier qu'on a des chunks à traiter
            if not chunks_list:
                logger.warning("Aucun chunk valide trouvé dans les résultats de recherche")
                await matrix_client.send_markdown_message(
                    room_id,
                    f"⚠️ La recherche n'a pas trouvé d'informations pertinentes pour: {prompt}",
                    msgtype="m.notice"
                )
                return
                
            chunks = "\n\n\n".join(chunks_list)
            sources = sources_set
            
            # Construire le prompt RAG
            prompt_template = """
Tu es un assistant IA spécialisé dans la recherche d'informations sur Internet.
Réponds à la question de l'utilisateur en te basant sur les informations suivantes.
Si tu ne peux pas répondre précisément à partir des informations fournies, indique-le clairement.

Question : {prompt}

Informations trouvées :
{chunks}

Réponds de façon claire, précise et structurée.
Cite les sources à la fin de ta réponse sous forme de liens.
"""
            
            rag_prompt = prompt_template.format(prompt=prompt, chunks=chunks)
            
            # Envoyer le prompt au modèle de langage
            response = await aclient.generate(
                model=config.albert_model,
                messages=[{"role": "user", "content": rag_prompt}]
            )
            
            # Formater la réponse avec les sources
            formatted_response = response
            
            # Ajouter les sources si elles ne sont pas déjà mentionnées
            if not any(re.search(re.escape(source), response, re.IGNORECASE) for source in sources):
                formatted_response += "\n\n**Sources :**\n"
                for source in sources:
                    formatted_response += f"- {source}\n"
            
            # Stocker dans le cache
            cache_data = {
                "response": formatted_response,
                "sources": list(sources),
                "chunks": chunks,
                "created_at": time.time()
            }
            
            await web_search_cache.set(prompt, cache_data)
            logger.info(f"Résultat mis en cache pour: {prompt}")
            
            # Envoyer la réponse
            await matrix_client.send_markdown_message(
                room_id,
                formatted_response,
                msgtype="m.notice"
            )
            
            # Enregistrer dans l'historique de conversation
            await update_conversation_history(
                config,
                room_id,
                sender,
                user_message=prompt,
                bot_response=formatted_response
            )
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche web: {str(e)}")
            await matrix_client.send_markdown_message(
                room_id,
                f"⚠️ Erreur lors de la recherche: {str(e)}",
                msgtype="m.notice"
            )
    
    finally:
        # Fermer les connexions
        if 'session' in locals():
            await session.close()
        if 'aclient' in locals():
            await aclient.close()

@albert_command(
    group="web",
    command="ajouter_lien",
    help_text="!ajouter_lien [URL] [titre?] [catégorie?] - Ajouter un lien à la base de données (titre et catégorie optionnels)",
    preserve_context=True,
    timeout=90.0
)
async def add_link_command(ep: EventParser, matrix_client: MatrixClient):
    """Ajoute un lien à la base de données avec classification automatique via browser-use et Albert API."""
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    
    try:
        # Extraire les arguments
        message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
        args = message_text.split(maxsplit=3)
        
        # Vérifier les arguments minimum (commande + URL)
        if len(args) < 2:
            await matrix_client.send_markdown_message(
                room_id,
                """❓ **Comment utiliser !ajouter_lien**

```
!ajouter_lien URL [titre] [catégorie] [description]
```

Si le titre ou la catégorie ne sont pas spécifiés, ils seront déterminés automatiquement.
Exemple : `!ajouter_lien https://www.service-public.fr`
Ou avec tous les détails : `!ajouter_lien https://www.service-public.fr "Service Public" administration "Site officiel de l'administration française"`""",
                msgtype="m.notice"
            )
            return
        
        # Extraire l'URL
        url = args[1]
        
        # Valider l'URL
        if not url.startswith(("http://", "https://")):
            await matrix_client.send_markdown_message(
                room_id,
                "⚠️ L'URL doit commencer par http:// ou https://",
                msgtype="m.notice"
            )
            return
        
        # Message de chargement
        await matrix_client.send_markdown_message(
            room_id,
            f"⏳ Analyse du lien {url} avec browser-use...",
            msgtype="m.notice"
        )
        
        # Vérifier le nombre d'arguments pour déterminer le comportement
        if len(args) == 2:  # Seulement l'URL
            # Extraction complète des métadonnées avec browser-use (plus efficace)
            try:
                metadata = await extract_page_metadata_with_browser(config, url)
                title = metadata.get("title", url.split("//")[-1].split("/")[0])
                category = metadata.get("category", "general")
                description = metadata.get("description", "Site web")
                content = metadata.get("content", "")
            except Exception as e:
                logger.error(f"Erreur lors de l'analyse automatique avec browser-use: {str(e)}")
                title = url.split("//")[-1].split("/")[0]  # Domaine comme titre par défaut
                category = "general"
                description = "Site web"
                content = None
        
        elif len(args) == 3:  # URL + titre
            title = args[2].strip('"\'')
            try:
                # Extraction avec browser-use pour catégorie et description
                metadata = await extract_page_metadata_with_browser(config, url)
                category = metadata.get("category", "general")
                description = metadata.get("description", "Site web")
                content = metadata.get("content", "")
            except Exception as e:
                logger.error(f"Erreur lors de la classification avec browser-use: {str(e)}")
                category = "general"
                description = "Site web"
                content = None
        
        elif len(args) == 4:  # URL + titre + catégorie
            title = args[2].strip('"\'')
            category = args[3].strip('"\'')
            try:
                # Extraction avec browser-use pour description
                result = await extract_web_content(
                    url=url,
                    config=config
                )
                content = result.get("content", "")
                
                # Utiliser Albert pour générer la description
                api_key = config.albert_api_token
                base_url = config.albert_api_url
                aclient = AlbertApiClient(base_url=base_url, api_key=api_key)
                
                description_prompt = f"""
À partir du contenu suivant extrait du site {url}, génère une description concise en 
une phrase de 15 mots maximum.

Contenu:
{content}

Réponds uniquement avec la description, sans introduction ni phrase supplémentaire.
"""
                
                description = await aclient.generate(
                    model=config.albert_model,
                    messages=[{"role": "user", "content": description_prompt}]
                )
                
                description = description.strip()
                await aclient.close()
                
            except Exception as e:
                logger.error(f"Erreur lors de la génération de la description: {str(e)}")
                description = "Site web"
                content = None
        
        else:  # URL + titre + catégorie + description
            title = args[2].strip('"\'')
            
            # Si le troisième argument est entre guillemets, traiter différemment
            category_desc = args[3]
            if category_desc.startswith('"') and '"' in category_desc[1:]:
                # Reconstruire la chaîne pour l'analyse correcte
                remaining = message_text.split(maxsplit=2)[2]
                parts = re.findall(r'("([^"]*)"|\S+)', remaining)
                
                if len(parts) >= 2:  # Au moins titre et catégorie
                    title = parts[0][1] if parts[0][0].startswith('"') else parts[0][0]
                    category = parts[1][1] if parts[1][0].startswith('"') else parts[1][0]
                    
                    if len(parts) >= 3:  # Description fournie
                        description = parts[2][1] if parts[2][0].startswith('"') else parts[2][0]
                        content = None  # Pas besoin d'extraire le contenu
            else:
                # Troisième argument simple
                category_parts = category_desc.split(maxsplit=1)
                category = category_parts[0].lower()
                description = category_parts[1].strip('"\'') if len(category_parts) > 1 else "Site web"
                content = None  # Pas besoin d'extraire le contenu
        
        # Ajouter le lien
        web_links_manager = await get_web_links_manager(config)
        success = await web_links_manager.add_link(url, title, category, description)
        
        if success:
            await matrix_client.send_markdown_message(
                room_id,
                f"✅ Lien ajouté avec succès:\n- **Titre**: {title}\n- **Catégorie**: {category}\n- **Description**: {description}",
                msgtype="m.notice"
            )
        else:
            await matrix_client.send_markdown_message(
                room_id,
                "❌ Erreur lors de l'ajout du lien",
                msgtype="m.notice"
            )
    
    except Exception as e:
        logger.error(f"Erreur lors de l'ajout du lien: {str(e)}")
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Erreur lors de l'ajout du lien: {str(e)}",
            msgtype="m.notice"
        )

@albert_command(
    group="web",
    command="liste_liens",
    help_text="!liste_liens [catégorie] - Afficher les liens par catégorie",
    preserve_context=True,
    timeout=30.0
)
async def list_links_command(ep: EventParser, matrix_client: MatrixClient):
    """Affiche les liens par catégorie."""
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    
    # Extraire la catégorie
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    args = message_text.split(maxsplit=1)
    
    category = None
    if len(args) > 1:
        category = args[1]
    
    # Récupérer les liens
    web_links_manager = await get_web_links_manager(config)
    links_by_category = await web_links_manager.get_links_by_category(category)
    
    if not links_by_category:
        await matrix_client.send_markdown_message(
            room_id,
            f"Aucun lien trouvé" + (f" dans la catégorie **{category}**" if category else ""),
            msgtype="m.notice"
        )
        return
    
    # Formater la réponse
    response = "📚 **Liste des liens**\n\n"
    
    for cat, links in links_by_category.items():
        if links:
            response += f"**{cat.upper()}**\n\n"
            for link in links:
                response += f"- [{link['title']}]({link['url']})"
                if link.get('description'):
                    response += f" - {link['description']}"
                response += "\n"
            response += "\n"
    
    await matrix_client.send_markdown_message(
        room_id,
        response,
        msgtype="m.notice"
    )

@albert_thread_command(
    thread_name="explorer_lien",
    group="web",
    command="explorer_lien",
    help_text="!explorer_lien [URL] - Explorer et résumer le contenu d'un lien",
    preserve_context=True,
    timeout=120.0
)
async def explore_link_command(ep: EventParser, matrix_client: MatrixClient):
    """
    Explore et analyse le contenu d'une URL à l'aide de browser-use, puis génère un résumé.
    Utilise l'extraction améliorée via WebManager.
    """
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    
    # Extraire la requête
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    command_parts = message_text.split(maxsplit=1)
    
    # Si le message est uniquement "!explorer_lien" sans arguments
    if len(command_parts) <= 1:
        await matrix_client.send_markdown_message(
            room_id,
            """❓ **Comment utiliser !explorer_lien**

```
!explorer_lien URL
```

J'explorerai le contenu de cette URL et vous fournirai un résumé.""",
            msgtype="m.notice"
        )
        return
    
    # Extraire l'URL
    url = command_parts[1].strip()
    
    # Vérifier le format de l'URL
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # Envoyer un message de chargement
    loading_message = await matrix_client.send_markdown_message(
        room_id,
        f"🔍 Exploration du lien {url} en cours...",
        msgtype="m.notice"
    )
    
    max_retries = 2
    retry_count = 0
    
    while retry_count <= max_retries:
        try:
            # Initialiser le WebManager pour utiliser browser-use
            web_manager = WebManager(config)
            
            # Extraire le contenu avec la détection automatique du type
            logger.info(f"Tentative d'extraction #{retry_count+1} pour {url}")
            web_result = await web_manager.extract_as_web_result(url)
            
            if not web_result.success:
                if retry_count < max_retries:
                    logger.warning(f"Échec de l'extraction #{retry_count+1}: {web_result.error}. Nouvelle tentative...")
                    retry_count += 1
                    continue
                else:
                    await matrix_client.send_markdown_message(
                        room_id,
                        f"⚠️ Erreur lors de l'exploration du lien après {max_retries+1} tentatives: {web_result.error}",
                        msgtype="m.notice"
                    )
                    return
            
            # Si nous sommes ici, l'extraction a réussi
            # Récupérer le contenu extrait
            content = web_result.content
            title = web_result.title
            content_type = web_result.content_type
            
            # Journal de débogage pour afficher ce qui a été extrait
            logger.info(f"Titre extrait: {title}")
            logger.info(f"Type de contenu détecté: {content_type}")
            logger.info(f"Longueur du contenu extrait: {len(content) if content else 0} caractères")
            
            # Afficher un aperçu du contenu pour le débogage
            if content:
                preview_length = min(500, len(content))
                logger.info(f"Aperçu du contenu extrait (500 premiers caractères): {content[:preview_length]}...")
            else:
                logger.info("Contenu extrait vide")
            
            # Vérifier que le contenu n'est pas vide
            if not content or content.strip() == "":
                if retry_count < max_retries:
                    logger.warning(f"Extraction #{retry_count+1} a produit un contenu vide. Nouvelle tentative...")
                    retry_count += 1
                    continue
                else:
                    await matrix_client.send_markdown_message(
                        room_id,
                        f"⚠️ Échec de l'extraction : le contenu de la page est vide après {max_retries+1} tentatives.",
                        msgtype="m.notice"
                    )
                    return
            
            # Si le contenu est trop long, le tronquer
            original_length = len(content)
            if len(content) > 12000:
                content = content[:12000] + "...\n\n[Contenu tronqué car trop long]"
                logger.info(f"Contenu tronqué de {original_length} à 12000 caractères")
            
            # Générer un résumé avec Albert
            aclient = AlbertApiClient(base_url=config.albert_api_url, api_key=config.albert_api_token)
            
            prompt = f"""Tu es un assistant qui résume efficacement le contenu d'une page web.
            
Voici le contenu extrait de {url} (type: {content_type}):

---
{content}
---

Fais-en un résumé clair et concis qui capture les informations essentielles.
Organise le résumé en sections si c'est pertinent.
Ton résumé ne doit pas dépasser 500 mots. Ne mentionne pas que tu es un modèle d'IA."""
            
            logger.info(f"Envoi d'une demande de résumé à l'API Albert avec {len(content)} caractères de contenu")
            
            summary_start_time = time.time()
            summary = await aclient.generate(
                model=config.albert_model,
                messages=[{"role": "user", "content": prompt}]
            )
            summary_duration = time.time() - summary_start_time
            
            logger.info(f"Résumé généré par Albert en {summary_duration:.2f} secondes")
            logger.info(f"Longueur du résumé: {len(summary)} caractères")
            logger.info(f"Aperçu du résumé: {summary[:min(300, len(summary))]}...")
            
            # Mettre en cache le résultat 
            # (Remarque: à implémenter éventuellement avec WebCache pour cohérence)
            
            # Formater le message avec le type de contenu
            content_type_emoji = {
                "article": "📄",
                "product": "🛒",
                "repository": "💻",
                "documentation": "📚",
                "webpage": "🌐"
            }.get(content_type, "🌐")
            
            content_type_label = {
                "article": "Article",
                "product": "Produit",
                "repository": "Dépôt de code",
                "documentation": "Documentation",
                "webpage": "Page web"
            }.get(content_type, "Page web")
            
            extraction_method = web_result.metadata.get("extraction_method", "Navigateur automatisé")
            
            # Envoyer le résumé
            await matrix_client.send_markdown_message(
                room_id,
                f"{content_type_emoji} **{title}**\n" +
                f"*{content_type_label} extrait par {extraction_method}*\n\n" +
                f"{summary}",
                msgtype="m.notice"
            )
            
            # Enregistrer dans l'historique de conversation
            await update_conversation_history(
                config,
                room_id,
                ep.sender,
                user_message=message_text,
                bot_response=summary
            )
            
            # Sortir de la boucle si l'extraction a réussi
            break
            
        except Exception as e:
            logger.error(f"Erreur lors de l'exploration du lien: {str(e)}")
            
            # Si nous avons encore des tentatives, réessayer
            if retry_count < max_retries:
                logger.warning(f"Échec de l'extraction #{retry_count+1} avec exception: {str(e)}. Nouvelle tentative...")
                retry_count += 1
                continue
            else:
                import traceback
                logger.error(f"Détails de l'erreur: {traceback.format_exc()}")
                await matrix_client.send_markdown_message(
                    room_id,
                    f"⚠️ Erreur technique lors de l'exploration du lien après {max_retries+1} tentatives: {str(e)}",
                    msgtype="m.notice"
                )
                return
        finally:
            # Fermer les connexions
            if 'aclient' in locals():
                await aclient.close() 