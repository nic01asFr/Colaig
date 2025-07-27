"""
Commandes pour la recherche d'informations locales et la gestion de liens.

Ce module fournit des commandes pour:
- Rechercher des informations dans les liens indexés localement
- Gérer une base de données de liens organisés par catégories
- Explorer et naviguer sur le web via des résumés de contenu
"""

import json
import asyncio
import time
from typing import Dict, List, Any

from app.matrix_bot.client import MatrixClient
from app.matrix_bot.config import logger
from app.matrix_bot.eventparser import EventParser

from app.commands.decorators import albert_command, albert_thread_command
from app.commands import update_conversation_history
from app.services.web_links_manager import get_web_links_manager
from app.services.web_search_cache import get_web_search_cache
from app.core_llm import AlbertApiClient
from app.services.browser_extraction import extract_web_content
from app.services.web_manager import WebManager
from app.services.web_content_manager import get_web_content_manager

@albert_thread_command(
    thread_name="recherche_web",
    group="web",
    command="recherche_web",
    help_text="!recherche_web [question] - Rechercher des informations actualisées dans les liens indexés",
    preserve_context=True,
    timeout=300.0  # 5 minutes pour permettre le rafraîchissement
)
async def web_search_command(ep: EventParser, matrix_client: MatrixClient):
    """Recherche des informations dans le contenu indexé localement avec mise à jour automatique."""
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

Je rechercherai des informations **à jour** dans les liens indexés.
Les sources pertinentes seront automatiquement actualisées avant de répondre.

💡 **Astuce** : Plus vous ajoutez de liens avec `!ajouter_lien`, plus mes réponses seront complètes !""",
            msgtype="m.notice"
        )
        return
    
    # Extraire la requête
    prompt = command_parts[1]
    
    # Envoyer un message de chargement
    await matrix_client.send_markdown_message(
        room_id,
        f"🔍 **Recherche avec données actualisées** : *{prompt}*\n\n📚 Analyse et mise à jour des sources...",
        msgtype="m.notice"
    )
    
    try:
        # Étape 1: Recherche initiale dans le contenu indexé
        web_content_manager = await get_web_content_manager(
            config,
            room_id=room_id,
            user_id=sender
        )
        initial_results = await web_content_manager.search_stored_content(prompt, top_k=15)
        
        if not initial_results:
            await matrix_client.send_markdown_message(
                room_id,
                f"""❌ **Aucune source indexée trouvée** pour : *{prompt}*

🔧 **Actions recommandées** :
1. Ajoutez des liens pertinents avec `!ajouter_lien URL`
2. Explorez des sources existantes avec `!explorer_lien URL`
3. Consultez les sources disponibles avec `!liste_liens`

