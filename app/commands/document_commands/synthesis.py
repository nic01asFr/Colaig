import re
import asyncio
import urllib.parse
from typing import Dict, Any, Optional, List, Union

from app.matrix_bot.config import logger
from app.matrix_bot.eventparser import EventParser
from nio import RoomMessageText
from app.matrix_bot.client import MatrixClient

from app.config import Config
from app.commands.registry import register_feature
from app.commands import update_conversation_history, get_unified_session_context, get_context_manager
from app.actions.synthesis_rag_action import SynthesisRagAction
from app.services.context.models import SessionContext, RoomContext

DEFAULT_SYNTHESIS_CONTEXT = 'Le message "!synthese [sujet]" permet de générer une synthèse complète sur un sujet à partir de la base documentaire. Cette commande utilise un système de reranking pour sélectionner les informations les plus pertinentes et les organiser de manière cohérente.'

SYNTHESIS_HELP = '''
**!synthese [sujet]**
Génère une synthèse complète et structurée sur le sujet demandé.

Exemples:
- `!synthese procédures de recrutement` - Pour une synthèse sur les procédures de recrutement
- `!synthese gestion des congés` - Pour une synthèse sur la gestion des congés

La synthèse s'appuie sur l'ensemble des documents disponibles dans la base documentaire.
'''

async def build_document_link(doc_path: str, webdav_service=None) -> str:
    """
    Crée un lien de partage pour un document via le service WebDAV.
    Délègue à create_share_link qui gère BigFolder et Nextcloud.

    Args:
        doc_path: Chemin du document
        webdav_service: Instance de WebDAVService

    Returns:
        URL du lien de partage ou chaîne vide
    """
    if not doc_path or not webdav_service:
        return ""

    try:
        real_path = urllib.parse.unquote(doc_path.lstrip('/'))
        logger.info(f"[SYNTHESE] Création lien pour: {real_path}")

        share_link = await webdav_service.create_share_link(real_path, expiration_days=7)
        if share_link:
            logger.info(f"[SYNTHESE] Lien créé: {share_link}")
            return share_link

        logger.warning(f"[SYNTHESE] Impossible de créer un lien pour: {real_path}")
        return ""

    except Exception as e:
        logger.error(f"[SYNTHESE] Erreur création lien: {e}")
        return ""

# Fonction utilitaire pour nettoyer les noms de documents pour l'affichage
def clean_document_name(name: str) -> str:
    """
    Nettoie le nom d'un document pour un affichage plus propre.
    
    Args:
        name: Nom du document brut
        
    Returns:
        Nom nettoyé pour l'affichage
    """
    if not name:
        return "Document inconnu"
    
    # Supprimer l'extension si elle est présente
    if name.endswith('.pdf'):
        name = name[:-4]
    elif name.endswith(('.doc', '.txt')):
        name = name[:-4]
    elif name.endswith(('.docx', '.xlsx', '.pptx')):
        name = name[:-5]
    
    # Remplacer les underscores et tirets par des espaces
    name = re.sub(r'[_-]+', ' ', name)
    
    # Nettoyer les espaces multiples
    name = re.sub(r'\s+', ' ', name).strip()
    
    # Tronquer si trop long
    if len(name) > 50:
        name = name[:47] + "..."
    
    return name

