"""
Version adaptée de la commande pj utilisant les nouveaux décorateurs.

Cette version montre comment adapter une commande avec thread pour utiliser
le nouveau système de décorateurs sans changer la logique métier.
"""

import os
import re
import asyncio
import time
import tempfile
import traceback
from typing import Dict, Any, Optional, List

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser
from nio import Event, RoomEncryptedFile, RoomMessageText, RoomMessageFile

from app.commands.decorators import albert_thread_command, albert_thread_response
from app.commands.registry import CommandThread
from app.commands import get_unified_session_context
from app.tchap_utils import get_decrypted_file, isa_reply_to
from app.commands.decorators import register_feature, thread_response

def decode_path_for_display(path: str) -> str:
    """Décode les caractères URL-encodés dans un chemin pour l'affichage à l'utilisateur."""
    if not path:
        return path
    
    # Décode les séquences comme %c3%a9 en é
    import urllib.parse
    return urllib.parse.unquote(path)

@albert_thread_command(
    thread_name="pj-new",
    group="document",
    command="pj-new",
    help_text="!pj-new - Analyser une pièce jointe et proposer un classement intelligent",
    preserve_context=True
)
async def handle_attachments_adapted_command(ep: EventParser, matrix_client: MatrixClient):
    """Gère la commande !pj-new pour analyser une pièce jointe et proposer un classement intelligent."""
    room_id = ep.room.room_id
    sender = ep.sender
    # Utiliser albert_config si disponible, sinon utiliser config
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    
    # Log de démarrage
    logger.info(f"[PJ-NEW] Démarrage de la commande pj-new")
    logger.info(f"[PJ-NEW] Sender: {sender}, Room ID: {room_id}")
    
    # 1. Envoyer un message de démarrage immédiatement pour l'utilisateur
    await matrix_client.send_markdown_message(
        room_id,
        "🔄 **Recherche de pièce jointe...**\nAnalyse du message en cours...",
        msgtype="m.notice"
    )
    
    # 2. Vérifier si c'est une réponse à un message
    is_reply = isa_reply_to(ep.event)
    logger.info(f"[PJ-NEW] Est une réponse: {is_reply}")
    
    if not is_reply:
        # Si ce n'est pas une réponse, demander à l'utilisateur de répondre à un message avec une pièce jointe
        return """📎 **Classement intelligent de document**

Pour classer intelligemment un document, veuillez utiliser cette commande en **réponse** à un message contenant une pièce jointe.

**Exemple d'utilisation:**
1. Trouvez un message avec une pièce jointe
2. Répondez à ce message avec `!pj-new`
3. Je vais analyser le document et vous proposer des options de classement
"""
    
    # 3. Récupérer l'événement référencé
    referenced_event_id = None
    if hasattr(ep.event, 'source') and 'm.relates_to' in ep.event.source.get('content', {}):
        relates_to = ep.event.source['content']['m.relates_to']
        if 'm.in_reply_to' in relates_to:
            referenced_event_id = relates_to['m.in_reply_to'].get('event_id')
            logger.info(f"[PJ-NEW] ID de l'événement référencé: {referenced_event_id}")
    
    if not referenced_event_id:
        logger.error(f"[PJ-NEW] Impossible de trouver l'ID de l'événement référencé")
        return """❌ **Erreur de référence**

Je n'ai pas pu identifier le message auquel vous répondez. Veuillez réessayer en répondant directement à un message contenant une pièce jointe.
"""
    
    # 4. Récupérer l'événement référencé
    try:
        response = await matrix_client.room_get_event(room_id, referenced_event_id)
        logger.info(f"[PJ-NEW] Réponse du room_get_event: {response}")
        
        if not response:
            logger.error(f"[PJ-NEW] La réponse room_get_event est None")
            return "❌ **Erreur de récupération**\n\nJe n'ai pas pu récupérer le message auquel vous répondez. Veuillez réessayer."
        
        referenced_event = response.event
        logger.info(f"[PJ-NEW] Type de l'événement référencé: {type(referenced_event).__name__}")
        
        if not referenced_event or not hasattr(referenced_event, 'source'):
            logger.error(f"[PJ-NEW] L'événement référencé est invalide: {referenced_event}")
            return """❌ **Erreur de récupération**

Je n'ai pas pu récupérer le message auquel vous répondez. Veuillez réessayer.
"""
    except Exception as e:
        logger.error(f"[PJ-NEW] Erreur lors de la récupération de l'événement référencé: {str(e)}")
        return f"""❌ **Erreur technique**

Une erreur est survenue lors de la récupération du message: {str(e)}
"""
    
    # 5. Vérifier si l'événement référencé contient une pièce jointe
    attachment_name = None
    attachment_size = 0
    mimetype = None
    
    # Log détaillé de l'événement pour le débogage
    logger.info(f"[PJ-NEW] Type de l'événement référencé: {type(referenced_event).__name__}")
    logger.info(f"[PJ-NEW] Attributs de l'événement: {dir(referenced_event)}")
    
    if hasattr(referenced_event, 'source'):
        logger.info(f"[PJ-NEW] Source: {referenced_event.source}")
        
        if isinstance(referenced_event.source, dict) and 'content' in referenced_event.source:
            content = referenced_event.source['content']
            logger.info(f"[PJ-NEW] Content: {content}")
            logger.info(f"[PJ-NEW] Message type: {content.get('msgtype', 'Non spécifié')}")
    
    # Si c'est un RoomMessageFile, nous définissons directement ces attributs
    if isinstance(referenced_event, RoomMessageFile):
        # Directement définir le nom du fichier
        if hasattr(referenced_event, 'body'):
            attachment_name = referenced_event.body
            logger.info(f"[PJ-NEW] Nom du fichier depuis body: {attachment_name}")
        
        # Récupérer les informations depuis source.content si disponible
        if hasattr(referenced_event, 'source') and isinstance(referenced_event.source, dict) and 'content' in referenced_event.source:
            content = referenced_event.source['content']
            # Si body n'est pas défini, essayer de le récupérer depuis content
            if not attachment_name and 'body' in content:
                attachment_name = content['body']
                logger.info(f"[PJ-NEW] Nom du fichier depuis content.body: {attachment_name}")
            
            # Récupérer la taille et le type MIME
            if 'info' in content and isinstance(content['info'], dict):
                if 'size' in content['info']:
                    attachment_size = content['info']['size']
                if 'mimetype' in content['info']:
                    mimetype = content['info']['mimetype']
        
        logger.info(f"[PJ-NEW] Détecté comme RoomMessageFile: {attachment_name}, taille: {attachment_size} octets, type: {mimetype}")
        
    # Si nous n'avons pas pu récupérer les informations via RoomMessageFile, essayer via le contenu source
    elif hasattr(referenced_event, 'source') and 'content' in referenced_event.source:
        content = referenced_event.source['content']
        
        # Vérifier si c'est un fichier par msgtype
        if content.get('msgtype') == 'm.file':
            attachment_name = content.get('body', 'fichier.txt')
            attachment_size = content.get('info', {}).get('size', 0)
            mimetype = content.get('info', {}).get('mimetype')
            logger.info(f"[PJ-NEW] Détecté comme fichier standard dans source: {attachment_name}")
        
        # Cas pour les images, vidéos et audio
        elif content.get('msgtype') in ['m.image', 'm.video', 'm.audio']:
            attachment_name = content.get('body', 'media.bin')
            attachment_size = content.get('info', {}).get('size', 0)
            mimetype = content.get('info', {}).get('mimetype')
            logger.info(f"[PJ-NEW] Détecté comme média dans source: {attachment_name}")
        
        # Cas pour les fichiers chiffrés
        elif 'file' in content and 'url' in content['file']:
            attachment_name = content.get('body', 'fichier_chiffré.bin')
            attachment_size = content.get('info', {}).get('size', 0) 
            mimetype = content.get('info', {}).get('mimetype')
            logger.info(f"[PJ-NEW] Détecté comme fichier chiffré dans source: {attachment_name}")
        
        # Tout autre type avec une URL
        elif 'url' in content:
            attachment_name = content.get('body', 'fichier.bin')
            attachment_size = content.get('info', {}).get('size', 0)
            mimetype = content.get('info', {}).get('mimetype')
            logger.info(f"[PJ-NEW] Détecté comme fichier avec URL dans source: {attachment_name}")
    
    # Si aucune pièce jointe n'a été détectée par les méthodes précédentes
    if not attachment_name:
        # Dernier recours : si c'est un RoomMessageFile mais que nous n'avons pas détecté de pièce jointe,
        # forcer l'utilisation du body et de l'URL de l'événement
        if isinstance(referenced_event, RoomMessageFile) and hasattr(referenced_event, 'body'):
            attachment_name = referenced_event.body
            if hasattr(referenced_event, 'source') and 'content' in referenced_event.source:
                content = referenced_event.source['content']
                attachment_size = content.get('info', {}).get('size', 0)
                mimetype = content.get('info', {}).get('mimetype')
            logger.info(f"[PJ-NEW] Détecté comme RoomMessageFile via fallback: {attachment_name}")
        # Si nous avons msgtype=m.file mais pas attachment_name, essayer encore une fois
        elif hasattr(referenced_event, 'source') and 'content' in referenced_event.source:
            content = referenced_event.source['content']
            if content.get('msgtype') == 'm.file':
                attachment_name = content.get('body', 'fichier.txt')
                attachment_size = content.get('info', {}).get('size', 0)
                mimetype = content.get('info', {}).get('mimetype')
                logger.info(f"[PJ-NEW] Détecté comme fichier via fallback: {attachment_name}")
    
    # Si toujours pas de pièce jointe détectée, afficher un message d'erreur
    if not attachment_name:
        logger.error(f"[PJ-NEW] Aucune pièce jointe détectée dans l'événement référencé")
        # Logguer les informations importantes pour debug
        if hasattr(referenced_event, 'source') and isinstance(referenced_event.source, dict):
            source = referenced_event.source
            logger.error(f"[PJ-NEW] DEBUG - Event source: {source}")
            if 'content' in source:
                logger.error(f"[PJ-NEW] DEBUG - Content: {source['content']}")
                logger.error(f"[PJ-NEW] DEBUG - Msgtype: {source['content'].get('msgtype')}")
                logger.error(f"[PJ-NEW] DEBUG - URL présente: {'url' in source['content']}")
                logger.error(f"[PJ-NEW] DEBUG - Body présent: {'body' in source['content']}")
        
        return """❌ **Aucune pièce jointe détectée**

Le message auquel vous répondez ne semble pas contenir de pièce jointe. 
Veuillez répondre à un message contenant un fichier, une image ou un document.
"""
    
    logger.info(f"[PJ-NEW] Pièce jointe détectée: {attachment_name}, taille: {attachment_size} octets, type: {mimetype}")
    
    # 6. Créer un fichier temporaire pour stocker la pièce jointe
    temp_file_path = f"/tmp/albert_attachment_{int(time.time())}"
    logger.info(f"[PJ-NEW] Fichier temporaire créé: {temp_file_path}")
    
    # Envoyer un message pour indiquer que le téléchargement est en cours
    await matrix_client.send_markdown_message(
        room_id,
        f"📥 **Téléchargement en cours** : *{attachment_name}*...",
        msgtype="m.notice"
    )
    
    # 7. Récupérer le contenu du fichier
    try:
        logger.info(f"[PJ-NEW] Récupération du contenu du fichier depuis l'événement: {referenced_event_id}")
        
        # Récupérer l'URL de la pièce jointe
        file_url = None
        
        # Cas 1: URL directe si c'est un RoomMessageFile
        if isinstance(referenced_event, RoomMessageFile) and hasattr(referenced_event, 'url'):
            file_url = referenced_event.url
            logger.info(f"[PJ-NEW] URL trouvée directement dans l'objet RoomMessageFile: {file_url}")
            
        # Cas 2: URL dans le contenu source
        elif hasattr(referenced_event, 'source') and 'content' in referenced_event.source:
            content = referenced_event.source['content']
            if 'url' in content:
                file_url = content['url']
                logger.info(f"[PJ-NEW] URL trouvée dans content: {file_url}")
            elif 'file' in content and isinstance(content['file'], dict) and 'url' in content['file']:
                file_url = content['file']['url']
                logger.info(f"[PJ-NEW] URL trouvée dans file.url: {file_url}")
        
        if not file_url:
            logger.error(f"[PJ-NEW] Impossible de trouver l'URL du fichier")
            return """❌ **Erreur de fichier**

Je n'ai pas pu accéder au fichier. Veuillez vérifier que le message contient bien une pièce jointe accessible.
"""
        
        # Télécharger le fichier
        logger.info(f"[PJ-NEW] Tentative de téléchargement depuis: {file_url}")
        response = await matrix_client.download(file_url)
        
        if not response or not hasattr(response, 'body'):
            logger.error(f"[PJ-NEW] Réponse de téléchargement invalide")
            return """❌ **Erreur de téléchargement**

Je n'ai pas pu télécharger le fichier. Veuillez réessayer ou utiliser un autre fichier.
"""
        
        # Déterminer si le fichier est chiffré
        file_content = response.body
        is_encrypted = False
        
        # Déchiffrer le fichier si nécessaire
        if hasattr(referenced_event, 'source') and 'content' in referenced_event.source:
            content = referenced_event.source['content']
            if 'file' in content and 'key' in content['file']:
                is_encrypted = True
                try:
                    file_info = content['file']
                    logger.info(f"[PJ-NEW] Déchiffrement du fichier avec les clés: {file_info.get('key')}")
                    file_content = decrypt_attachment(
                        response.body,
                        file_info['key'].get('k'),
                        file_info['hashes'].get('sha256'),
                        file_info['iv']
                    )
                    logger.info(f"[PJ-NEW] Fichier déchiffré, taille: {len(file_content)} octets")
                except Exception as e:
                    logger.error(f"[PJ-NEW] Erreur lors du déchiffrement: {str(e)}")
                    return f"""❌ **Erreur de déchiffrement**

Je n'ai pas pu déchiffrer le fichier. Erreur: {str(e)}
"""
        
        # Enregistrer le fichier
        with open(temp_file_path, 'wb') as f:
            f.write(file_content)
        
        logger.info(f"[PJ-NEW] Fichier sauvegardé sur disque: {temp_file_path}, taille: {len(file_content)} octets, chiffré: {is_encrypted}")
    except Exception as e:
        logger.error(f"[PJ-NEW] Erreur lors du téléchargement ou du déchiffrement du fichier: {str(e)}", exc_info=True)
        return f"""❌ **Erreur de traitement**

Une erreur est survenue lors du traitement de la pièce jointe: {str(e)}
"""
    
    # Envoyer un message pour indiquer que l'analyse est en cours
    await matrix_client.send_markdown_message(
        room_id,
        f"🔍 **Analyse du document** : *{attachment_name}*...",
        msgtype="m.notice"
    )
    
    # 8. Analyser le document
    try:
        # Extraire le contenu du document
        from app.services.document_analyzer import DocumentAnalyzer
        analyzer = DocumentAnalyzer(config)
        
        # Envoi d'un message pour indiquer que l'analyse approfondie est en cours
        await matrix_client.send_markdown_message(
            room_id,
            f"🔎 **Analyse approfondie du document** : *{attachment_name}*...",
            msgtype="m.notice"
        )
        
        # Utiliser analyze_document pour une analyse plus complète
        with open(temp_file_path, "rb") as file:
            # Récupérer d'abord le texte brut
            file.seek(0)
            raw_document_content = await analyzer.extract_text(file, attachment_name)
            
            # Vérifier si le texte a été extrait correctement
            if raw_document_content and len(raw_document_content.strip()) > 100 and not raw_document_content.startswith("Le document"):
                # Si nous avons du texte, générer une analyse
                file.seek(0)
                analysis_result = await analyzer.analyze_document(file, attachment_name)
                document_content = analysis_result
            else:
                # Si l'extraction échoue, utiliser le texte brut
                document_content = raw_document_content
        
        if not document_content or len(document_content.strip()) < 10:
            logger.warning(f"[PJ-NEW] Contenu du document trop court ou vide: {len(document_content) if document_content else 0} caractères")
            document_content = "Document vide ou non analysable"
    except Exception as e:
        logger.error(f"[PJ-NEW] Erreur lors de l'analyse du document: {str(e)}")
        document_content = f"Erreur d'analyse: {str(e)}"
    
    # 9. Analyser l'espace documentaire (arborescence des dossiers)
    try:
        # Envoyer un message pour indiquer que l'exploration de l'arborescence est en cours
        await matrix_client.send_markdown_message(
            room_id,
            f"🗂️ **Exploration de l'arborescence documentaire**...",
            msgtype="m.notice"
        )
        
        # Récupérer le service WebDAV
        from app.services.index_service import get_index_service
        index_service = await get_index_service(config)
        webdav_service = index_service.webdav_service
        
        # Déterminer le dossier racine pour les chemins
        root_folder = ""
        try:
            # Récupérer le chemin racine à partir de la configuration si disponible
            if hasattr(config, "webdav_root_path") and config.webdav_root_path:
                root_folder = config.webdav_root_path.strip("/")
                if not root_folder.endswith("/"):
                    root_folder += "/"
                logger.info(f"[PJ-NEW] Dossier racine déterminé à partir de la configuration: {root_folder}")
        except Exception as root_err:
            logger.warning(f"[PJ-NEW] Erreur lors de la détermination du dossier racine: {str(root_err)}")
            root_folder = "dossiers-espace-demonstration-2/"  # Valeur par défaut sécurisée
        
        # Lister les dossiers existants (premiers niveaux)
        # Utiliser la même approche que dans handle_attachments_command
        available_folders = []
        try:
            # Récupérer la liste des dossiers racine dans WebDAV
            root_folders = await webdav_service.list_directory("/")
            
            # Filtrer pour ne garder que les dossiers (pas les fichiers)
            available_folders = [
                folder for folder in root_folders 
                if (folder.get('type') == 'directory' or folder.get('isCollection', False)) 
                and not folder.get('name', '').startswith('.')
            ]
            
            # Ajouter les sous-dossiers du premier niveau
            full_folder_list = available_folders.copy()
            for folder in available_folders:
                folder_path = f"/{folder['name']}"
                try:
                    subfolders = await webdav_service.list_directory(folder_path)
                    for subfolder in subfolders:
                        if (subfolder.get('type') == 'directory' or subfolder.get('isCollection', False)) and not subfolder.get('name', '').startswith('.'):
                            subfolder_full_path = f"{folder_path}/{subfolder['name']}"
                            subfolder_info = subfolder.copy()
                            subfolder_info['full_path'] = subfolder_full_path
                            subfolder_info['parent'] = folder['name']
                            full_folder_list.append(subfolder_info)
                except Exception as sub_err:
                    logger.warning(f"[PJ-NEW] Erreur lors de la récupération des sous-dossiers de {folder_path}: {str(sub_err)}")
            
            available_folders = full_folder_list
            logger.info(f"[PJ-NEW] Total de {len(available_folders)} dossiers et sous-dossiers trouvés")
            
            # Extraire les chemins de dossiers pour les options
            folder_paths = []
            for folder in available_folders:
                if 'full_path' in folder:
                    folder_paths.append(folder['full_path'])
                else:
                    folder_paths.append(f"/{folder['name']}")
            
            all_folder_paths = folder_paths
            logger.info(f"[PJ-NEW] Chemins de dossiers extraits: {len(all_folder_paths)}")
        except Exception as folders_err:
            logger.error(f"[PJ-NEW] Erreur lors de la récupération des dossiers WebDAV: {str(folders_err)}")
            all_folder_paths = []
        
        # Nettoyer les chemins pour l'affichage
        display_folders = [decode_path_for_display(path) for path in all_folder_paths]
        
        # Initialiser folder_options ici
        folder_options = []
    except Exception as e:
        logger.error(f"[PJ-NEW] Erreur lors de l'exploration de l'arborescence: {str(e)}")
        display_folders = []
        all_folder_paths = []
        folder_options = []  # Initialisation en cas d'erreur
    
    # 10. Utiliser le LLM pour proposer un classement intelligent
    try:
        # Envoyer un message pour indiquer que les suggestions de classement sont en cours
        await matrix_client.send_markdown_message(
            room_id,
            f"🧠 **Analyse des options de classement**...",
            msgtype="m.notice"
        )
        
        # Si aucun dossier n'est trouvé, créer des options par défaut
        if not folder_options:
            logger.warning("[PJ-NEW] Aucun dossier trouvé dans l'arborescence, création d'options par défaut")
            
            # Utiliser les dossiers déjà récupérés précédemment
            try:
                # Utiliser available_folders pour créer les options
                root_folders = []
                for folder in available_folders:
                    if 'parent' not in folder:  # C'est un dossier racine
                        root_folders.append(folder.get('name', ''))
                
                logger.info(f"[PJ-NEW] Dossiers racine trouvés: {root_folders}")
                
                # Si nous avons trouvé des dossiers, les utiliser
                if root_folders:
                    # Prendre les 3 premiers dossiers racine maximum
                    root_folders = root_folders[:3]
                    
                    # Créer des options avec les dossiers racine réels
                    for folder in root_folders:
                        folder_options.append(("existing", folder))
                else:
                    # Fallback - essayer de récupérer les dossiers directement
                    webdav_root_folders = await webdav_service.list_directory("")
                    
                    for item in webdav_root_folders:
                        if (item.get("isCollection", False) or item.get("type") == "directory") and not item.get("href", "").startswith("."):
                            folder_name = item.get("href", "").rstrip("/")
                            if folder_name and not folder_name.startswith("."):
                                folder_name = item.get("name", folder_name)
                                if folder_name not in root_folders:
                                    root_folders.append(folder_name)
                    
                    logger.info(f"[PJ-NEW] Dossiers racine trouvés (seconde méthode): {root_folders}")
                    
                    # Prendre les 3 premiers dossiers racine maximum
                    root_folders = root_folders[:3]
                    
                    # Créer des options avec les dossiers racine réels
                    for folder in root_folders:
                        folder_options.append(("existing", folder))
                
                # Ajouter une suggestion pour un nouveau dossier adapté
                if attachment_name:
                    # Déterminer un type de dossier approprié basé sur l'extension du fichier
                    file_extension = os.path.splitext(attachment_name)[1].lower()
                    if file_extension in ['.pdf', '.doc', '.docx', '.txt']:
                        folder_options.append(("new", "Documents/Protocoles"))
                    elif file_extension in ['.jpg', '.png', '.gif', '.tif', '.bmp']:
                        folder_options.append(("new", "Images/Références"))
                    elif file_extension in ['.mp4', '.avi', '.mov', '.wmv']:
                        folder_options.append(("new", "Médias/Vidéos"))
                    elif file_extension in ['.mp3', '.wav', '.ogg', '.flac']:
                        folder_options.append(("new", "Médias/Audio"))
                    else:
                        folder_options.append(("new", "Documents/Divers"))
            except Exception as e:
                logger.error(f"[PJ-NEW] Erreur lors de la récupération des dossiers racine: {str(e)}")
                # Fallback en cas d'erreur - utiliser des options par défaut
                folder_options = [
                    ("existing", "Documents"), 
                    ("existing", "Projets"), 
                    ("existing", "Archives"),
                    ("new", "Documents/Protocoles")
                ]
        
        # Créer manuellement des suggestions de classement simples si l'API n'est pas disponible
        logger.info(f"[PJ-NEW] Génération de suggestions manuelles pour le classement")
        
        # Obtenir l'extension du fichier pour suggestion basée sur le type
        file_extension = os.path.splitext(attachment_name)[1].lower() if attachment_name else ""
        file_type_folder = ""
        
        # Associer les extensions aux types de dossiers
        if file_extension in ['.pdf', '.doc', '.docx', '.txt', '.odt']:
            file_type_folder = "Documents"
        elif file_extension in ['.jpg', '.jpeg', '.png', '.gif', '.tiff', '.bmp']:
            file_type_folder = "Images"
        elif file_extension in ['.mp4', '.avi', '.mov', '.wmv', '.flv']:
            file_type_folder = "Vidéos"
        elif file_extension in ['.xls', '.xlsx', '.csv', '.ods']:
            file_type_folder = "Données"
        elif file_extension in ['.ppt', '.pptx', '.odp']:
            file_type_folder = "Présentations"
        elif file_extension in ['.zip', '.rar', '.7z', '.tar', '.gz']:
            file_type_folder = "Archives"
        else:
            file_type_folder = "Divers"
        
        # Créer des suggestions basées sur le nom et le type de fichier
        suggestions_text = f"""Option 1: {folder_options[0][1]} - Dossier approprié pour ce type de fichier
Option 2: {folder_options[1][1] if len(folder_options) > 1 else "Archives"} - Dossier pour les fichiers importés récemment
Option 3: {folder_options[2][1] if len(folder_options) > 2 else "Ressources"} - Dossier pour le stockage à long terme
Option 4: NOUVEAU - {folder_options[-1][1] if folder_options[-1][0] == "new" else f"{file_type_folder}_{int(time.time())}"} - Nouveau dossier spécifique pour ce type de contenu"""
        
        # Tenter d'utiliser l'API si disponible
        try:
            if hasattr(config, "albert_api_token") and config.albert_api_token:
                # Récupérer le module de génération LLM
                from app.core_llm import generate
                
                # Construire la liste des dossiers réels pour le prompt
                real_folders = []
                for opt_type, opt_path in folder_options:
                    if opt_type == "existing":
                        real_folders.append(decode_path_for_display(opt_path))
                
                # Construire le contexte pour le LLM
                prompt = f"""En tant qu'assistant de classement documentaire, analyse le contenu de ce document et propose des options de classement basées sur l'arborescence existante, plus une option de création d'un nouveau dossier si nécessaire.

Document: {attachment_name}
Type: {mimetype if mimetype else "Non spécifié"}
Contenu: {document_content[:5000]}  # Limiter à 5000 caractères pour le prompt

Arborescence des dossiers racine existants:
{chr(10).join(real_folders)}

Fournir 4 suggestions de classement:
1. Les {min(3, len(real_folders))} meilleurs dossiers existants où ce document pourrait être classé, en justifiant brièvement chaque choix
2. Une suggestion de nouveau dossier à créer pour un classement optimal, avec justification

Format de réponse:
Option 1: [dossier racine 1] - [brève justification]
Option 2: [dossier racine 2] - [brève justification]
Option 3: [dossier racine 3] - [brève justification]
Option 4: NOUVEAU - [nom du nouveau dossier] - [brève justification]

N'utilise que les dossiers existants listés ci-dessus pour les 3 premières options. Si tu ne peux pas faire de suggestion pertinente, indique simplement "Pas de suggestion pertinente" pour cette option."""

                # Génération des suggestions avec le LLM
                api_suggestions = await generate(config, [{"role": "user", "content": prompt}])
                if api_suggestions and len(api_suggestions) > 50:
                    suggestions_text = api_suggestions
                    logger.info(f"[PJ-NEW] Suggestions générées par le LLM: {len(suggestions_text)} caractères")
        except Exception as llm_err:
            logger.warning(f"[PJ-NEW] Impossible d'utiliser l'API LLM, utilisation des suggestions par défaut: {str(llm_err)}")
        
        # Extraire les suggestions du texte généré
        suggestion_pattern = r"Option (\d): ([^\n]+)"
        suggestion_matches = re.findall(suggestion_pattern, suggestions_text)
        
        # Formater les suggestions pour l'affichage
        formatted_suggestions = []
        folder_options = []
        
        if suggestion_matches:
            option_number = 1  # Pour la numérotation séquentielle
            for i, (num, suggestion) in enumerate(suggestion_matches):
                # Vérifier si c'est une suggestion pertinente
                if "Pas de suggestion pertinente" in suggestion and "NOUVEAU" not in suggestion:
                    # Ignorer cette suggestion
                    continue
                
                # Utiliser des chiffres mathématiques unicode
                unicode_numbers = ["𝟭", "𝟮", "𝟯", "𝟰", "𝟱"]
                number_display = unicode_numbers[option_number-1] if option_number <= len(unicode_numbers) else f"{option_number}"
                
                # Extraire le chemin du dossier ou la suggestion de nouveau dossier
                if "NOUVEAU" in suggestion:
                    # Suggestion de nouveau dossier
                    new_folder_match = re.search(r"NOUVEAU - ([^-]+)", suggestion)
                    if new_folder_match:
                        folder_name = new_folder_match.group(1).strip()
                        # Nettoyer le nom du dossier en retirant les guillemets et autres caractères spéciaux
                        folder_name = re.sub(r'["""*]', '', folder_name).strip()
                        folder_options.append(("new", folder_name))
                        
                        # Formater avec icône et saut de ligne
                        explanation = suggestion.split('-')[-1].strip() if '-' in suggestion else ""
                        formatted_text = f"{number_display} +📁 {root_folder}{folder_name} :\n{explanation}"
                        formatted_suggestions.append(formatted_text)
                else:
                    # Chemin de dossier existant
                    path_match = re.search(r"([^-]+) -", suggestion)
                    if path_match:
                        folder_path = path_match.group(1).strip()
                        # Nettoyer le chemin en retirant les caractères spéciaux, y compris les astérisques
                        folder_path = re.sub(r'[*]+', '', folder_path).strip()
                        # Nettoyer aussi les guillemets et autres caractères spéciaux
                        folder_path = re.sub(r'["""*<>:|?]', '', folder_path).strip()
                        matching_path = next((path for path in all_folder_paths if decode_path_for_display(path) == folder_path), None)
                        folder_options.append(("existing", matching_path or folder_path))
                        
                        # Formater avec icône et saut de ligne
                        explanation = suggestion.split('-')[-1].strip() if '-' in suggestion else ""
                        # Afficher le chemin complet avec le dossier racine
                        display_path = f"{root_folder}{folder_path}"
                        formatted_text = f"{number_display} 📁 {display_path} :\n{explanation}"
                        formatted_suggestions.append(formatted_text)
                
                option_number += 1  # Incrémenter le numéro d'option seulement pour les suggestions valides
        else:
            # Si aucune suggestion n'a été générée par le LLM, utiliser les dossiers racine
            logger.info(f"[PJ-NEW] Pas de suggestions LLM pertinentes trouvées, utilisation des dossiers racine")
            
            # Générer des suggestions manuelles basées sur les dossiers racine
            option_number = 1  # Pour la numérotation séquentielle
            if folder_options:
                for i, (option_type, option_path) in enumerate(folder_options):
                    # Ignorer les suggestions non pertinentes
                    if option_path == "Pas de suggestion pertinente":
                        continue
                        
                    # Utiliser des chiffres mathématiques unicode
                    unicode_numbers = ["𝟭", "𝟮", "𝟯", "𝟰", "𝟱"]
                    number_display = unicode_numbers[option_number-1] if option_number <= len(unicode_numbers) else f"{option_number}"
                    
                    if option_type == "existing":
                        display_path = decode_path_for_display(option_path)
                        # Nettoyer le chemin en retirant tous les caractères spéciaux
                        display_path = re.sub(r'[*]+', '', display_path).strip()
                        display_path = re.sub(r'["""*<>:|?]', '', display_path).strip()
                        # Afficher le chemin complet avec le dossier racine
                        full_path = f"{root_folder}{display_path}"
                        formatted_suggestions.append(f"{number_display} 📁 {full_path}\nDossier adapté pour ce type de document")
                    else:
                        # Nettoyer le nom du dossier en retirant les guillemets et autres caractères spéciaux
                        folder_name = re.sub(r'["""*]', '', option_path).strip()
                        # Afficher le chemin complet avec le dossier racine
                        full_path = f"{root_folder}{folder_name}"
                        formatted_suggestions.append(f"{number_display} +📁 {full_path}\nDossier spécifique pour ce document")
                    
                    option_number += 1  # Incrémenter le numéro d'option seulement pour les suggestions valides
            else:
                # Aucun dossier racine ni suggestion LLM, créer des options par défaut
                logger.warning(f"[PJ-NEW] Aucun dossier ni suggestion, création d'options par défaut")
                dossiers_par_defaut = [
                    "Documents", 
                    "Projets", 
                    "Archives", 
                    f"Documents/PDFs_{int(time.time())}"
                ]
                
                for i, folder in enumerate(dossiers_par_defaut[:3]):
                    folder_options.append(("existing", folder))
                    # Utiliser des chiffres mathématiques unicode
                    unicode_numbers = ["𝟭", "𝟮", "𝟯", "𝟰", "𝟱"]
                    number_display = unicode_numbers[i] if i < len(unicode_numbers) else f"{i+1}"
                    formatted_suggestions.append(f"{number_display} 📁 {root_folder}{folder}\nDossier adapté pour ce type de document")
                
                # Ajouter la suggestion de nouveau dossier
                folder_options.append(("new", dossiers_par_defaut[3]))
                # Extraire le nom de dossier sans le chemin
                new_folder_name = os.path.basename(dossiers_par_defaut[3])
                formatted_suggestions.append(f"𝟰 +📁 {root_folder}{new_folder_name}\nDossier spécifique pour ce document")
        
        logger.info(f"[PJ-NEW] {len(formatted_suggestions)} suggestions formatées pour l'affichage")
    except Exception as e:
        logger.error(f"[PJ-NEW] Erreur lors de la génération des suggestions: {str(e)}")
        formatted_suggestions = ["Erreur lors de la génération des suggestions."]
        folder_options = []
    
    # Créer une synthèse du contenu du document
    document_summary = "Synthèse non disponible pour ce document."
    try:
        from app.core_llm import generate
        
        # Limiter la synthèse aux 2000 premiers caractères pour éviter un prompt trop long
        content_for_summary = document_content[:2000] if document_content else "Contenu non disponible"
        
        # Requête pour la synthèse
        summary_prompt = f"""Résume le contenu suivant en exactement 3 phrases et 5 lignes maximum:
        
{content_for_summary}

Sois très concis et va à l'essentiel. Limite-toi à 3 phrases."""
        
        summary_response = await generate(config, [{"role": "user", "content": summary_prompt}])
        
        if summary_response and len(summary_response) > 10:
            document_summary = summary_response.strip()
        else:
            document_summary = "Synthèse non disponible pour ce document."
            
    except Exception as summary_err:
        logger.warning(f"[PJ-NEW] Impossible de générer une synthèse: {str(summary_err)}")
        document_summary = "Synthèse non disponible pour ce document."
    
    # 11. Mettre à jour le contexte du thread
    session_context = await get_unified_session_context(config, room_id, sender)
    await CommandThread.update_state(
        room_id, 
        sender, 
        config,
        action="attente_choix_classement",
        attachment_data={
            "temp_file_path": temp_file_path,
            "attachment_name": attachment_name,
            "mimetype": mimetype,
            "size": attachment_size
        },
        document_content=document_content[:1000],  # Stocker un extrait du contenu
        folder_options=folder_options,  # Stocker les options de dossiers
        all_folder_paths=all_folder_paths  # Stocker tous les chemins de dossiers
    )
    logger.info(f"[PJ-NEW] État du thread mis à jour, attente du choix de classement")
    
    # 12. Présenter les options à l'utilisateur
    suggestions_display = "\n\n".join(formatted_suggestions)
    
    # Déterminer le nombre d'options affiché
    num_options = len(formatted_suggestions)
    # Construire la liste des numéros d'options disponibles
    options_numbers = []
    for i in range(1, num_options + 1):
        options_numbers.append(str(i))
    options_text = ", ".join(options_numbers)
    
    # Préparer le message de suggestions
    suggestions_message = f"""📋 **Analyse du document** : *{attachment_name}*

{document_summary}

Voici les suggestions de classement pour ce document:

{suggestions_display}

Pour faire votre choix, répondez simplement avec le numéro de l'option ({options_text}).
Si vous souhaitez annuler, répondez "annuler".
"""
    
    # Envoyer le message directement dans le salon
    try:
        await matrix_client.send_markdown_message(
            room_id,
            suggestions_message
        )
        logger.info(f"[PJ-NEW] Message de suggestions envoyé avec succès au salon")
    except Exception as e:
        logger.error(f"[PJ-NEW] Erreur lors de l'envoi du message de suggestions: {str(e)}")
        # En cas d'erreur, essayer d'envoyer un message simplifié
        try:
            await matrix_client.send_markdown_message(
                room_id,
                "📋 **Analyse terminée** : Veuillez répondre avec un numéro entre 1 et 4 pour choisir une option de classement."
            )
        except:
            logger.error(f"[PJ-NEW] Échec de l'envoi du message de secours")
    
    # Également retourner le message pour la compatibilité avec le système de threads
    return suggestions_message