💡 **Suggestion** : Commencez par ajouter 2-3 sites de référence sur votre sujet.""",
                msgtype="m.notice"
            )
            return
        
        # Étape 2: Analyser les résultats et identifier les sources à actualiser
        sources_to_refresh = []
        sources_fresh = []
        sources_analysis = {}
        
        for result in initial_results:
            url = result.get("url")
            similarity = result.get("similarity", 0)
            title = result.get("title", "")
            summary = result.get("summary", "")
            best_chunk_text = result.get("best_chunk_text", "")
            
            # Vérifier la fraîcheur
            is_fresh = await web_content_manager.is_content_fresh(url)
            
            sources_analysis[url] = {
                "similarity": similarity,
                "is_fresh": is_fresh,
                "title": title,
                "summary": summary,
                "best_chunk_text": best_chunk_text,
                "should_refresh": False
            }
            
            # Logique de décision pour le rafraîchissement
            if similarity > 0.5:  # Haute pertinence
                if not is_fresh:
                    sources_to_refresh.append(url)
                    sources_analysis[url]["should_refresh"] = True
                else:
                    sources_fresh.append(url)
            elif similarity > 0.3:  # Pertinence moyenne
                if not is_fresh:
                    sources_to_refresh.append(url)
                    sources_analysis[url]["should_refresh"] = True
        
        # Limiter le nombre de sources à rafraîchir pour éviter la surcharge
        sources_to_refresh = sources_to_refresh[:5]
        
        # Étape 3: Rafraîchir les sources pertinentes de manière synchrone
        refreshed_sources = []
        if sources_to_refresh:
            await matrix_client.send_markdown_message(
                room_id,
                f"🔄 **Actualisation en cours** : {len(sources_to_refresh)} source(s) pertinente(s) détectée(s)\n\n*Mise à jour pour garantir des informations récentes...*",
                msgtype="m.notice"
            )
            
            for i, url in enumerate(sources_to_refresh, 1):
                try:
                    logger.info(f"Rafraîchissement {i}/{len(sources_to_refresh)}: {url}")
                    
                    # Utiliser la logique d'ajouter_lien pour actualiser
                    refresh_result = await web_content_manager.process_and_store_page(url)
                    
                    if refresh_result["status"] == "success":
                        refreshed_sources.append(url)
                        logger.info(f"Source actualisée avec succès: {url}")
                    else:
                        logger.warning(f"Échec de l'actualisation: {url} - {refresh_result.get('error', 'Erreur inconnue')}")
                
                except Exception as e:
                    logger.error(f"Erreur lors du rafraîchissement de {url}: {str(e)}")
                    continue
            
            # Message de progression
            if refreshed_sources:
                await matrix_client.send_markdown_message(
                    room_id,
                    f"✅ **{len(refreshed_sources)} source(s) actualisée(s)**\n\n🔍 Nouvelle recherche avec données fraîches...",
                    msgtype="m.notice"
                )
            else:
                await matrix_client.send_markdown_message(
                    room_id,
                    f"⚠️ **Actualisation partielle** : Utilisation des données disponibles\n\n🔍 Génération de la réponse...",
                    msgtype="m.notice"
                )
        
        # Étape 4: Nouvelle recherche avec les données actualisées
        updated_results = await web_content_manager.search_stored_content(prompt, top_k=12)
        
        if not updated_results:
            await matrix_client.send_markdown_message(
                room_id,
                f"❌ **Problème technique** : Impossible de récupérer les données après actualisation.\n\nVeuillez réessayer dans quelques instants.",
                msgtype="m.notice"
            )
            return
        
        # Étape 5: Sélectionner les meilleurs résultats
        high_quality_sites = []
        sources_used = []
        
        for result in updated_results:
            url = result.get("url")
            title = result.get("title", "")
            summary = result.get("summary", "")
            similarity = result.get("similarity", 0)
            best_chunk_text = result.get("best_chunk_text", "")
            chunks_matched = result.get("chunks_matched", 0)
            
            # Seuil adaptatif basé sur la qualité des résultats
            min_similarity = 0.3 if len(updated_results) > 8 else 0.25
            
            if similarity > min_similarity:
                is_fresh = await web_content_manager.is_content_fresh(url)
                was_refreshed = url in refreshed_sources
                
                high_quality_sites.append({
                    "url": url,
                    "title": title,
                    "summary": summary,
                    "text": best_chunk_text,  # Meilleur extrait pour le contexte
                    "similarity": similarity,
                    "chunks_matched": chunks_matched,
                    "is_fresh": is_fresh,
                    "was_refreshed": was_refreshed
                })
                
                sources_used.append({
                    "url": url,
                    "title": title,
                    "similarity": similarity,
                    "chunks_matched": chunks_matched,
                    "is_fresh": is_fresh,
                    "was_refreshed": was_refreshed
                })
        
        if not high_quality_sites:
            await matrix_client.send_markdown_message(
                room_id,
                f"""⚠️ **Résultats insuffisants** après actualisation pour : *{prompt}*

📊 **Diagnostic** :
- Sources analysées : {len(initial_results)}
- Sources actualisées : {len(refreshed_sources)}
- Sites pertinents : {len(high_quality_sites)}

