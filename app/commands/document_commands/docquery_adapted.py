"""
Version adaptée de la commande docquery utilisant le nouveau décorateur albert_command.

Cette version montre comment adapter une commande existante pour utiliser
le nouveau système de décorateurs sans changer la logique métier.
"""

import asyncio
import datetime
import logging
import os
import time
from typing import Dict, List, Optional, Any, Tuple, Union
import copy
import urllib.parse
import traceback
import re
import json

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser

from app.commands.decorators import albert_command
from app.config import Config, get_config
from app.index.types import IndexService, get_index_service
from app.llm import infer_query_plan, QueryPlan, format_answer
from app.services.webdav import WebDAVService
from nio.events.room_events import RoomMessageText

logger = logging.getLogger(__name__)

@albert_command(
    group="document",
    command="docquery-new",
    aliases=["docq-new"],
    help_text="!docquery-new [question] - Interroger les documents indexés avec une question en langage naturel",
    preserve_context=True,
    timeout=90.0  # 90 secondes maximum
)
async def doc_query_adapted_command(ep: EventParser, matrix_client: MatrixClient):
    """Interroge les documents indexés avec une question en langage naturel."""
    # Logs de début
    logger.info(f"[DOCQUERY-NEW] Démarrage de la commande docquery-new")
    logger.info(f"[DOCQUERY-NEW] Sender: {ep.sender}, Room ID: {ep.room.room_id}")
    
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    
    # Fonction utilitaire pour construire l'URL WebDAV correctement
    async def build_document_link(base_url: str, username: str, doc_path: str, webdav_service=None) -> str:
        """
        Tente de créer un lien de partage pour le document, ou construit une URL WebDAV standard.
        
        Args:
            base_url: L'URL de base du serveur WebDAV
            username: Nom d'utilisateur pour l'accès WebDAV
            doc_path: Chemin du document dans le serveur WebDAV
            webdav_service: Instance de WebDAVService pour créer des liens de partage
            
        Returns:
            URL du document (lien de partage ou WebDAV)
        """
        if not base_url or not doc_path:
            return ""
        
        logger.info(f"[DOCQUERY-NEW] Construction URL pour: base={base_url}, user={username}, path={doc_path}")
        
        # Si nous avons un service WebDAV, tenter de créer un lien de partage
        if webdav_service:
            try:
                # Normaliser le chemin pour l'API OCS
                real_path = doc_path.lstrip('/')
                
                # Extraire le nom d'utilisateur simple (sans domaine) si présent
                user_local = username.split('@')[0] if username and '@' in username else username
                
                # Vérifier si le chemin commence par le nom d'utilisateur
                if user_local and real_path.startswith(f"{user_local}/"):
                    # Supprimer le préfixe utilisateur pour l'API OCS
                    real_path = real_path[len(user_local)+1:]
                
                # Tenter de créer un lien de partage (sans mot de passe, validité 7 jours)
                share_link = await webdav_service.create_share_link(real_path, expiration_days=7)
                
                if share_link:
                    logger.info(f"[DOCQUERY-NEW] Lien de partage créé avec succès: {share_link}")
                    return share_link
                else:
                    logger.warning(f"[DOCQUERY-NEW] Impossible de créer un lien de partage, fallback vers WebDAV standard")
            except Exception as e:
                logger.error(f"[DOCQUERY-NEW] Erreur lors de la création du lien de partage: {str(e)}")
        
        # Fallback: construire une URL WebDAV standard (code existant)
        def build_webdav_url(base_url, username, doc_path):
            # Nettoyer les URLs
            base_url = base_url.rstrip('/')
            doc_path = doc_path.lstrip('/')
            
            # Extraire le nom d'utilisateur simple (sans domaine) si présent
            user_local = username.split('@')[0] if username and '@' in username else username
            
            # Pattern WebDAV
            webdav_pattern = r'remote\.php/dav/files/'
            
            # Cas 1: Le chemin contient déjà la partie WebDAV complète
            if re.search(webdav_pattern, doc_path):
                # Extraire uniquement le domaine de base_url
                domain_part = re.match(r'(https?://[^/]+)', base_url)
                if domain_part:
                    base_url = domain_part.group(1)
                
                logger.info(f"[DOCQUERY-NEW] Cas 1: Chemin contient déjà WebDAV pattern: {doc_path}")
                url = f"{base_url}/{doc_path}"
                # Ajouter le paramètre de téléchargement
                return f"{url}?download=1"
            
            # Cas 2: Extraire le vrai chemin du document en supprimant l'utilisateur s'il est au début
            real_doc_path = doc_path
            
            # Vérifier si le chemin commence par le nom d'utilisateur
            if user_local and doc_path.startswith(f"{user_local}/"):
                # Supprimer le préfixe utilisateur
                real_doc_path = doc_path[len(user_local)+1:]
                logger.info(f"[DOCQUERY-NEW] Cas 2: Suppression préfixe utilisateur: {real_doc_path}")
            
            # Vérifier également si le chemin complet commence par le nom d'utilisateur
            elif username and doc_path.startswith(f"{username}/"):
                # Supprimer le préfixe utilisateur complet
                real_doc_path = doc_path[len(username)+1:]
                logger.info(f"[DOCQUERY-NEW] Cas 2: Suppression préfixe email: {real_doc_path}")
            
            # Cas 3: Construire l'URL WebDAV correcte
            # Vérifier si base_url contient déjà le pattern WebDAV
            if re.search(webdav_pattern, base_url):
                webdav_url = f"{base_url}/{real_doc_path}"
            else:
                webdav_url = f"{base_url}/remote.php/dav/files/{username}/{real_doc_path}"
            
            logger.info(f"[DOCQUERY-NEW] URL WebDAV construite: {webdav_url}")
            return f"{webdav_url}?download=1"
        
        # Construire l'URL WebDAV standard
        return build_webdav_url(base_url, username, doc_path)
    
    # Extraire la question
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    command_parts = message_text.split(maxsplit=1)
    
    # Si le message est uniquement "!docquery-new" sans arguments
    if len(command_parts) <= 1:
        logger.info(f"[DOCQUERY-NEW] Pas de question fournie, envoi des instructions")
        return """❓ **Comment utiliser !docquery-new**

```
!docquery-new Votre question sur les documents indexés
```

Posez une question en langage naturel, et je chercherai des réponses dans les documents indexés."""
    
    # Extraire la question (tout ce qui suit après la commande)
    question = command_parts[1]
    logger.info(f"[DOCQUERY-NEW] Question: '{question}'")
    
    # Envoyer un message de chargement
    await matrix_client.send_markdown_message(
        room_id,
        "🔍 Recherche en cours dans les documents indexés...",
        msgtype="m.notice"
    )
    
    try:
        # Obtenir le service d'index
        index_service = await get_index_service(config)
        logger.info(f"[DOCQUERY-NEW] Service d'index obtenu")
        
        # Vérifier si l'index est valide
        try:
            # Vérifier l'état de l'index
            if not hasattr(index_service, 'document_index') or not index_service.document_index:
                logger.error(f"[DOCQUERY-NEW] Index de documents non initialisé")
                await matrix_client.send_markdown_message(
                    room_id,
                    "⚠️ **Index non disponible**\n\nL'index de documents n'est pas initialisé. Veuillez utiliser la commande `!index rebuild` pour reconstruire l'index.",
                    reply_to=ep.event.event_id
                )
                return None
                
            if not hasattr(index_service.document_index, 'faiss_index') or not index_service.document_index.faiss_index:
                logger.error(f"[DOCQUERY-NEW] Index FAISS non initialisé")
                await matrix_client.send_markdown_message(
                    room_id,
                    "⚠️ **Index corrompu**\n\nL'index FAISS n'est pas correctement initialisé. Veuillez utiliser la commande `!index rebuild` pour reconstruire l'index.",
                    reply_to=ep.event.event_id
                )
                return None
                
            # Vérifier si l'index contient des documents
            if (not hasattr(index_service.document_index.faiss_index, 'document_map') or 
                not index_service.document_index.faiss_index.document_map):
                logger.error(f"[DOCQUERY-NEW] Index vide ou corrompu")
                await matrix_client.send_markdown_message(
                    room_id,
                    "⚠️ **Index vide ou corrompu**\n\nAucun document n'est présent dans l'index. Veuillez utiliser la commande `!index rebuild` pour reconstruire l'index.",
                    reply_to=ep.event.event_id
                )
                return None
        except Exception as index_err:
            logger.error(f"[DOCQUERY-NEW] Erreur lors de la vérification de l'index: {str(index_err)}")
            await matrix_client.send_markdown_message(
                room_id,
                "⚠️ **Erreur d'index**\n\nUne erreur s'est produite lors de la vérification de l'index. Veuillez utiliser la commande `!index rebuild` pour reconstruire l'index ou contactez l'administrateur.",
                reply_to=ep.event.event_id
            )
            return None
        
        # Obtenir les résultats de recherche avec un timeout
        logger.info(f"[DOCQUERY-NEW] Exécution de la recherche pour '{question}'")
        try:
            search_results = await asyncio.wait_for(
                index_service.search(
                    question,
                    limit=5,  # Utiliser limit au lieu de max_results
                    index_type="document"  # Spécifier le type d'index à utiliser
                ),
                timeout=10.0
            )
            logger.info(f"[DOCQUERY-NEW] Nombre de résultats: {len(search_results)}")
        except asyncio.TimeoutError:
            logger.error(f"[DOCQUERY-NEW] Timeout lors de la recherche pour '{question}'")
            await matrix_client.send_markdown_message(
                room_id,
                "⏱️ La recherche a pris trop de temps. Veuillez réessayer avec une question plus simple ou contactez l'administrateur.",
                reply_to=ep.event.event_id
            )
            return None
        except Exception as e:
            logger.error(f"[DOCQUERY-NEW] Erreur lors de la recherche: {str(e)}")
            await matrix_client.send_markdown_message(
                room_id,
                f"❌ Une erreur s'est produite lors de la recherche: {str(e)}",
                reply_to=ep.event.event_id
            )
            return None
        
        # Logs détaillés pour les résultats de recherche
        logger.info(f"[DOCQUERY-NEW] Détails des résultats de recherche:")
        for i, result in enumerate(search_results):
            source = result.get("source", "Document inconnu")
            score = result.get("score", 0)
            text_preview = result.get("text", "")[:100] + "..." if result.get("text") else "Pas de texte"
            metadata = result.get("metadata", {})
            logger.info(f"[DOCQUERY-NEW] Résultat #{i+1}: Source={source}, Score={score}")
            logger.info(f"[DOCQUERY-NEW] Aperçu du texte: {text_preview}")
            logger.info(f"[DOCQUERY-NEW] Métadonnées: {metadata}")
        
        # Si aucun résultat n'est trouvé
        if not search_results:
            logger.warning(f"[DOCQUERY-NEW] Aucun résultat trouvé pour la question: '{question}'")
            return f"""⚠️ Je n'ai trouvé aucun document pertinent pour répondre à votre question.

Suggestions:
- Essayez de reformuler votre question
- Vérifiez si les documents sont bien indexés
- Utilisez des mots-clés plus généraux"""
        
        # Envoyer un message de réflexion pendant la génération
        await matrix_client.send_markdown_message(
            room_id,
            "💭 J'analyse les documents pertinents pour formuler une réponse...",
            msgtype="m.notice"
        )
        
        # Extraire les sources
        sources = []
        contexts = []
        
        # Liste pour stocker les documents uniques et leurs informations
        document_sources = []
        
        for result in search_results:
            # Extraire les informations du document à partir des métadonnées
            metadata = result.get("metadata", {})
            document_name = metadata.get("document_name", "")
            document_path = metadata.get("document_path", "")
            page = metadata.get("page", "")
            section_title = metadata.get("section_title", "")
            
            # Si nous avons un nom de document, ajouter à la liste des sources
            if document_name:
                # Créer une entrée source avec les informations disponibles
                source_info = {
                    "name": document_name,
                    "path": document_path,
                    "page": page,
                    "section": section_title,
                    "score": metadata.get("similarity_score", 0)
                }
                document_sources.append(source_info)
            
            # Récupérer le contenu du résultat pour le contexte
            content = None
            
            # Essayer d'abord d'extraire directement du champ "content"
            if result.get("content"):
                content = result.get("content")
            # Ensuite, tenter d'accéder au contenu du champ "text" 
            elif result.get("text"):
                content = result.get("text")
            
            # Si nous n'avons toujours pas de contenu, le construire à partir des métadonnées
            if not content or content.strip() == "":
                content_parts = []
                
                if document_name:
                    content_parts.append(f"Document: {document_name}")
                
                if section_title:
                    content_parts.append(f"Section: {section_title}")
                
                if page:
                    content_parts.append(f"Page: {page}")
                
                # Chercher des extraits dans d'autres champs des métadonnées
                for field in ['document_title', 'chunk_content', 'text']:
                    if metadata.get(field):
                        content_parts.append(metadata.get(field))
                
                # Joindre toutes les parties pour former un contenu
                content = "\n".join(content_parts)
            
            # Log détaillé pour comprendre la structure des résultats
            logger.info(f"[DOCQUERY-NEW] Structure complète du résultat: {result}")
            
            # Ajouter le contexte seulement si nous avons du contenu
            if content and content.strip():
                contexts.append(content)
                # Ajouter également à la liste des sources pour compatibilité
                sources.append(document_name or "Document inconnu")
            else:
                logger.warning(f"[DOCQUERY-NEW] Résultat ignoré car contenu vide")
        
        # Si aucun contexte valide n'a été trouvé après le filtrage
        if not contexts:
            logger.warning(f"[DOCQUERY-NEW] Aucun contexte textuel valide trouvé pour la question: '{question}'")
            return f"""⚠️ J'ai trouvé des documents potentiellement pertinents, mais je n'ai pas pu en extraire le contenu textuel.

Suggestions:
- Vérifiez l'indexation des documents avec la commande !index verify
- Essayez de reconstruire l'index avec !index rebuild
- Contactez l'administrateur pour vérifier l'extraction de texte"""
        
        # Marquer le début de la génération
        generation_start = time.time()
        
        # Générer et envoyer la réponse
        try:
            # Générer une réponse avec les références
            logger.info(f"[DOCQUERY-NEW] Génération de la réponse avec {len(contexts)} contextes")
            
            # Tracer les contextes pour le débogage
            for i, context in enumerate(contexts):
                logger.info(f"[DOCQUERY-NEW] Contexte {i+1}: {context[:100]}...")
            
            # Déterminer le plan de requête avec l'inférence
            query_plan = await infer_query_plan(
                config,
                query=question,
                contexts=contexts
            )
            logger.info(f"[DOCQUERY-NEW] Plan de requête déterminé: {query_plan}")
            
            # Générer la réponse formatée
            response = await format_answer(
                config,
                query=question,
                contexts=contexts,
                query_plan=query_plan
            )
            logger.info(f"[DOCQUERY-NEW] Réponse générée: {response[:100]}...")
            
            # Ajouter les sources à la réponse (TOUJOURS inclure une section de sources)
            logger.info(f"[DOCQUERY-NEW] Préparation des sources, {len(document_sources)} documents trouvés")
            
            # Trier les sources par score de similarité (du plus élevé au plus bas)
            document_sources.sort(key=lambda x: x.get("score", 0), reverse=True)
            
            # Regrouper les sources par document unique
            document_groups = {}
            for src in document_sources:
                doc_name = src["name"]
                
                # Si c'est la première fois qu'on voit ce document
                if doc_name not in document_groups:
                    document_groups[doc_name] = {
                        "path": src["path"],
                        "pages": set(),
                        "sections": set(),
                        "score": src.get("score", 0)
                    }
                else:
                    # Mettre à jour le score si le nouveau est meilleur
                    if src.get("score", 0) > document_groups[doc_name]["score"]:
                        document_groups[doc_name]["score"] = src.get("score", 0)
                
                # Ajouter page et section si elles existent
                if src.get("page"):
                    document_groups[doc_name]["pages"].add(src.get("page"))
                if src.get("section"):
                    document_groups[doc_name]["sections"].add(src.get("section"))
            
            # Construire la section des sources
            formatted_sources = "\n\n## Sources consultées:"
            
            if document_groups:
                # Base WebDAV URL du serveur
                webdav_base_url = config.webdav_url if hasattr(config, 'webdav_url') else ""
                webdav_username = config.webdav_username if hasattr(config, 'webdav_username') else ""
                
                logger.info(f"[DOCQUERY-NEW] Base WebDAV URL: {webdav_base_url}")
                
                # Créer une instance de WebDAVService pour les liens de partage
                webdav_service = WebDAVService(config)
                
                # Trier les documents par score de similarité
                sorted_docs = sorted(document_groups.items(), key=lambda x: x[1]["score"], reverse=True)
                
                # Ajouter chaque document unique avec un lien correct
                for doc_name, doc_info in sorted_docs:
                    doc_path = doc_info["path"]
                    pages = sorted(list(doc_info["pages"]))
                    sections = sorted(list(doc_info["sections"]))
                    
                    # Formater l'information contextuelle
                    context_info = ""
                    
                    # Ajouter les pages (formatées de manière compacte)
                    if pages:
                        if len(pages) == 1:
                            context_info += f"page {pages[0]}"
                        else:
                            # Regrouper les pages consécutives
                            ranges = []
                            start = pages[0]
                            end = pages[0]
                            
                            for i in range(1, len(pages)):
                                try:
                                    current = int(pages[i])
                                    previous = int(end)
                                    if current == previous + 1:
                                        end = pages[i]
                                    else:
                                        if start == end:
                                            ranges.append(f"{start}")
                                        else:
                                            ranges.append(f"{start}-{end}")
                                        start = pages[i]
                                        end = pages[i]
                                except ValueError:
                                    # Si ce n'est pas un nombre, l'ajouter individuellement
                                    ranges.append(pages[i])
                            
                            # Ajouter la dernière plage
                            if start == end:
                                ranges.append(f"{start}")
                            else:
                                ranges.append(f"{start}-{end}")
                                
                            context_info += f"pages {', '.join(ranges)}"
                    
                    # Ajouter les sections (limitées pour éviter un texte trop long)
                    if sections:
                        if context_info:
                            context_info += ", "
                        
                        if len(sections) == 1:
                            context_info += f"section: {sections[0]}"
                        else:
                            # Limiter le nombre de sections affichées
                            if len(sections) <= 3:
                                section_list = sections
                            else:
                                section_list = sections[:2] + ["..."]
                            
                            context_info += f"sections: {', '.join(section_list)}"
                    
                    # Ajouter les parenthèses seulement s'il y a du contenu
                    if context_info:
                        context_info = f" ({context_info})"
                    
                    # Construire l'URL WebDAV ou lien de partage
                    webdav_url = await build_document_link(
                        webdav_base_url, 
                        webdav_username, 
                        doc_path, 
                        webdav_service=webdav_service
                    )
                    
                    if webdav_url:
                        # Décodez pour l'affichage
                        decoded_path = urllib.parse.unquote(doc_path)
                        
                        # Ajouter avec lien
                        source_line = f"\n- [{doc_name}]({webdav_url}){context_info}"
                    else:
                        # Ajouter sans lien
                        source_line = f"\n- {doc_name}{context_info}"
                    
                    formatted_sources += source_line
                    logger.info(f"[DOCQUERY-NEW] Source ajoutée: {source_line}")
            else:
                # Si aucune source n'a été extraite, mais des résultats existent
                if search_results:
                    # Chercher les documents dans les résultats de recherche
                    doc_names = set()
                    for result in search_results:
                        metadata = result.get("metadata", {})
                        doc_name = metadata.get("document_name", "")
                        if doc_name:
                            doc_names.add(doc_name)
                    
                    # Ajouter chaque document trouvé
                    if doc_names:
                        for doc_name in doc_names:
                            formatted_sources += f"\n- {doc_name}"
                    else:
                        formatted_sources += "\n- Document BPU_OPSIA.pdf (aucun lien disponible)"
                    
                    logger.info("[DOCQUERY-NEW] Ajout des sources basé sur les résultats bruts")
                else:
                    formatted_sources += "\n- Aucune source spécifique identifiée."
                    logger.warning("[DOCQUERY-NEW] Aucune source unique n'a été identifiée")
            
            # Ajouter une note sur l'accessibilité des documents
            formatted_sources += "\n\n*Cliquez sur les noms des documents pour les télécharger. Si vous n'avez pas accès à certains documents, veuillez contacter votre administrateur.*"
            
            # Ajouter les sources à la réponse
            response += formatted_sources
            
            # Ajouter une section note de bas de page
            response += "\n\n---\n*Cette réponse a été générée automatiquement à partir des documents indexés. Pour toute question, précision ou information complémentaire, n'hésitez pas à demander.*"
            
            # Log de la réponse complète pour débogage
            logger.info(f"[DOCQUERY-NEW] Réponse finale générée: \n{response}")
            
            # Envoyer la réponse finale
            logger.info(f"[DOCQUERY-NEW] Envoi de la réponse finale au chat")
            response_event = await matrix_client.send_markdown_message(
                room_id,
                response,
                reply_to=ep.event.event_id
            )
            
            logger.info(f"[DOCQUERY-NEW] Réponse envoyée avec event_id: {response_event}")
            return None
            
        except Exception as e:
            error_message = f"Une erreur est survenue lors de la génération de la réponse: {str(e)}"
            logger.error(f"[DOCQUERY-NEW] {error_message}")
            logger.exception(e)
            
            # Envoyer un message d'erreur
            await matrix_client.send_markdown_message(
                room_id,
                f"⚠️ {error_message}",
                reply_to=ep.event.event_id
            )
            return None
        
    except Exception as e:
        logger.error(f"[DOCQUERY-NEW] Erreur lors du traitement de la commande: {str(e)}")
        # Envoyer le message d'erreur directement
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Une erreur est survenue lors de la recherche: {str(e)}",
            msgtype="m.notice"
        )
        return None  # Retourner None au lieu du message d'erreur 