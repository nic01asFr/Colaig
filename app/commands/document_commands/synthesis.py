import re
import asyncio
import urllib.parse
from typing import Dict, Any, Optional, List, Union

from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser
from nio import RoomMessageText
from matrix_bot.client import MatrixClient

from app.config import Config
from app.commands.registry import register_feature
from app.commands import update_conversation_history, get_unified_session_context, get_context_manager
from app.actions.synthesis_rag_action import SynthesisRagAction
from app.services.context.models import SessionContext, RoomContext
from app.services.webdav import WebDAVService

DEFAULT_SYNTHESIS_CONTEXT = 'Le message "!synthese [sujet]" permet de générer une synthèse complète sur un sujet à partir de la base documentaire. Cette commande utilise un système de reranking pour sélectionner les informations les plus pertinentes et les organiser de manière cohérente.'

SYNTHESIS_HELP = '''
**!synthese [sujet]**
Génère une synthèse complète et structurée sur le sujet demandé.

Exemples:
- `!synthese procédures de recrutement` - Pour une synthèse sur les procédures de recrutement
- `!synthese gestion des congés` - Pour une synthèse sur la gestion des congés

La synthèse s'appuie sur l'ensemble des documents disponibles dans la base documentaire.
'''

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
    
    logger.info(f"[SYNTHESE] Construction URL pour: base={base_url}, user={username}, path={doc_path}")
    
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
                logger.info(f"[SYNTHESE] Lien de partage créé avec succès: {share_link}")
                return share_link
            else:
                logger.warning(f"[SYNTHESE] Impossible de créer un lien de partage, fallback vers WebDAV standard")
        except Exception as e:
            logger.error(f"[SYNTHESE] Erreur lors de la création du lien de partage: {str(e)}")
    
    # Fallback: construire une URL WebDAV standard
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
            
            logger.info(f"[SYNTHESE] Cas 1: Chemin contient déjà WebDAV pattern: {doc_path}")
            url = f"{base_url}/{doc_path}"
            # Ajouter le paramètre de téléchargement
            return f"{url}?download=1"
        
        # Cas 2: Extraire le vrai chemin du document en supprimant l'utilisateur s'il est au début
        real_doc_path = doc_path
        
        # Vérifier si le chemin commence par le nom d'utilisateur
        if user_local and doc_path.startswith(f"{user_local}/"):
            # Supprimer le préfixe utilisateur
            real_doc_path = doc_path[len(user_local)+1:]
            logger.info(f"[SYNTHESE] Cas 2: Suppression préfixe utilisateur: {real_doc_path}")
        
        # Vérifier également si le chemin complet commence par le nom d'utilisateur
        elif username and doc_path.startswith(f"{username}/"):
            # Supprimer le préfixe utilisateur complet
            real_doc_path = doc_path[len(username)+1:]
            logger.info(f"[SYNTHESE] Cas 2: Suppression préfixe email: {real_doc_path}")
        
        # Cas 3: Construire l'URL WebDAV correcte
        # Vérifier si base_url contient déjà le pattern WebDAV
        if re.search(webdav_pattern, base_url):
            webdav_url = f"{base_url}/{real_doc_path}"
        else:
            webdav_url = f"{base_url}/remote.php/dav/files/{username}/{real_doc_path}"
        
        logger.info(f"[SYNTHESE] URL WebDAV construite: {webdav_url}")
        return f"{webdav_url}?download=1"
    
    # Construire l'URL WebDAV standard
    return build_webdav_url(base_url, username, doc_path)