# Fonction utilitaire pour obtenir une icône selon le type de fichier
def get_file_icon(file_path: str) -> str:
    """
    Retourne une icône simple basée sur l'extension du fichier.
    """
    if not file_path:
        return "📄"
    
    # Extraire l'extension du fichier
    extension = file_path.lower().split('.')[-1] if '.' in file_path else ''
    
    # Mapping des extensions aux icônes
    icon_map = {
        'pdf': '📄',
        'doc': '📄',
        'docx': '📄',
        'txt': '📄',
        'md': '📄',
        'rtf': '📄',
        'odt': '📄',
        'xls': '📊',
        'xlsx': '📊',
        'csv': '📊',
        'ppt': '📊',
        'pptx': '📊',
        'jpg': '🖼️',
        'jpeg': '🖼️',
        'png': '🖼️',
        'gif': '🖼️',
        'svg': '🖼️',
        'mp3': '🎵',
        'wav': '🎵',
        'mp4': '🎬',
        'avi': '🎬',
        'zip': '📦',
        'rar': '📦',
        'tar': '📦',
        'gz': '📦',
        'json': '📋',
        'xml': '📋',
        'html': '🌐',
        'css': '🎨',
        'js': '⚡',
        'py': '🐍',
        'java': '☕',
        'cpp': '⚙️',
        'c': '⚙️',
        'h': '⚙️',
        'sql': '🗃️',
        'db': '🗃️',
        'log': '📋',
        'ini': '⚙️',
        'conf': '⚙️',
        'yaml': '📋',
        'yml': '📋',
        'toml': '📋',
    }
    
    # Retourner l'icône correspondante ou une icône par défaut
    return icon_map.get(extension, '📄')


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
        context_manager = await get_context_manager(config)
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
            from app.matrix_bot.typing import typing_indicator

            async with typing_indicator(matrix_client, room_id):
                # Indiquer que la génération est en cours
                await matrix_client.send_markdown_message(
                    room_id,
                    f"🔄 Génération de la synthèse sur « {subject} »...",
                    msgtype="m.notice"
                )

                # Traiter la demande de synthèse
                synthesis_result = await synthesis_service.process_synthesis_request(
                    query=subject,
                    session_context=session_context,
                    room_context=room_context
                )
            
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
                            role="assistant",
                            user_message=synthesis_text
                        )
                except Exception as e:
                    logger.error(f"Erreur lors de la mise à jour de l'historique avec la réponse: {str(e)}")
                
                # Préparer le message formaté avec références intégrées
                formatted_synthesis = synthesis_text

                # Réutiliser le WebDAV service déjà initialisé par SynthesisRagAction
                webdav_service = synthesis_service.webdav_service

                # Vérifier si une section Références existe déjà dans le texte
                references_already_present = re.search(r"\n#+\s*Références", formatted_synthesis) is not None

                # Préparation des liens pour chaque source
                document_links = {}
                for source in sources:
                    doc_name = source.get("name", "Document inconnu")
                    doc_path = source.get("path", "")

                    if webdav_service and doc_path:
                        link = await build_document_link(doc_path, webdav_service=webdav_service)
                        if link:
                            document_links[doc_name] = link
                
                # Traitement selon la présence ou non d'une section Références
                if references_already_present and document_links:
                    # Trouver la section Références et remplacer les noms des documents par des liens
                    references_section_match = re.search(r"(\n#+\s*Références.*?)(\n#+\s+|\n*$)", formatted_synthesis, re.DOTALL)
                    if references_section_match:
                        references_section = references_section_match.group(1)
                        original_section = references_section
                        
                        # Remplacer chaque mention de document par un lien avec formatage amélioré
                        for doc_name, webdav_url in document_links.items():
                            # Nettoyer le nom du document et obtenir l'icône
                            clean_name = clean_document_name(doc_name)
                            icon = get_file_icon(doc_name)
                            
                            # Échapper les caractères spéciaux pour la regex
                            escaped_doc_name = re.escape(doc_name)
                            # Remplacer uniquement le nom du document, pas les textes environnants
                            references_section = re.sub(
                                f"({escaped_doc_name})",
                                f"{icon} [{clean_name}]({webdav_url})",
                                references_section
                            )
                        
                        # Remplacer l'ancienne section par la nouvelle avec les liens
                        formatted_synthesis = formatted_synthesis.replace(original_section, references_section)
                        
                # Si des sources sont disponibles et qu'il n'y a pas déjà une section Références
                elif not references_already_present and sources:
                    # Préparation des références à ajouter en bas du document avec formatage amélioré
                    references_section = "\n\n### 📚 Références\n"
                    
                    for idx, source in enumerate(sources, 1):
                        doc_name = source.get("name", "Document inconnu")
                        doc_path = source.get("path", "")
                        
                        # Nettoyer le nom du document et obtenir l'icône
                        clean_name = clean_document_name(doc_name)
                        icon = get_file_icon(doc_path)
                        
                        # Utiliser le lien déjà préparé
                        webdav_url = document_links.get(doc_name, "")
                        
                        # Ajouter la référence avec lien si disponible
                        if webdav_url:
                            references_section += f"\n{idx}. {icon} [{clean_name}]({webdav_url})"
                        else:
                            references_section += f"\n{idx}. {icon} {clean_name}"
                    
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
        logger.error(f"Erreur lors de la génération de la synthèse: {str(e)}")
        error_message = f"❌ **Erreur de synthèse** — {str(e)}"
        await matrix_client.send_markdown_message(
            room_id,
            error_message,
            reply_to=event_id
        )
        return None 