# Fonction de validation des réponses au thread pj
def validate_pj_response(text):
    """Vérifie si la réponse est un choix valide pour le thread pj."""
    # Log pour déboguer
    logger.info(f"[PJ-NEW-VALIDATE] Validation de la réponse: '{text}'")
    
    # Ignorer la commande !pj-new elle-même
    if text.strip() == "!pj-new":
        logger.info(f"[PJ-NEW-VALIDATE] Commande !pj-new ignorée")
        return False
    
    # Améliorer la détection des réponses valides
    result = re.match(r"^\s*[1-9]\s*$", text) is not None or text.lower().strip() in ["annuler", "cancel"]
    
    logger.info(f"[PJ-NEW-VALIDATE] Résultat de validation: {result}")
    return result

@register_feature(
    group="document",
    onEvent=RoomMessageText,
    command="",  # Pas de commande spécifique pour les réponses
    help=""  # Pas d'aide pour les réponses
)
@thread_response("pj-new")
async def handle_attachments_adapted_response(ep: EventParser, matrix_client: MatrixClient):
    """Traite les réponses pour la commande pj-new et classe le document."""
    # Logs de début
    logger.info(f"[PJ-NEW-RESPONSE] Traitement de la réponse au thread pj-new")
    logger.info(f"[PJ-NEW-RESPONSE] Sender: {ep.sender}, Room ID: {ep.room.room_id}")
    
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Extraire la réponse de l'utilisateur
    choice_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    logger.info(f"[PJ-NEW-RESPONSE] Réponse utilisateur: '{choice_text}'")
    
    # Ignorer la commande !pj-new elle-même
    if choice_text == "!pj-new":
        logger.info(f"[PJ-NEW-RESPONSE] Commande !pj-new ignorée")
        return None
    
    # Récupérer l'état du thread
    session_context = await get_unified_session_context(config, room_id, sender)
    conversation_state = session_context.conversation_state
    logger.info(f"[PJ-NEW-RESPONSE] État de la conversation: {conversation_state}")
    
    # Récupérer les données du thread
    attachment_data = conversation_state.get("attachment_data", {})
    temp_file_path = attachment_data.get("temp_file_path")
    attachment_name = attachment_data.get("attachment_name", "document")
    current_action = conversation_state.get("action", "")
    folder_options = conversation_state.get("folder_options", [])
    all_folder_paths = conversation_state.get("all_folder_paths", [])
    
    # Vérifier si nous attendons une réponse pour l'action actuelle
    if current_action != "attente_choix_classement":
        logger.warning(f"[PJ-NEW-RESPONSE] Action actuelle '{current_action}' n'est pas 'attente_choix_classement'")
        
        # Essayer de corriger si l'action est vide
        if not current_action and attachment_data:
            logger.info(f"[PJ-NEW-RESPONSE] Tentative de récupération avec action vide mais données d'attachement présentes")
            # Mettre à jour l'état pour accepter la réponse
            await CommandThread.update_state(
                room_id, 
                sender, 
                config,
                action="attente_choix_classement"
            )
        else:
            return None
    
    # Vérifier si le fichier temporaire existe
    if not temp_file_path or not os.path.exists(temp_file_path):
        logger.error(f"[PJ-NEW-RESPONSE] Fichier temporaire introuvable: {temp_file_path}")
        await CommandThread.end(
            room_id, sender, "pj-new", config,
            action="erreur_fichier_absent",
            status="error"
        )
        return "⚠️ Le fichier temporaire n'existe plus. Veuillez réessayer avec la commande !pj-new."
    
    # Si l'utilisateur choisit d'annuler
    if choice_text.lower() == "annuler":
        logger.info(f"[PJ-NEW-RESPONSE] Classement annulé par l'utilisateur")
        
        # Terminer le thread
        await CommandThread.end(
            room_id, sender, "pj-new", config,
            action="classement_annulé",
            status="cancelled"
        )
        
        # Supprimer le fichier temporaire
        try:
            os.remove(temp_file_path)
            logger.info(f"[PJ-NEW-RESPONSE] Fichier temporaire supprimé: {temp_file_path}")
        except Exception as e:
            logger.warning(f"[PJ-NEW-RESPONSE] Erreur lors de la suppression du fichier temporaire: {str(e)}")
        
        # Envoyer le message directement dans le salon
        await matrix_client.send_markdown_message(room_id, "❌ Classement du document annulé.")
        
        # Également retourner le message
        return "❌ Classement du document annulé."
    
    try:
        # Traiter le choix de l'utilisateur
        choice_index = int(choice_text) - 1
        
        if choice_index < 0 or choice_index >= len(folder_options):
            logger.warning(f"[PJ-NEW-RESPONSE] Choix invalide: {choice_text} (index {choice_index}, options disponibles: {len(folder_options)})")
            return "⚠️ Choix invalide. Veuillez répondre avec un numéro entre 1 et 4."
        
        # Récupérer l'option choisie
        option_type, option_path = folder_options[choice_index]
        logger.info(f"[PJ-NEW-RESPONSE] Option choisie: type={option_type}, chemin={option_path}")
        
        # Récupérer le service WebDAV
        from app.services.index_service import get_index_service
        index_service = await get_index_service(config)
        webdav_service = index_service.webdav_service
        
        # Message de confirmation
        await matrix_client.send_markdown_message(
            room_id,
            f"📁 **Classement en cours** : *{attachment_name}*...",
            msgtype="m.notice"
        )
        
        # Traiter en fonction du type d'option
        target_path = None
        
        if option_type == "new":
            # Créer un nouveau dossier
            folder_name = option_path
            
            # Nettoyer le nom du dossier
            folder_name = re.sub(r'[<>:"/\\|?*]', '_', folder_name)  # Remplacer les caractères invalides
            
            # Créer le dossier à la racine
            new_folder_path = folder_name
            success = await webdav_service.create_directory(new_folder_path)
            
            if success:
                logger.info(f"[PJ-NEW-RESPONSE] Nouveau dossier créé: {new_folder_path}")
                target_path = new_folder_path
            else:
                logger.error(f"[PJ-NEW-RESPONSE] Échec de la création du dossier: {new_folder_path}")
                return "❌ Impossible de créer le nouveau dossier. Veuillez réessayer avec un autre choix."
        
        elif option_type == "existing":
            # Utiliser un dossier existant
            target_path = option_path
        
        # Vérifier si le chemin cible est valide
        if not target_path:
            logger.error(f"[PJ-NEW-RESPONSE] Chemin cible invalide ou non défini")
            return "❌ Le chemin cible est invalide. Veuillez réessayer avec un autre choix."
        
        # Déterminer le chemin complet du fichier cible
        file_target_path = f"{target_path}/{attachment_name}"
        
        # Vérifier si un fichier du même nom existe déjà
        file_exists = await webdav_service.exists(file_target_path)
        
        if file_exists:
            # Ajouter un suffixe au nom du fichier
            base_name, extension = os.path.splitext(attachment_name)
            timestamp = int(time.time())
            new_file_name = f"{base_name}_{timestamp}{extension}"
            file_target_path = f"{target_path}/{new_file_name}"
            logger.info(f"[PJ-NEW-RESPONSE] Un fichier du même nom existe déjà, utilisation du nouveau nom: {new_file_name}")
        
        # Télécharger le fichier vers l'emplacement cible
        with open(temp_file_path, "rb") as file:
            upload_success = await webdav_service.upload_file(file, file_target_path)
        
        if upload_success:
            logger.info(f"[PJ-NEW-RESPONSE] Fichier téléchargé avec succès vers: {file_target_path}")
            
            # Indexer le document
            await matrix_client.send_markdown_message(
                room_id,
                f"📑 **Indexation du document** : *{attachment_name}*...",
                msgtype="m.notice"
            )
            
            try:
                # Mise à jour de l'index pour ce document
                await index_service.document_index.update_specific_document(file_target_path)
                logger.info(f"[PJ-NEW-RESPONSE] Document indexé avec succès: {file_target_path}")
                indexation_success = True
            except Exception as index_err:
                logger.error(f"[PJ-NEW-RESPONSE] Erreur lors de l'indexation du document: {str(index_err)}")
                indexation_success = False
            
            # Terminer le thread avec succès
            await CommandThread.end(
                room_id, sender, "pj-new", config,
                action="document_classé",
                document_path=file_target_path,
                document_name=attachment_name,
                target_folder=target_path,
                indexed=indexation_success
            )
            
            # Construire le chemin d'affichage
            display_path = decode_path_for_display(target_path)
            
            # Message final
            final_message = f"""✅ **Document classé avec succès**

Le document *{attachment_name}* a été classé dans le dossier:
📁 *{display_path}*

"""
            if indexation_success:
                final_message += "Le document a également été indexé et pourra être recherché avec `!docquery-new`."
            else:
                final_message += "⚠️ L'indexation du document a échoué, mais le fichier a bien été classé."
            
            # Envoyer le message directement dans le salon
            await matrix_client.send_markdown_message(room_id, final_message)
            
            # Également retourner le message
            return final_message
        else:
            logger.error(f"[PJ-NEW-RESPONSE] Échec du téléchargement du fichier vers: {file_target_path}")
            
            # Terminer le thread avec une erreur
            await CommandThread.end(
                room_id, sender, "pj-new", config,
                action="erreur_classement",
                status="error"
            )
            
            # Envoyer le message directement dans le salon
            error_message = "❌ Impossible de classer le document. Une erreur s'est produite lors du téléchargement."
            await matrix_client.send_markdown_message(room_id, error_message)
            
            # Également retourner le message
            return error_message
    
    except Exception as e:
        # Gestion globale des erreurs
        logger.error(f"[PJ-NEW-RESPONSE] Erreur lors du traitement de la réponse: {str(e)}")
        logger.error(f"[PJ-NEW-RESPONSE] Traceback: {traceback.format_exc()}")
        
        # Terminer le thread avec une erreur
        try:
            await CommandThread.end(
                room_id, sender, "pj-new", config,
                action="erreur",
                error=str(e),
                status="error"
            )
        except Exception as end_err:
            logger.error(f"[PJ-NEW-RESPONSE] Erreur lors de la fin du thread: {str(end_err)}")
        
        # Envoyer le message directement dans le salon
        error_message = f"⚠️ Une erreur est survenue: {str(e)}"
        await matrix_client.send_markdown_message(room_id, error_message)
        
        # Également retourner le message
        return error_message
    
    finally:
        # Nettoyage des ressources
        try:
            if temp_file_path and os.path.exists(temp_file_path):
                os.remove(temp_file_path)
                logger.info(f"[PJ-NEW-RESPONSE] Fichier temporaire supprimé: {temp_file_path}")
        except Exception as e:
            logger.warning(f"[PJ-NEW-RESPONSE] Erreur lors de la suppression du fichier temporaire: {str(e)}") 