🔧 **Suggestions** :
- Reformulez votre question avec des termes différents
- Ajoutez des sources plus spécialisées avec `!ajouter_lien`
- Vérifiez que vos sources couvrent bien le sujet""",
                msgtype="m.notice"
            )
            return
        
        # Étape 6: Préparer le contexte pour la génération
        # Trier par similarité et prioriser les sources fraîches
        high_quality_sites.sort(key=lambda x: (x["similarity"], x["is_fresh"]), reverse=True)
        
        context_chunks = []
        sources_list = []
        
        for i, site_info in enumerate(high_quality_sites[:6]):  # Top 6 sites (au lieu de 8 chunks)
            freshness_indicator = "🆕" if site_info["was_refreshed"] else ("✅" if site_info["is_fresh"] else "⏰")
            
            # Utiliser le titre et le résumé si disponibles, sinon l'extrait
            site_description = f"**{site_info['title']}**" if site_info['title'] else f"**Site {i+1}**"
            
            content_text = ""
            if site_info['summary']:
                content_text = f"Résumé : {site_info['summary']}"
                if site_info['text']:
                    content_text += f"\n\nExtrait pertinent : {site_info['text']}"
            else:
                content_text = site_info['text']
            
            context_chunks.append(
                f"{site_description} {freshness_indicator} (similarité: {site_info['similarity']:.2f}, {site_info['chunks_matched']} sections pertinentes):\n{content_text}"
            )
            
            source_status = "actualisée" if site_info["was_refreshed"] else ("récente" if site_info["is_fresh"] else "ancienne")
            sources_list.append(f"**{site_info['title']}** - {site_info['url']} (sim: {site_info['similarity']:.2f}, {source_status})")
        
        # Construire le contexte final
        context = "\n\n---\n\n".join(context_chunks)
        
        # Tronquer si nécessaire
        if len(context) > 16000:
            context = context[:16000] + "...\n\n[Contenu tronqué pour respecter les limites]"
        
        # Étape 7: Générer la réponse avec Albert
        aclient = AlbertApiClient(
            base_url=config.albert_api_url, 
            api_key=config.albert_api_token
        )
        
        # Compter les sources actualisées pour le prompt
        fresh_sources_count = sum(1 for s in sources_used if s["was_refreshed"])
        
        generation_prompt = f"""Tu es un assistant de recherche qui répond aux questions en utilisant des informations extraites de sites web locaux indexés, dont {fresh_sources_count} ont été actualisés spécifiquement pour cette recherche.

**Question de l'utilisateur** : {prompt}

