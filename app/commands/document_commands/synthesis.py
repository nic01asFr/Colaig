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

# Fonction utilitaire pour construire l'URL WebDAV correctement avec améliorations
async def build_document_link(base_url: str, username: str, doc_path: str, webdav_service=None) -> str:
    """
    Tente de créer un lien de partage pour le document, ou construit une URL WebDAV standard.
    Améliorations: gestion des caractères spéciaux, URL encoding correct.
    
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
            # Normaliser le chemin pour l'API OCS avec validation améliorée
            real_path = doc_path.lstrip('/')
            
            # Extraire le nom d'utilisateur simple (sans domaine) si présent
            user_local = username.split('@')[0] if username and '@' in username else username
            
            # Stratégie de nettoyage du chemin pour l'API OCS
            # L'API OCS attend un chemin relatif à partir du dossier racine utilisateur
            
            # Cas 1: Le chemin contient déjà la structure WebDAV complète 
            if f"remote.php/dav/files/{username}" in real_path:
                # Extraire seulement la partie après le nom d'utilisateur
                parts = real_path.split(f"remote.php/dav/files/{username}/")
                if len(parts) > 1:
                    real_path = parts[1]
            elif f"remote.php/dav/files/{user_local}" in real_path:
                # Même chose avec le nom d'utilisateur local
                parts = real_path.split(f"remote.php/dav/files/{user_local}/")
                if len(parts) > 1:
                    real_path = parts[1]
            # Cas 2: Le chemin commence par le nom d'utilisateur
            elif user_local and real_path.startswith(f"{user_local}/"):
                real_path = real_path[len(user_local)+1:]
            elif username and real_path.startswith(f"{username}/"):
                real_path = real_path[len(username)+1:]
            
            # Décoder les caractères URL encodés pour l'API OCS
            real_path = urllib.parse.unquote(real_path)
            
            # Vérifier que le chemin nettoyé n'est pas vide
            if not real_path or real_path == '/':
                logger.warning(f"[SYNTHESE] Chemin vide après nettoyage: {doc_path}")
                # Fallback vers WebDAV direct
            else:
                # Optionnel: Vérifier l'existence du fichier avant de créer le lien
                try:
                    # Tester l'existence du fichier (seulement si la méthode exists est disponible)
                    if hasattr(webdav_service, 'exists'):
                        file_exists = await webdav_service.exists(real_path)
                        if not file_exists:
                            logger.warning(f"[SYNTHESE] Fichier non trouvé sur le serveur: {real_path}")
                            # Continuer quand même car le fichier peut exister mais être mal détecté
                except Exception as check_error:
                    logger.debug(f"[SYNTHESE] Impossible de vérifier l'existence: {str(check_error)}")
                    # Continuer quand même
                
                # Tenter de créer un lien de partage (sans mot de passe, validité 7 jours)
                share_link = await webdav_service.create_share_link(real_path, expiration_days=7)
                
                if share_link:
                    logger.info(f"[SYNTHESE] Lien de partage créé avec succès: {share_link}")
                    return share_link
                else:
                    logger.warning(f"[SYNTHESE] Impossible de créer un lien de partage pour: {real_path}")
                    
        except Exception as e:
            logger.error(f"[SYNTHESE] Erreur lors de la création du lien de partage: {str(e)}")
            logger.debug(f"[SYNTHESE] Détails de l'erreur: doc_path={doc_path}, username={username}")
            # Continuer vers le fallback WebDAV
    
    # Fallback: construire une URL WebDAV standard avec gestion améliorée des caractères spéciaux
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
            
        # Décoder d'abord si le chemin contient des caractères encodés
        if "%" in real_doc_path:
            decoded_path = urllib.parse.unquote(real_doc_path)
            logger.info(f"[SYNTHESE] Décodage avant encodage: '{real_doc_path}' -> '{decoded_path}'")
            real_doc_path = decoded_path
        
        # Normaliser les caractères Unicode pour assurer la compatibilité
        import unicodedata
        normalized_path = unicodedata.normalize('NFC', real_doc_path)
        if normalized_path != real_doc_path:
            logger.info(f"[SYNTHESE] Normalisation Unicode: '{real_doc_path}' -> '{normalized_path}'")
            real_doc_path = normalized_path
        
        # Encoder correctement le chemin pour les caractères spéciaux
        # Séparer le chemin en parties et encoder chaque partie
        path_parts = real_doc_path.split('/')
        encoded_parts = []
        for part in path_parts:
            # Ne pas encoder à nouveau si le segment semble déjà encodé
            if "%" in part and all(c in "0123456789ABCDEFabcdef%" for c in part if c not in "0123456789ABCDEFabcdef%"):
                # Cette partie semble déjà encodée correctement
                encoded_parts.append(part)
                logger.info(f"[SYNTHESE] Partie déjà encodée préservée: '{part}'")
            else:
                # Encoder la partie tout en gardant les caractères sûrs
                encoded_part = urllib.parse.quote(part, safe='')
                encoded_parts.append(encoded_part)
                
        encoded_path = '/'.join(encoded_parts)
        
        logger.info(f"[SYNTHESE] Encodage URL: '{real_doc_path}' -> '{encoded_path}'")
        
        # Cas 3: Construire l'URL WebDAV correcte
        # Vérifier si base_url contient déjà le pattern WebDAV
        if re.search(webdav_pattern, base_url):
            webdav_url = f"{base_url}/{encoded_path}"
        else:
            webdav_url = f"{base_url}/remote.php/dav/files/{username}/{encoded_path}"
        
        logger.info(f"[SYNTHESE] URL WebDAV construite: {webdav_url}")
        return f"{webdav_url}?download=1"
    
    # Construire l'URL WebDAV standard
    return build_webdav_url(base_url, username, doc_path)

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


def extract_username_for_webdav(tchap_user_id: str) -> str:
    """
    Extrait le nom d'utilisateur utilisable pour WebDAV depuis un identifiant Tchap.
    
    Args:
        tchap_user_id: Identifiant Tchap (ex: @nicolas.laval-developpement-durable.gouv.fr1:agent.dev-durable.tchap.gouv.fr)
        
    Returns:
        Nom d'utilisateur pour WebDAV (ex: nicolas.laval-developpement-durable.gouv.fr1 ou nicolas.laval)
    """
    if not tchap_user_id:
        return ""
    
    # Extraire la partie utilisateur de l'identifiant Tchap
    # Format: @user:domain -> user
    username = tchap_user_id
    if username.startswith('@') and ':' in username:
        username = username.split(':')[0][1:]  # Enlever @ et prendre avant :
    
    # Si vous avez besoin seulement du prénom.nom, décommentez ces lignes :
    # if '.' in username and '-' in username:
    #     # Extraire seulement prénom.nom (ex: nicolas.laval-developpement-durable.gouv.fr1 -> nicolas.laval)
    #     parts = username.split('-')
    #     if len(parts) > 0 and '.' in parts[0]:
    #         username = parts[0]  # Prendre seulement la première partie (prénom.nom)
    
    return username

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
                            role="assistant",
                            user_message=synthesis_text
                        )
                except Exception as e:
                    logger.error(f"Erreur lors de la mise à jour de l'historique avec la réponse: {str(e)}")
                
                # Préparer le message formaté avec références intégrées
                formatted_synthesis = synthesis_text
                
                # Configuration pour WebDAV
                base_webdav_url = getattr(config, "webdav_url", "")
                
                # Extraire l'identifiant utilisateur réel depuis Tchap
                sender_user = ep.event.sender if hasattr(ep.event, 'sender') else None
                actual_user_id = None
                
                if sender_user:
                    # Extraire le nom d'utilisateur utilisable depuis l'identifiant Tchap
                    # Format: @nicolas.laval-developpement-durable.gouv.fr1:agent.dev-durable.tchap.gouv.fr
                    # -> nicolas.laval-developpement-durable.gouv.fr1
                    actual_user_id = extract_username_for_webdav(sender_user)
                    logger.info(f"[SYNTHESE] Utilisateur extrait: {sender_user} -> {actual_user_id}")
                else:
                    # Fallback vers la configuration si impossible d'extraire l'utilisateur
                    actual_user_id = getattr(config, "webdav_username", "")
                    logger.warning(f"[SYNTHESE] Fallback vers config webdav_username: {actual_user_id}")
                
                # Construire la base URL WebDAV avec l'utilisateur réel
                if base_webdav_url and actual_user_id:
                    # Remplacer l'utilisateur dans l'URL de base si présent
                    if "/dav/files/" in base_webdav_url:
                        # Extraire la partie base et reconstruire avec le bon utilisateur
                        base_parts = base_webdav_url.split("/dav/files/")
                        if len(base_parts) >= 2:
                            webdav_base_url = f"{base_parts[0]}/dav/files/{actual_user_id}"
                        else:
                            webdav_base_url = base_webdav_url
                    else:
                        # Ajouter la structure WebDAV complète
                        webdav_base_url = f"{base_webdav_url.rstrip('/')}/remote.php/dav/files/{actual_user_id}"
                else:
                    webdav_base_url = base_webdav_url
                
                webdav_service = WebDAVService(config) if webdav_base_url and actual_user_id else None
                
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
                            actual_user_id,  # Utiliser l'identifiant utilisateur réel
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