@register_feature(
    group="document",
    onEvent=RoomMessageText,
    command="synthese",
    help=SYNTHESIS_HELP,
    for_geek=False
)
async def handle_synthesis_command(
    ep: EventParser, 
    matrix_client: MatrixClient
) -> str:
    """
    Traite la commande !synthese pour générer une synthèse complète sur un sujet
    
    Args:
        ep: EventParser contenant les informations de l'événement
        matrix_client: Client Matrix
        
    Returns:
        Réponse formatée pour l'utilisateur
    """
    # Récupérer la configuration
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    event_id = ep.event.event_id
    sender = ep.sender
    
    # Récupérer le contenu du message
    message = ep.event.body.strip()
    # Extraire le sujet de la synthèse (après !synthese)
    match = re.match(r"^!synthese\s+(.*)", message)
    
    if not match:
        return "❓ **Comment utiliser !synthese**\n\n```\n!synthese Sujet sur lequel vous souhaitez une synthèse\n```\n\nPrécisez un sujet pour obtenir une synthèse. Exemple: `!synthese gestion des congés`"
    
    subject = match.group(1).strip()
    if not subject:
        return "❓ **Comment utiliser !synthese**\n\n```\n!synthese Sujet sur lequel vous souhaitez une synthèse\n```\n\nPrécisez un sujet pour obtenir une synthèse. Exemple: `!synthese gestion des congés`"
    
    # Récupérer les contextes
    try:
        session_context = await get_unified_session_context(config, room_id, sender)
        context_manager = get_context_manager(config)
        room_context = await context_manager.get_or_create_room_context(
            room_id=room_id,
            room_name=ep.room.name or "Salle sans nom",
            is_direct=hasattr(ep.room, "is_direct") and ep.room.is_direct
        )
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du contexte: {str(e)}")
        session_context = None
        room_context = None
    
    # Mettre à jour l'historique de conversation
    query = f"!synthese {subject}"
    try:
        if session_context:
            session_context = await update_conversation_history(
                config=config,
                room_id=room_id,
                sender=sender,
                user_message=query
            )
    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour de l'historique: {str(e)}")
    
    try:
        # Initialiser le service de synthèse
        async with SynthesisRagAction(config) as synthesis_service:
            # Activer l'indicateur de frappe
            await matrix_client.room_typing(room_id, True)
            
            # Indiquer que la génération est en cours
            await matrix_client.send_markdown_message(
                room_id,
                f"💭 Génération d'une synthèse sur '*{subject}*'...",
                msgtype="m.notice"
            )
            
            # Traiter la demande de synthèse
            synthesis_result = await synthesis_service.process_synthesis_request(
                query=subject,
                session_context=session_context,
                room_context=room_context
            )
            
            # Désactiver l'indicateur de frappe
            await matrix_client.room_typing(room_id, False)
            
            if isinstance(synthesis_result, dict):
                synthesis_text = synthesis_result.get("synthesis", "")
                sources = synthesis_result.get("sources", [])
            else:
                synthesis_text = synthesis_result
                sources = []
            
            if synthesis_text:
                # Mettre à jour l'historique avec la réponse générée
                try:
                    if session_context:
                        await update_conversation_history(
                            config=config,
                            room_id=room_id,
                            sender=sender,
                            bot_response=synthesis_text
                        )
                except Exception as e:
                    logger.error(f"Erreur lors de la mise à jour de l'historique avec la réponse: {str(e)}")
                
                # Préparer le message formaté avec références intégrées
                formatted_synthesis = synthesis_text
                
                # Configuration pour WebDAV
                webdav_base_url = getattr(config, "webdav_url", "")
                webdav_username = getattr(config, "webdav_username", "")
                webdav_service = WebDAVService(config) if webdav_base_url and webdav_username else None
                
                # Vérifier si une section Références existe déjà dans le texte
                references_already_present = re.search(r"\n#+\s*Références", formatted_synthesis) is not None
                
                # Préparation des liens pour chaque source
                document_links = {}
                for source in sources:
                    doc_name = source.get("name", "Document inconnu")
                    doc_path = source.get("path", "")
                    
                    # Construire l'URL WebDAV ou lien de partage si possible
                    webdav_url = ""
                    if webdav_service and doc_path:
                        webdav_url = await build_document_link(
                            webdav_base_url, 
                            webdav_username, 
                            doc_path, 
                            webdav_service=webdav_service
                        )
                    
                    if webdav_url:
                        document_links[doc_name] = webdav_url
                
                # Traitement selon la présence ou non d'une section Références
                if references_already_present and document_links:
                    # Trouver la section Références et remplacer les noms des documents par des liens
                    references_section_match = re.search(r"(\n#+\s*Références.*?)(\n#+\s+|\n*$)", formatted_synthesis, re.DOTALL)
                    if references_section_match:
                        references_section = references_section_match.group(1)
                        original_section = references_section
                        
                        # Remplacer chaque mention de document par un lien
                        for doc_name, webdav_url in document_links.items():
                            # Échapper les caractères spéciaux pour la regex
                            escaped_doc_name = re.escape(doc_name)
                            # Remplacer uniquement le nom du document, pas les textes environnants
                            references_section = re.sub(
                                f"({escaped_doc_name})",
                                f"[\\1]({webdav_url})",
                                references_section
                            )
                        
                        # Remplacer l'ancienne section par la nouvelle avec les liens
                        formatted_synthesis = formatted_synthesis.replace(original_section, references_section)
                        
                # Si des sources sont disponibles et qu'il n'y a pas déjà une section Références
                elif not references_already_present and sources:
                    # Préparation des références à ajouter en bas du document
                    references_section = "\n\n### Références\n"
                    
                    for idx, source in enumerate(sources, 1):
                        doc_name = source.get("name", "Document inconnu")
                        doc_path = source.get("path", "")
                        
                        # Utiliser le lien déjà préparé
                        webdav_url = document_links.get(doc_name, "")
                        
                        # Ajouter la référence avec lien si disponible
                        if webdav_url:
                            references_section += f"\n{idx}. [{doc_name}]({webdav_url})"
                        else:
                            references_section += f"\n{idx}. {doc_name}"
                    
                    # Ajouter la section de références
                    formatted_synthesis += references_section
                
                # Ajouter une note de bas de page
                if not formatted_synthesis.endswith("---"):  # Éviter la duplication si déjà présent
                    formatted_synthesis += "\n\n---\n*Cette synthèse a été générée automatiquement à partir des documents indexés. Pour plus de détails ou des précisions, n'hésitez pas à poser une question spécifique avec la commande `!chercher`.*"
                
                # Envoyer directement la synthèse au salon
                await matrix_client.send_markdown_message(
                    room_id,
                    formatted_synthesis,
                    reply_to=event_id
                )
                
                # Retourner une chaîne vide pour signaler que la commande a été traitée
                return None
            else:
                error_message = f"⚠️ Je n'ai pas pu générer une synthèse sur '*{subject}*'. Veuillez vérifier que ce sujet est couvert dans la base documentaire."
                await matrix_client.send_markdown_message(
                    room_id,
                    error_message,
                    reply_to=event_id
                )
                return None
    
    except Exception as e:
        # Désactiver l'indicateur de frappe en cas d'erreur
        await matrix_client.room_typing(room_id, False)
        
        logger.error(f"Erreur lors de la génération de la synthèse: {str(e)}")
        error_message = f"❌ Une erreur est survenue lors de la génération de la synthèse: {str(e)}"
        await matrix_client.send_markdown_message(
            room_id,
            error_message,
            reply_to=event_id
        )
        return None 