**Sites web consultés** (certains viennent d'être actualisés) :
{context}

**Instructions importantes** :
- Réponds UNIQUEMENT en te basant sur les informations fournies ci-dessus
- Privilégie les informations des sites qui viennent d'être actualisés (marqués 🆕)
- Si des informations se contredisent entre sites anciens et récents, privilégie les récents
- Utilise les titres des sites pour contextualiser tes références
- Organise ta réponse de manière structurée et claire
- Mentionne quand des informations sont particulièrement récentes ou ont été actualisées
- Si les informations sont insuffisantes, indique-le clairement
- Ne fais AUCUNE référence à des connaissances externes
- Ne mentionne pas que tu es un modèle d'IA

Fournis une réponse précise et bien structurée basée sur ces sites web actualisés."""
        
        try:
            response_text = await aclient.generate(
                model=config.albert_model,
                messages=[{"role": "user", "content": generation_prompt}]
            )
            
            # Formater la réponse finale
            formatted_response = f"**🔍 Recherche avec données actualisées** : *{prompt}*\n\n{response_text}"
            
            # Ajouter les informations détaillées sur les sources
            if sources_list:
                formatted_response += f"\n\n**📚 Sources consultées** ({len(sources_list)}) :"
                for i, source in enumerate(sources_list, 1):
                    formatted_response += f"\n{i}. {source}"
            
            # Résumé de l'actualisation
            if refreshed_sources:
                formatted_response += f"\n\n**🔄 Actualisation effectuée** :"
                formatted_response += f"\n- {len(refreshed_sources)} source(s) mise(s) à jour pour cette recherche"
                formatted_response += f"\n- Données garanties fraîches au moment de la réponse"
            
            # Conseils pour l'avenir
            old_sources_count = sum(1 for s in sources_used if not s["is_fresh"] and not s["was_refreshed"])
            if old_sources_count > 0:
                formatted_response += f"\n\n💡 **Conseil** : {old_sources_count} source(s) restent anciennes. Utilisez `!ajouter_lien` pour ajouter des sources plus récentes."
            
            # Mettre en cache avec métadonnées d'actualisation
            web_search_cache = await get_web_search_cache(config)
            await web_search_cache.set(
                query=prompt,
                data={
                    "response": formatted_response,
                    "sources": sources_list,
                    "refreshed_sources": refreshed_sources,
                    "total_sources": len(sources_used),
                    "created_at": time.time()
                }
            )
            
            # Enregistrer dans l'historique
            await update_conversation_history(
                config,
                room_id,
                sender,
                role="user",
                user_message=prompt
            )
            
            # Puis enregistrer la réponse
            await update_conversation_history(
                config,
                room_id,
                sender,
                role="assistant",
                user_message=formatted_response
            )
            
            # Envoyer la réponse
            await matrix_client.send_markdown_message(
                room_id,
                formatted_response,
                msgtype="m.notice"
            )
            
        except Exception as e:
            logger.error(f"Erreur lors de la génération de réponse: {str(e)}")
            await matrix_client.send_markdown_message(
                room_id,
                f"❌ **Erreur lors de la génération** : {str(e)}\n\nLes sources ont été actualisées mais la réponse n'a pas pu être générée.",
                msgtype="m.notice"
            )
        finally:
            await aclient.close()
    
    except Exception as e:
        logger.error(f"Erreur lors de la recherche avec actualisation: {str(e)}")
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ **Erreur système** : {str(e)}\n\nProblème lors de l'actualisation des sources. Veuillez réessayer.",
            msgtype="m.notice"
        )
    
    finally:
        # Fermer les connexions
        if 'aclient' in locals():
            await aclient.close()

@albert_command(
    group="web",
    command="ajouter_lien",
    help_text="!ajouter_lien [URL] [titre?] [catégorie?] - Ajouter un lien à la base de données et indexer son contenu",
    preserve_context=True,
    timeout=180.0  # Temps plus long car indexation
)
async def add_link_command(ep: EventParser, matrix_client: MatrixClient):
    """Ajoute un lien à la base de données avec extraction et indexation du contenu."""
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
!ajouter_lien URL [titre] [catégorie]
```

Si le titre ou la catégorie ne sont pas spécifiés, ils seront déterminés automatiquement.
La page sera explorée, résumée et indexée pour les recherches futures.

**Exemples**:
- `!ajouter_lien https://www.service-public.fr`
- `!ajouter_lien https://www.service-public.fr "Service Public" administration`""",
                msgtype="m.notice"
            )
            return
        
        # Extraire l'URL
        url = args[1]
        
        # Valider l'URL
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        
        # Message de chargement
        await matrix_client.send_markdown_message(
            room_id,
            f"⏳ Analyse et indexation du lien {url} en cours...\n\n*Cette opération peut prendre quelques minutes.*",
            msgtype="m.notice"
        )
        
        # Obtenir les arguments supplémentaires si présents
        title = None
        category = None
        
        if len(args) >= 3:
            title = args[2].strip('"\'')
        
        if len(args) >= 4:
            category = args[3].strip('"\'')
        
        # Traiter et stocker la page avec le nouveau service
        web_content_manager = await get_web_content_manager(config)
        
        result = await web_content_manager.process_and_store_page(url)
        
        if result["status"] == "success":
            # Si un titre ou une catégorie ont été spécifiés, mettre à jour le lien
            if title or category:
                web_links_manager = await get_web_links_manager(config)
                await web_links_manager.add_link(
                    url=url,
                    title=title or result.get("title", url),
                    category=category or "general",
                    description=result.get("summary", "")[:100] + "..." if len(result.get("summary", "")) > 100 else result.get("summary", "")
                )
            
            # Message de succès détaillé
            summary_preview = result.get("summary", "")[:300] + "..." if len(result.get("summary", "")) > 300 else result.get("summary", "")
            
            await matrix_client.send_markdown_message(
                room_id,
                f"""✅ **Lien ajouté et indexé avec succès**

**📄 Informations**:
- **URL**: {url}
- **Titre**: {title or result.get("title", "Non déterminé")}
- **Catégorie**: {category or "general"}

**📊 Indexation**:
- **Contenu**: {result.get("content_length", 0):,} caractères
- **Fragments**: {result.get("chunks_count", 0)} chunks indexés
- **Vecteurs**: {result.get("vectors_count", 0)} embeddings générés

**📝 Résumé**:
{summary_preview}

---
*Ce lien sera désormais pris en compte dans les recherches via !recherche_web*""",
                msgtype="m.notice"
            )
        else:
            await matrix_client.send_markdown_message(
                room_id,
                f"""❌ **Erreur lors de l'ajout du lien**

**URL**: {url}
**Erreur**: {result.get('error', 'Erreur inconnue')}

Veuillez vérifier que l'URL est accessible et réessayer.""",
                msgtype="m.notice"
            )
    
    except Exception as e:
        logger.error(f"Erreur lors de l'ajout du lien: {str(e)}")
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ **Erreur critique lors de l'ajout du lien**: {str(e)}",
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
    command_parts = message_text.split()
    
    # Si le message est uniquement "!explorer_lien" sans arguments
    if len(command_parts) <= 1:
        await matrix_client.send_markdown_message(
            room_id,
            """❓ **Comment utiliser !explorer_lien**

```
!explorer_lien URL [--ocr]
```

**Options** :
- `URL` : L'URL à explorer
- `--ocr` : Active l'extraction OCR pour les images et graphiques (optionnel)

**Exemples** :
- `!explorer_lien https://www.service-public.fr` 
- `!explorer_lien https://example.com/chart.html --ocr`

J'explorerai le contenu de cette URL et vous fournirai un résumé.""",
            msgtype="m.notice"
        )
        return
    
    # Extraire l'URL et les options
    url = command_parts[1].strip()
    use_ocr = "--ocr" in message_text.lower()
    
    # Vérifier le format de l'URL
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    # Envoyer un message de chargement
    if use_ocr:
        loading_message = await matrix_client.send_markdown_message(
            room_id,
            f"🔍📷 Exploration avancée avec OCR du lien {url} en cours...\n\n*Cette opération peut prendre plus de temps car j'analyse aussi les images et graphiques.*",
            msgtype="m.notice"
        )
    else:
        loading_message = await matrix_client.send_markdown_message(
            room_id,
            f"🔍 Exploration du lien {url} en cours...",
            msgtype="m.notice"
        )
    
    max_retries = 2
    retry_count = 0
    
    while retry_count <= max_retries:
        try:
            # Utiliser l'extraction hybride LLM+OCR
            if use_ocr:
                from app.services.browser_extraction import extract_with_llm_and_ocr
                extraction_result = await extract_with_llm_and_ocr(url, matrix_client.albert_client)
            else:
                # Utiliser l'extraction standard
                from app.services.browser_extraction import extract_web_content
                extraction_result = await extract_web_content(url, config)
            
            if not extraction_result or (hasattr(extraction_result, 'success') and not extraction_result.success):
                if retry_count < max_retries:
                    logger.warning(f"Échec de l'extraction #{retry_count+1}: {extraction_result.get('error', 'Erreur inconnue') if hasattr(extraction_result, 'get') else 'Erreur inconnue'}. Nouvelle tentative...")
                    retry_count += 1
                    continue
                else:
                    await matrix_client.send_markdown_message(
                        room_id,
                        f"⚠️ Erreur lors de l'exploration du lien après {max_retries+1} tentatives: {extraction_result.get('error', 'Erreur inconnue') if hasattr(extraction_result, 'get') else 'Erreur inconnue'}",
                        msgtype="m.notice"
                    )
                    return
            
            # Extraire le contenu selon le format de retour
            if hasattr(extraction_result, 'content'):
                # Format WebResult
                content = extraction_result.content
                title = extraction_result.title
                content_type = getattr(extraction_result, 'content_type', 'webpage')
                
                # Métadonnées spécifiques OCR
                has_visual_content = False
                ocr_elements_found = 0
                extraction_method = getattr(extraction_result, 'extraction_method', 'standard')
            else:
                # Format dict pour extraction OCR
                content = extraction_result.get('content', '')
                title = extraction_result.get('title', '')
                content_type = extraction_result.get('content_type', 'webpage')
                
                # Métadonnées OCR
                has_visual_content = extraction_result.get('has_visual_content', False)
                ocr_elements_found = extraction_result.get('ocr_elements_found', 0)
                extraction_method = extraction_result.get('extraction_method', 'standard')
            
            # Journal de débogage pour afficher ce qui a été extrait
            logger.info(f"Titre extrait: {title}")
            logger.info(f"Type de contenu détecté: {content_type}")
            logger.info(f"Méthode d'extraction: {extraction_method}")
            logger.info(f"Longueur du contenu extrait: {len(content) if content else 0} caractères")
            
            if use_ocr and has_visual_content:
                logger.info(f"Éléments visuels traités par OCR: {ocr_elements_found}")
            
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
            
            # Adapter le prompt selon l'utilisation d'OCR
            if use_ocr and has_visual_content:
                prompt = f"""Tu es un assistant qui résume efficacement le contenu d'une page web incluant les éléments visuels.
                
Voici le contenu extrait de {url} (type: {content_type}):
- Extraction par: {extraction_method}
- Éléments visuels analysés par OCR: {ocr_elements_found}

---
{content}
---

Fais-en un résumé clair et concis qui capture les informations essentielles, 
INCLUDING les données extraites des images, graphiques et tableaux par OCR.
Organise le résumé en sections si c'est pertinent.
Ton résumé ne doit pas dépasser 500 mots. Ne mentionne pas que tu es un modèle d'IA."""
            else:
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
            
            # Formater le message avec les informations d'extraction
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
            
            # Message de résumé avec informations d'extraction
            response_message = f"{content_type_emoji} **{title}**\n"
            
            if use_ocr and has_visual_content:
                response_message += f"*{content_type_label} avec analyse OCR ({ocr_elements_found} élément(s) visuel(s) analysé(s) par OCR)*\n\n"
            elif use_ocr:
                response_message += f"*{content_type_label} avec analyse OCR (aucun élément visuel détecté)*\n\n"
            else:
                response_message += f"*{content_type_label} - extraction standard*\n\n"
            
            response_message += summary
            
            # Ajouter des informations techniques si OCR utilisé
            if use_ocr and has_visual_content:
                response_message += f"\n\n---\n📊 **Analyse technique** : {ocr_elements_found} élément(s) visuel(s) analysé(s) par OCR"
            
            # Envoyer le résumé
            await matrix_client.send_markdown_message(
                room_id,
                response_message,
                msgtype="m.notice"
            )
            
            # Enregistrer dans l'historique de conversation
            await update_conversation_history(
                config,
                room_id,
                ep.sender,
                role="user",
                user_message=message_text
            )
            
            # Puis enregistrer la réponse
            await update_conversation_history(
                config,
                room_id,
                ep.sender,
                role="assistant",
                user_message=summary
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