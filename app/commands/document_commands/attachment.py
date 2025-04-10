"""
Module de gestion des pièces jointes.

Ce module contient les commandes pour gérer les pièces jointes dans les messages,
permettant de les télécharger, les analyser et les intégrer à l'index documentaire.
"""

from typing import List, Dict, Any, Optional, Tuple

import asyncio
import traceback
import re
from datetime import datetime
import os
import io
import tempfile
import mimetypes
from pathlib import Path
import shutil
import urllib.parse

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser
from nio import Event, RoomEncryptedFile, RoomMessageText, RoomMessageFile

from app.bot_msg import AlbertMsg
from app.config import Config, COMMAND_PREFIX
from app.services.webdav import WebDAVService
from app.services.document_index import DocumentIndex
from app.services.index_service import IndexService
from app.services.context.manager import ContextManager
from app.services.context.types import ContextType
from app.tchap_utils import (
    get_decrypted_file,
    isa_reply_to
)
from app.commands.registry import register_feature, only_allowed_user, threaded_command, thread_response, CommandThread
from app.commands import get_context_manager, get_unified_session_context, update_conversation_history
from app.core_llm import generate, AlbertApiClient
from app.services.context.models import SessionContext

def decode_path_for_display(path: str) -> str:
    """Décode les caractères URL-encodés dans un chemin pour l'affichage à l'utilisateur.
    
    Args:
        path: Chemin avec caractères potentiellement encodés
        
    Returns:
        Chemin décodé pour affichage
    """
    if not path:
        return path
    
    # Décode les séquences comme %c3%a9 en é
    decoded_path = urllib.parse.unquote(path)
    return decoded_path

@register_feature(
    group="document",
    onEvent=RoomMessageText,
    command="pj",
    help="!pj - Analyser les pièces jointes d'un message (utilisez cette commande en réponse à un message)"
)
@only_allowed_user
@threaded_command("pj")
async def handle_attachments_command(ep: EventParser, matrix_client: MatrixClient):
    """Gère la commande !pj"""
    try:
        logger.debug(
            f"Handling attachments command with event type: {type(ep.event).__name__} and message: {ep.event.body[:50] if hasattr(ep.event, 'body') else 'No body'}"
        )
        
        # Log l'événement complet pour le débogage
        if hasattr(ep.event, 'source'):
            logger.debug(f"Source complète de l'événement de commande: {ep.event.source}")
        
        # Récupérer ou créer un contexte de session
        session_id = f"{ep.room.room_id}_{ep.sender}"
        
        # Vérifier si c'est une réponse à un message en utilisant la fonction isa_reply_to
        is_reply = isa_reply_to(ep.event)
        reply_to_event_id = None
        
        logger.debug(f"isa_reply_to a identifié: {is_reply}")
        
        # Vérifier si l'utilisateur a fourni un ID directement (syntaxe !pj $event_id)
        command_parts = ep.event.body.strip().split()
        if len(command_parts) > 1 and command_parts[1].startswith('$'):
            reply_to_event_id = command_parts[1][1:]  # Supprimer le $ du début
            is_reply = True
            logger.debug(f"ID d'événement fourni directement: {reply_to_event_id}")
        elif is_reply and hasattr(ep.event, 'source') and 'm.relates_to' in ep.event.source.get('content', {}):
            # Récupérer l'ID de l'événement auquel on répond
            relates_to = ep.event.source["content"]["m.relates_to"]
            if "m.in_reply_to" in relates_to:
                reply_to_event_id = relates_to["m.in_reply_to"]["event_id"]
                logger.debug(f"Message en réponse à l'événement ID: {reply_to_event_id}")
            else:
                logger.debug(f"Structure m.relates_to inattendue: {relates_to}")
        
        # Si ce n'est pas une réponse mais que c'est la commande !pj, chercher le dernier fichier
        if not is_reply and hasattr(ep.event, 'body') and ep.event.body.strip() == "!pj":
            try:
                logger.debug("Recherche du dernier fichier envoyé dans la salle")
                # Chercher dans les événements récents celui qui contient un fichier
                limit = 30  # Augmenter la limite à 30 messages pour être sûr
                
                # Log pour tracer les messages récents
                logger.debug(f"Recherche dans les {limit} derniers messages du salon {ep.room.room_id}")
                
                try:
                    messages = await matrix_client.room_messages(
                        ep.room.room_id, limit=limit
                    )
                    recent_events = messages.chunk
                    
                    if not recent_events:
                        logger.debug("Aucun message récent trouvé")
                        await matrix_client.send_markdown_message(
                            ep.room.room_id,
                            "⚠️ Impossible de trouver des messages récents contenant un fichier. Veuillez utiliser !pj en réponse à un message contenant une pièce jointe.",
                            msgtype="m.notice"
                        )
                        return
                        
                    # Parcourir les messages et chercher un fichier
                    for event in recent_events:
                        if event.sender == ep.sender and event.event_id != ep.event.event_id:
                            logger.debug(f"Examen de l'événement {event.event_id} de type {type(event).__name__}")
                            
                            # Pour les messages de type inconnu, essayer de lire le type à partir de la source
                            event_type = event.source.get("type") if hasattr(event, "source") else None
                            
                            # Log plus détaillé pour déboguer
                            if hasattr(event, "source"):
                                logger.debug(f"Source de l'événement: {event.source.get('content', {}).get('msgtype', 'non spécifié')}")
                            
                            # Vérifier si c'est un fichier (m.file, m.image, etc.)
                            is_file = False
                            if hasattr(event, "source") and event.source.get("content", {}).get("msgtype") in ["m.file", "m.image", "m.audio", "m.video"]:
                                is_file = True
                                logger.debug(f"Fichier trouvé dans événement {event.event_id}")
                            
                            if is_file:
                                reply_to_event_id = event.event_id
                                is_reply = True  # Traiter comme une réponse
                                logger.debug(f"Dernier fichier trouvé: {reply_to_event_id}")
                                break
                    
                    if not is_reply:
                        logger.debug("Aucun fichier trouvé dans les messages récents")
                        await matrix_client.send_markdown_message(
                            ep.room.room_id,
                            "⚠️ Aucun fichier récent trouvé. Veuillez utiliser !pj en réponse à un message contenant une pièce jointe.",
                            msgtype="m.notice"
                        )
                        return
                        
                except Exception as e:
                    logger.warning(f"Erreur lors de la recherche des messages récents: {str(e)}")
                    await matrix_client.send_markdown_message(
                        ep.room.room_id,
                        "⚠️ Erreur lors de la recherche des messages récents. Veuillez utiliser !pj en réponse à un message contenant une pièce jointe.",
                        msgtype="m.notice"
                    )
                    return
            except Exception as e:
                logger.warning(f"Erreur lors de la recherche du dernier fichier: {str(e)}")
                await matrix_client.send_markdown_message(
                    ep.room.room_id,
                    "⚠️ Impossible de trouver le dernier fichier. Veuillez utiliser !pj en réponse à un message contenant une pièce jointe.",
                    msgtype="m.notice"
                )
                return
        
        # Si on n'a pas d'ID d'événement à ce stade, demander à l'utilisateur de répondre à un message
        if not reply_to_event_id:
            logger.warning("Aucun ID d'événement trouvé pour la recherche de fichier")
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                "⚠️ Veuillez utiliser !pj en réponse à un message contenant une pièce jointe, ou spécifiez un ID d'événement avec !pj $event_id",
                msgtype="m.notice"
            )
            return
            
        logger.debug(f"Tentative de récupération de l'événement {reply_to_event_id}")
        
        # Récupérer l'événement original (avec handle_exceptions à True pour gérer les erreurs)
        original_event = None
        try:
            # Essayer d'abord avec get_event si disponible
            if hasattr(matrix_client, 'get_event'):
                original_event = await matrix_client.get_event(
                    ep.room.room_id, reply_to_event_id, handle_exceptions=True
                )
                logger.debug(f"Événement récupéré avec get_event: {original_event is not None}")
            else:
                # Sinon, essayer avec room_get_event
                try:
                    logger.warning("Erreur avec get_event: 'MatrixClient' object has no attribute 'get_event', tentative avec room_get_event")
                    response = await matrix_client.room_get_event(
                        ep.room.room_id, reply_to_event_id
                    )
                    if response and hasattr(response, 'event'):
                        original_event = response.event
                    elif response and hasattr(response, 'source'):
                        original_event = response
                    else:
                        logger.warning(f"Structure de réponse room_get_event inattendue: {response}")
                except Exception as e2:
                    logger.warning(f"Erreur lors de la récupération avec room_get_event: {str(e2)}")
        except Exception as e:
            logger.warning(f"Erreur lors de la récupération de l'événement original: {str(e)}")
        
        if not original_event:
            logger.warning(f"Impossible de récupérer l'événement {reply_to_event_id}")
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                "⚠️ Impossible de récupérer l'événement mentionné. Veuillez réessayer ou contacter l'administrateur.",
                msgtype="m.notice"
            )
            return

        # Log le type de l'événement original
        logger.debug(f"Type de l'événement original: {type(original_event).__name__}")
        if hasattr(original_event, 'source'):
            logger.debug(f"Source de l'événement original: {original_event.source}")
        
        # Vérifier si l'événement original contient un fichier
        has_file = False
        file_url = None
        file_name = None
        file_mimetype = None
        file_size = None
        is_encrypted = False
        encryption_info = {}
        
        # Vérifier différentes structures possibles d'événements
        if hasattr(original_event, 'source') and 'content' in original_event.source:
            content = original_event.source['content']
            
            # Vérifier s'il s'agit d'un fichier (m.file, m.image, etc.)
            msgtype = content.get('msgtype')
            logger.debug(f"Message type: {msgtype}")
            
            # Vérifier les URL et attributs de fichier dans différentes structures
            if 'url' in content:
                has_file = True
                file_url = content['url']
                file_name = content.get('body', 'fichier.pdf')
                
                # Vérifier les infos du fichier
                if 'info' in content:
                    file_mimetype = content['info'].get('mimetype')
                    file_size = content['info'].get('size')
                    
                # Vérifier s'il est chiffré
                if 'file' in content:
                    is_encrypted = True
                    encryption_info = content.get('file', {})
                    file_url = encryption_info.get('url', file_url)
        
        if not has_file:
            logger.warning(f"L'événement {reply_to_event_id} ne semble pas contenir de fichier")
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                "⚠️ L'événement mentionné ne semble pas contenir de fichier. Veuillez répondre à un message avec une pièce jointe.",
                msgtype="m.notice"
            )
            return

        # Mettre à jour le message de progression
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            "🔄 Pièce jointe trouvée, téléchargement en cours...",
            msgtype="m.notice"
        )

        # Extraire les informations du fichier avec l'approche simplifiée
        try:
            # Version simplifiée et plus directe pour extraire les informations du fichier
            # S'inspirer de la version fonctionnelle
            file_info = {}
            
            # Récupérer directement depuis la source comme dans la version fonctionnelle
            if hasattr(original_event, 'source') and 'content' in original_event.source:
                content = original_event.source['content']
                file_info = {
                    "filename": content.get('body', 'fichier.bin'),
                    "mimetype": content.get('info', {}).get('mimetype', 'application/octet-stream'),
                    "size": content.get('info', {}).get('size', 0),
                    "encrypted": isinstance(content.get('file', None), dict),
                    "url": content.get('url'),
                    "sender": original_event.sender if hasattr(original_event, 'sender') else ep.sender
                }
            # Fallback pour les objets RoomMessageFile standards
            elif isinstance(original_event, RoomMessageFile):
                file_info = {
                    "filename": original_event.body,
                    "mimetype": original_event.info.get('mimetype', 'application/octet-stream'),
                    "size": original_event.info.get('size', 0),
                    "encrypted": hasattr(original_event, 'key') and hasattr(original_event, 'iv'),
                    "url": original_event.url if hasattr(original_event, 'url') else None,
                    "sender": original_event.sender
                }
                
            logger.info(f"Informations du fichier: {file_info}")
        except Exception as file_info_err:
            logger.error(f"Erreur lors de l'extraction des informations du fichier: {str(file_info_err)}")
            logger.error(f"Type d'événement: {type(original_event).__name__}")
            if hasattr(original_event, 'source'):
                logger.error(f"Source de l'événement: {original_event.source}")
                
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"❌ Erreur lors de l'analyse des informations du fichier: {str(file_info_err)}",
                msgtype="m.notice"
            )
            return

        # Télécharger le fichier
        file_content = None
        temp_file_path = None
        try:
            # Créer un fichier temporaire
            file_ext = os.path.splitext(file_info["filename"])[1]
            temp_fd, temp_file_path = tempfile.mkstemp(suffix=file_ext)
            os.close(temp_fd)
            
            # Télécharger le fichier simplifié comme dans la version fonctionnelle
            if file_info.get("url"):
                # Pour les fichiers non chiffrés avec URL
                logger.info("Téléchargement du fichier depuis URL...")
                response = await matrix_client.download(file_info["url"])
                file_content = response.body
                
                # Écrire le contenu dans le fichier temporaire
                with open(temp_file_path, "wb") as f:
                    f.write(file_content)
            elif file_info.get("encrypted", False):
                # Cas d'un fichier chiffré
                logger.info("Téléchargement du fichier chiffré...")
                # Utiliser la fonction get_decrypted_file
                if hasattr(original_event, 'key') and hasattr(original_event, 'iv') and hasattr(original_event, 'hashes'):
                    file_content = await get_decrypted_file(
                        matrix_client=matrix_client,
                        event=original_event
                    )
                    # Écrire le contenu dans le fichier temporaire
                    with open(temp_file_path, "wb") as f:
                        f.write(file_content)
                else:
                    raise ValueError("Informations de chiffrement manquantes pour le fichier")
            else:
                raise ValueError("Aucune méthode de téléchargement disponible pour ce fichier")
            
            # Mettre à jour le message de progression
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"✅ Fichier '{file_info['filename']}' téléchargé avec succès ({file_info['size']} octets)",
                msgtype="m.notice"
            )
        except Exception as download_err:
            logger.error(f"Erreur lors du téléchargement du fichier: {str(download_err)}")
            logger.error(f"Type d'événement: {type(original_event).__name__}")
            if hasattr(original_event, 'source'):
                logger.error(f"Source de l'événement pour téléchargement: {original_event.source}")
                
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"❌ Erreur lors du téléchargement du fichier: {str(download_err)}",
                msgtype="m.notice"
            )
            if temp_file_path and os.path.exists(temp_file_path):
                os.unlink(temp_file_path)
            return
        
        # Informer l'utilisateur que l'analyse commence
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            "🔄 Analyse du contenu et suggestion de classement...",
            msgtype="m.notice"
        )

        # Récupérer la configuration pour les services
        config = getattr(matrix_client, "albert_config", matrix_client.config)
        
        # Récupérer ou créer le contexte de session avec notre fonction unifiée
        session_context = await get_unified_session_context(config, ep.room.room_id, ep.sender)
        session_id = f"{ep.room.room_id}_{ep.sender}"
        
        # Mettre à jour l'état de conversation pour la commande pj
        if not hasattr(session_context, "conversation_state"):
            session_context.conversation_state = {}
            
        # Log de l'état avant mise à jour
        logger.info(f"[DEBUG PJ] État du conversation_state avant mise à jour: {session_context.conversation_state}")
            
        # Utiliser update() au lieu de réassigner pour préserver les marqueurs de thread
        # Étape 1: action "analyser" - Téléchargement et analyse du fichier
        session_context.conversation_state.update({
                "command_type": "pj",
                "last_command": "pj",
            "action": "analyser"
        })
        
        # Log de l'état après mise à jour
        logger.info(f"[DEBUG PJ] État du conversation_state après mise à jour: {session_context.conversation_state}")
        
        # Récupérer le gestionnaire de contexte global
        context_manager = get_context_manager(config)
        
        # Mettre à jour le contexte
        await context_manager.update_context(
            session_id,
            ContextType.SESSION,
            session_context.to_dict()
        )
            
        # Initialiser le service WebDAV
        webdav = WebDAVService(config)
        await webdav.initialize()
        
        # Obtenir la liste des dossiers disponibles
        available_folders = []
        try:
            # Récupérer la liste des dossiers racine dans WebDAV
            root_folders = await webdav.list_directory("/")
            
            # Filtrer pour ne garder que les dossiers (pas les fichiers)
            available_folders = [
                folder for folder in root_folders 
                if folder.get('type') == 'directory' and not folder.get('name').startswith('.')
            ]
            
            # Ajouter les sous-dossiers du premier niveau
            full_folder_list = available_folders.copy()
            for folder in available_folders:
                folder_path = f"/{folder['name']}"
                subfolders = await webdav.list_directory(folder_path)
                for subfolder in subfolders:
                    if subfolder.get('type') == 'directory' and not subfolder.get('name').startswith('.'):
                        subfolder_full_path = f"{folder_path}/{subfolder['name']}"
                        subfolder_info = subfolder.copy()
                        subfolder_info['full_path'] = subfolder_full_path
                        subfolder_info['parent'] = folder['name']
                        full_folder_list.append(subfolder_info)
            
            available_folders = full_folder_list
            logger.info(f"Dossiers disponibles: {len(available_folders)}")
        except Exception as folders_err:
            logger.error(f"Erreur lors de la récupération des dossiers WebDAV: {str(folders_err)}")
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"⚠️ Impossible de récupérer la liste des dossiers disponibles: {str(folders_err)}",
                msgtype="m.notice"
            )
            # Continuer avec une liste vide
        
        # Préparer la liste des dossiers pour l'affichage
        folder_structure = []
        for folder in available_folders:
            if 'parent' in folder:
                folder_display = f"- {folder['parent']}/{folder['name']}"
                folder_structure.append(folder_display)
            else:
                folder_display = f"- {folder['name']}"
                folder_structure.append(folder_display)
        
        folder_list_text = "\n".join(folder_structure)
        
        # Analyser le fichier pour suggérer un classement
        file_type = file_info.get("mimetype", "").split("/")[0]
        file_extension = os.path.splitext(file_info["filename"])[1].lower()
        
        # Utiliser LLM pour analyser et suggérer des dossiers
        try:
            # Extraction simple d'informations supplémentaires basées sur le type de fichier
            file_info_text = ""
            
            # Pour les documents textuels, extraire un aperçu du contenu
            if file_type == "text" or file_extension in [".txt", ".md", ".csv"]:
                try:
                    # Lire les 1000 premiers caractères pour l'aperçu
                    preview_text = file_content.decode('utf-8', errors='ignore')[:1000]
                    file_info_text = f"Aperçu du contenu:\n{preview_text}\n\n"
                except Exception as text_err:
                    logger.warning(f"Impossible d'extraire l'aperçu du texte: {str(text_err)}")
            
            # Construire un prompt pour l'analyse LLM
            albert_client = AlbertApiClient(
                base_url=config.albert_api_url,
                api_key=config.albert_api_token
            )
            
            # Construire le prompt pour l'analyse
            prompt = f"""Analyse ce fichier et propose une classification intelligente dans la structure de dossiers disponible.

Informations sur le fichier:
- Nom: {file_info["filename"]}
- Taille: {file_info["size"]} octets
- Type MIME: {file_info["mimetype"]}
{file_info_text}

Dossiers disponibles:
{folder_list_text}

Analyse le nom du fichier, son type et son contenu (si disponible) pour suggérer le dossier le plus approprié.
Propose également 2-3 autres dossiers alternatifs qui pourraient être pertinents, avec un niveau de confiance pour chaque suggestion.

IMPORTANT: Propose les dossiers en utilisant des noms relatifs, sans inclure le dossier racine dans le chemin. 
Par exemple, si le dossier disponible est "dossiers-espace-demonstration-2/Marchés", propose simplement "Marchés".

Format de réponse:
1. **Classification principale** : [nom du dossier] (niveau de confiance %)

2. **Alternatives** : 
   - [nom du dossier alternatif 1] (niveau de confiance %)
   - [nom du dossier alternatif 2] (niveau de confiance %)

### Explication : 
[justification courte de la classification principale]

IMPORTANT: L'explication doit être dans une section séparée, pas numérotée comme une alternative.
"""
            
            # Générer les suggestions avec l'API
            try:
                analysis_response = await albert_client.generate(
                    model=config.albert_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=1000
                )
                
                logger.info(f"Réponse d'analyse: {analysis_response[:500]}...")
                
                # Parser la réponse pour extraire les suggestions
                suggested_folder = None
                alternatives = []
                explanation = ""
                
                # Analyse de la réponse LLM
                in_explanation_section = False  # Marque si nous sommes dans la section d'explication
                
                for line in analysis_response.split('\n'):
                    # Détecter la section d'explication
                    if "Explication" in line or "### Explication" in line:
                        in_explanation_section = True
                        # Récupérer le texte après "Explication :"
                        explanation_match = re.search(r'Explication\s*:\s*(.+)', line)
                        if explanation_match:
                            explanation = explanation_match.group(1).strip()
                        continue  # Passer à la ligne suivante
                    
                    # Si nous sommes dans la section d'explication, continuer à collecter l'explication
                    if in_explanation_section:
                        explanation += " " + line.strip()
                        continue  # Passer aux alternatives une fois que nous avons commencé à collecter l'explication
                    
                    # Traiter la classification principale
                    if "Classification principale" in line or "dossier principal" in line:
                        # Extraire le nom du dossier et la confiance
                        folder_match = re.search(r':\s*(?:\[)?([^(\]]+)(?:\])?', line)
                        confidence_match = re.search(r'(\d+)(?:\.\d+)?(?:\s*)?%', line)
                        
                        if folder_match:
                            suggested_folder = folder_match.group(1).strip()
                            confidence = float(confidence_match.group(1))/100 if confidence_match else 0.7
                    
                    # Ignorer les titres de sections
                    elif "Alternatives" in line or "Alternative" in line:
                        continue  # Juste un titre
                    
                    # Traiter les alternatives (lignes commençant par - ou *)
                    elif line.strip().startswith("-") or line.strip().startswith("*"):
                        # Extraire le dossier alternatif et la confiance
                        alt_folder_match = re.search(r'(?:\[)?([^(\]]+)(?:\])?', line)
                        alt_confidence_match = re.search(r'(\d+)(?:\.\d+)?(?:\s*)?%', line)
                        
                        if alt_folder_match:
                            alt_folder = alt_folder_match.group(1).strip()
                            
                            # Vérifier que c'est un chemin de dossier valide (pas une explication)
                            if "explication" not in alt_folder.lower() and "justification" not in alt_folder.lower():
                                alt_confidence = float(alt_confidence_match.group(1))/100 if alt_confidence_match else 0.5
                                alternatives.append((alt_folder, alt_confidence))
                
                # Si aucun dossier n'est suggéré, utiliser un dossier par défaut
                if not suggested_folder and available_folders:
                    suggested_folder = available_folders[0]['name']
                    explanation = "Aucune suggestion spécifique n'a été trouvée. Utilisation du dossier par défaut."
                
                # Nettoyer les suggestions pour éviter la redondance avec le dossier racine
                # Déterminer le dossier racine à partir des dossiers disponibles
                root_folder = None
                if available_folders and len(available_folders) > 0:
                    # Prendre le préfixe commun le plus fréquent comme dossier racine
                    folder_paths = [f.get('name', '') for f in available_folders]
                    common_prefixes = {}
                    for path in folder_paths:
                        parts = path.split('/')
                        if parts and parts[0]:
                            if parts[0] not in common_prefixes:
                                common_prefixes[parts[0]] = 0
                            common_prefixes[parts[0]] += 1
                    
                    # Trouver le préfixe le plus fréquent
                    if common_prefixes:
                        root_folder = max(common_prefixes.items(), key=lambda x: x[1])[0]
                        logger.info(f"Dossier racine détecté: {root_folder}")
                
                # Nettoyer la suggestion principale
                if root_folder and suggested_folder and suggested_folder.startswith(f"{root_folder}/"):
                    # Retirer le préfixe du dossier racine
                    suggested_folder = suggested_folder[len(root_folder)+1:]
                    logger.info(f"Suggestion nettoyée: {suggested_folder}")
                
                # Nettoyer également les alternatives
                cleaned_alternatives = []
                for alt_folder, confidence in alternatives:
                    if root_folder and alt_folder.startswith(f"{root_folder}/"):
                        # Retirer le préfixe du dossier racine
                        alt_folder = alt_folder[len(root_folder)+1:]
                    cleaned_alternatives.append((alt_folder, confidence))
                alternatives = cleaned_alternatives
                
                # Formater le chemin complet
                if root_folder:
                    # Ajouter le dossier racine si la suggestion n'en contient pas
                    if suggested_folder.startswith('/'):
                        target_path = suggested_folder  # Déjà un chemin absolu
                    else:
                        target_path = f"/{root_folder}/{suggested_folder}"
                else:
                    # Pas de dossier racine détecté, utiliser le chemin tel quel
                    target_path = suggested_folder
                    if not target_path.startswith('/'):
                        target_path = '/' + target_path

                if not target_path.endswith('/'):
                    target_path += '/'
                    
                # Nettoyer le nom de fichier pour éviter les caractères problématiques
                safe_filename = file_info["filename"]
                safe_filename = safe_filename.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
                
                # Assainir le chemin pour éviter les problèmes d'encodage
                target_path = target_path.strip()
                
                # Vérifier les caractères spéciaux problématiques en début de dossier
                path_parts = target_path.split('/')
                clean_parts = []
                
                for part in path_parts:
                    # Nettoyer les parties du chemin
                    if part:
                        # Supprimer les espaces en début et fin
                        cleaned_part = part.strip()
                        
                        # Si le dossier commence par un tiret suivi d'un espace, le supprimer
                        if cleaned_part.startswith('- '):
                            cleaned_part = cleaned_part[2:].strip()
                        
                        # Éviter les noms vides
                        if not cleaned_part:
                            cleaned_part = "dossier"
                            
                        clean_parts.append(cleaned_part)
                
                # Reconstruire le chemin
                clean_target_path = '/' + '/'.join(filter(None, clean_parts))
                
                if not clean_target_path.endswith('/'):
                    clean_target_path += '/'
                
                # Ajouter le nom de fichier sécurisé
                clean_target_path += safe_filename
                
                # Mise à jour du chemin cible
                target_path = clean_target_path
                
                # Après avoir analysé le fichier et juste avant de présenter les options, 
                # mettre à jour l'action pour refléter l'étape "proposer_classement"
                logger.info(f"[DEBUG PJ] Passage à l'étape 'proposer_classement'")
                session_context.conversation_state.update({
                    "action": "proposer_classement",  # Changement explicite d'étape
                    "file_info": file_info,
                    "suggested_path": target_path,
                    "suggested_folder": suggested_folder,
                    "alternatives": alternatives,
                    "explanation": explanation,
                    "temp_file_path": temp_file_path
                })
                
                # Mettre à jour le contexte avec la nouvelle action
                await context_manager.update_context(
                    session_id,
                    ContextType.SESSION,
                    session_context.to_dict()
                )
                
                # Log détaillé pour débogage du thread
                logger.info(f"[DEBUG PJ THREAD] État du thread avant présentation des options: in_command_thread={session_context.conversation_state.get('in_command_thread')}, thread_command={session_context.conversation_state.get('thread_command')}, command_completed={session_context.conversation_state.get('command_completed', False)}, action={session_context.conversation_state.get('action')}")
                
                # Formatter les alternatives pour l'affichage
                alternatives_text = ""
                if alternatives:
                    alternatives_text = "Autres suggestions :\n"
                    for i, (folder, confidence) in enumerate(alternatives[:3]):
                        # Décoder les caractères URL-encodés dans le nom du dossier
                        display_folder = decode_path_for_display(folder)
                        alternatives_text += f"{i+1}. {display_folder} ({confidence:.0%})\n"
                
                # Créer le message de réponse
                response_message = f"""📋 **Analyse du fichier** : {file_info["filename"]}

📁 **Classification suggérée** : {decode_path_for_display(suggested_folder)} ({session_context.conversation_state.get('confidence', 0.7):.0%})

{alternatives_text}
🔄 **Actions possibles** :
1. Utiliser la suggestion principale
2. Utiliser une alternative (répondez avec le numéro)
3. Spécifier un autre dossier (répondez avec le nom du dossier)
4. Annuler le classement (répondez "annuler")

### Explication :
{explanation}
"""
                
                # Envoyer le message final
                await matrix_client.send_markdown_message(
                    ep.room.room_id,
                    response_message,
                    msgtype="m.notice"
                )
                
                # Vérifier à nouveau l'état du thread pour confirmation
                session_context = await get_unified_session_context(config, ep.room.room_id, ep.sender)
                logger.info(f"[DEBUG PJ THREAD] État du thread après présentation des options: in_command_thread={session_context.conversation_state.get('in_command_thread')}, thread_command={session_context.conversation_state.get('thread_command')}, command_completed={session_context.conversation_state.get('command_completed', False)}, action={session_context.conversation_state.get('action')}")
                
                logger.debug(f"Options de classification présentées pour le fichier {file_info['filename']}")
                
            except Exception as analysis_err:
                logger.error(f"Erreur lors de l'analyse LLM: {str(analysis_err)}")
                
                # Message d'erreur avec fallback
                error_message = f"""📋 **Analyse du fichier** : {file_info["filename"]}

⚠️ Erreur lors de l'analyse intelligente: {str(analysis_err)}

🔄 **Actions possibles** :
1. Enregistrer dans le dossier racine
2. Spécifier un emplacement manuellement
3. Annuler

Répondez avec le numéro de votre choix (1, 2 ou 3).
"""
                
                # Mettre à jour le contexte avec les informations de base
                session_context.conversation_state = {
                    "command_type": "pj",
                    "action": "classify",
                    "file_info": file_info,
                    "suggested_path": f"/{file_info['filename']}",
                    "temp_file_path": temp_file_path,
                    "error_mode": True
                }
                
                # Récupérer le gestionnaire de contexte global
                context_manager = get_context_manager(config)
                
                await context_manager.update_context(
                    session_id,
                    ContextType.SESSION,
                    session_context.to_dict()
                )
                
                # Envoyer le message d'erreur
                await matrix_client.send_markdown_message(
                    ep.room.room_id,
                    error_message,
                    msgtype="m.notice"
                )
                
        except Exception as e:
            logger.error(f"Erreur lors de l'analyse du fichier: {str(e)}")
            traceback.print_exc()
            
            # Nettoyer le fichier temporaire si nécessaire
            if temp_file_path and os.path.exists(temp_file_path):
                try:
                    os.unlink(temp_file_path)
                    logger.debug(f"Fichier temporaire supprimé: {temp_file_path}")
                except Exception as clean_err:
                    logger.warning(f"Erreur lors de la suppression du fichier temporaire: {str(clean_err)}")
            
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"❌ Erreur lors de l'analyse du fichier : {str(e)}",
                msgtype="m.notice"
            )

    except Exception as e:
        logger.error(f"Erreur générale dans le traitement de la commande pj: {str(e)}")
        traceback.print_exc()
        
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            f"❌ Une erreur est survenue : {str(e)}",
            msgtype="m.notice"
        )

@register_feature(
    group="document",
    onEvent=RoomMessageText,
    command="",  # Pas de commande spécifique, ce handler est pour la réponse à !pj
    help=""
)
@only_allowed_user
@thread_response("pj")
async def handle_attachments_response(ep: EventParser, matrix_client: MatrixClient):
    """
    Fonction auxiliaire pour traiter les réponses aux commandes de pièces jointes.
    Cette fonction gère les réponses après l'analyse d'une pièce jointe.
    """
    # LOGS DE DIAGNOSTIC
    logger.info(f"[PJ DEBUG] ====== DÉMARRAGE handle_attachments_response ======")
    logger.info(f"[PJ DEBUG] Type d'événement: {type(ep.event).__name__}")
    logger.info(f"[PJ DEBUG] Event ID: {getattr(ep.event, 'event_id', 'Inconnu')}")
    logger.info(f"[PJ DEBUG] Sender: {ep.sender}")
    logger.info(f"[PJ DEBUG] Room ID: {ep.room.room_id}")
    
    try:
        # Utiliser albert_config si disponible, sinon utiliser la config standard
        config = getattr(matrix_client, "albert_config", matrix_client.config)
        room_id = ep.room.room_id
        sender = ep.sender
        
        # Extraire la réponse de l'utilisateur
        choice_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
        logger.info(f"[PJ DEBUG] Choix utilisateur: '{choice_text}'")
        
        # Vérifier si le message est une commande (commence par "!") et l'ignorer dans ce cas
        if choice_text.startswith(COMMAND_PREFIX):
            logger.debug(f"[PJ DEBUG] Message '{choice_text}' ignoré car c'est une commande")
            return None
        
        # Vérifier si nous sommes bien dans un thread de commande "pj"
        from app.commands.registry import is_in_active_command_thread
        logger.info(f"[PJ DEBUG] Vérification si dans un thread actif...")
        is_in_thread, thread_command = await is_in_active_command_thread(
            room_id, sender, config
        )
        
        logger.info(f"[PJ DEBUG] Vérification thread pour réponse '{choice_text}': is_in_thread={is_in_thread}, thread_command={thread_command or 'None'}")
        
        # Cas spécial : si thread_command est None ou vide mais que la réponse ressemble à un chiffre 1-3,
        # nous allons quand même tenter de traiter la réponse comme si c'était pour pj
        if not is_in_thread and re.match(r"^\s*[1-3]\s*$", choice_text):
            logger.info(f"[PJ DEBUG] Thread non détecté mais réponse '{choice_text}' ressemble à un choix PJ, tentative de récupération du contexte")
            try:
                # Récupérer les données du fichier depuis le contexte
                logger.info(f"[PJ DEBUG] Récupération du contexte de session")
                session_context = await get_unified_session_context(config, room_id, sender)
                conversation_state = session_context.conversation_state
                logger.info(f"[PJ DEBUG] État de conversation récupéré")
                
                # Vérifier si nous avons les informations de base pour un traitement PJ
                if conversation_state.get("file_info") and conversation_state.get("temp_file_path"):
                    current_action = conversation_state.get("action", "")
                    logger.info(f"[PJ DEBUG] Contexte PJ trouvé malgré l'absence de thread actif, action={current_action}")
                    
                    # Vérifier si nous sommes à l'étape proposer_classement
                    if current_action == "proposer_classement":
                        logger.info(f"[PJ DEBUG] Action 'proposer_classement' détectée, forçage du traitement comme réponse PJ")
                        # Forcer is_in_thread à True et thread_command à "pj" pour traiter cette réponse
                        is_in_thread = True
                        thread_command = "pj"
                    else:
                        logger.info(f"[PJ DEBUG] Action '{current_action}' non compatible avec le traitement de réponse")
            except Exception as e:
                logger.warning(f"[PJ DEBUG] Erreur lors de la tentative de récupération du contexte: {str(e)}")
        
        if not is_in_thread or (thread_command != "pj" and thread_command != "handle_attachments_command"):
            # Si nous ne sommes pas dans un thread "pj", ignorer ce message
            logger.debug(f"[PJ DEBUG] Message ignoré: pas dans un thread pj ou thread différent (actuel: {thread_command})")
            return None
            
        # À ce stade, l'utilisateur a fait un choix valide (1, 2, ou 3)
        # Traitement du choix de l'utilisateur
        try:
            # Récupérer les données du fichier depuis le contexte
            logger.info(f"[PJ DEBUG] Récupération du contexte pour le choix")
            session_context = await get_unified_session_context(config, room_id, sender)
            conversation_state = session_context.conversation_state
            
            logger.info(f"[PJ DEBUG] État de conversation avant traitement: {conversation_state}")
            
            # IMPORTANT: Récupérer d'abord les informations du fichier avant de les utiliser
            # Récupération des informations depuis le contexte de session
            file_info = conversation_state.get("file_info", {})
            temp_file_path = conversation_state.get("temp_file_path", "")
            suggested_folder = conversation_state.get("suggested_folder", "")
            alternatives = conversation_state.get("alternatives", [])
            
            logger.info(f"[PJ DEBUG] Infos récupérées - Fichier: {file_info.get('filename', 'inconnu')}, Dossier suggéré: {suggested_folder}")
            
            # Vérifier que nous sommes bien à l'étape "proposer_classement"
            current_action = conversation_state.get("action", "")
            if current_action != "proposer_classement":
                logger.warning(f"[PJ DEBUG] Action inattendue: {current_action}, attendu: proposer_classement")
                # Si nous ne sommes pas à la bonne étape, informer l'utilisateur et s'arrêter
            await matrix_client.send_markdown_message(
                    room_id,
                    f"⚠️ Impossible de traiter votre réponse: l'état du processus n'est pas correct (action: {current_action}). Veuillez réessayer la commande !pj.",
                msgtype="m.notice"
            )
            
            # Marquer la fin du thread avec un statut d'erreur
            await CommandThread.end(
                room_id, 
                sender, 
                "pj", 
                config,
                action="erreur",
                error=f"Action inattendue: {current_action}",
                étape_finale="erreur_sequence"
            )
            return None
            
            # Log détaillé après récupération des informations du fichier
            logger.info(f"[PJ DEBUG] Traitement de la réponse '{choice_text}' pour le fichier {file_info.get('filename')}")
            
            # Validation des données minimales requises
            if not file_info or not temp_file_path:
                raise ValueError("Informations manquantes pour traiter la réponse: fichier ou chemin temporaire non trouvé")
            
            # Mise à jour de l'action vers "classer"
            conversation_state.update({
                "action": "classer",
                "user_choice": choice_text  # Enregistrer le choix de l'utilisateur
            })
            
            # Mettre à jour le contexte
            session_id = f"{room_id}_{sender}"
            logger.info(f"[PJ DEBUG] Mise à jour du contexte - action=classer, choix={choice_text}")
            ctx_manager = get_context_manager(config)
            await ctx_manager.update_context(
                session_id,
                ContextType.SESSION,
                session_context.to_dict()
            )
            
            # Log après mise à jour pour confirmer le changement d'action
            logger.info(f"[PJ DEBUG] Action mise à jour: de 'proposer_classement' à 'classer' - Choix utilisateur: {choice_text}")
            logger.info(f"[PJ DEBUG] État du conversation_state après mise à jour de l'action: {conversation_state}")
            
            # Déterminer le chemin choisi en fonction de la réponse
            chosen_path = ""
            
            if choice_text == "1":
                # Utiliser la suggestion principale
                chosen_path = suggested_folder
                logger.info(f"[PJ DEBUG] Utilisateur a choisi la suggestion principale: {chosen_path}")
            elif choice_text == "2" and alternatives and len(alternatives) >= 1:
                # Utiliser la première alternative
                chosen_path = alternatives[0][0]
                logger.info(f"[PJ DEBUG] Utilisateur a choisi la première alternative: {chosen_path}")
            elif choice_text == "3":
                # L'utilisateur veut spécifier un autre emplacement
                # Pour l'instant, utiliser un emplacement par défaut
                chosen_path = "dossiers-espace-demonstration-2"
                logger.info(f"[PJ DEBUG] Utilisateur a choisi de spécifier un emplacement (utilisation de l'emplacement par défaut)")
            else:
                # Réponse non reconnue, utiliser la suggestion principale par défaut
                chosen_path = suggested_folder
                logger.warning(f"[PJ DEBUG] Réponse '{choice_text}' non reconnue, utilisation de la suggestion principale par défaut")
            
            # S'assurer que le chemin est valide
            if not chosen_path:
                raise ValueError("Impossible de déterminer un emplacement valide pour le fichier")
                
            # Sauvegarder le fichier dans WebDAV
            logger.info(f"[PJ DEBUG] Initialisation du service WebDAV")
            webdav_service = WebDAVService(config)
            await webdav_service.initialize()
            logger.info(f"[PJ DEBUG] Service WebDAV initialisé")
            
            # Extraire le nom du fichier
            file_name = file_info.get("filename", "fichier_sans_nom.pdf")
        
        # Construire le chemin complet
            if not chosen_path.endswith('/'):
                chosen_path += '/'
            full_path = f"{chosen_path}{file_name}"
            logger.info(f"[PJ DEBUG] Chemin complet pour upload: {full_path}")
            
            # Vérifier si le fichier temporaire existe
            if not os.path.exists(temp_file_path):
                logger.error(f"[PJ DEBUG] ERREUR: Le fichier temporaire n'existe pas: {temp_file_path}")
                raise FileNotFoundError(f"Le fichier temporaire n'existe pas: {temp_file_path}")
                
            # Ouvrir le fichier depuis le chemin temporaire
            logger.info(f"[PJ DEBUG] Lecture du fichier temporaire: {temp_file_path}")
            with open(temp_file_path, 'rb') as f:
                file_content = f.read()
                logger.info(f"[PJ DEBUG] Fichier lu avec succès: {len(file_content)} octets")
                
            # Télécharger le fichier sur le serveur WebDAV avec timeout
            try:
                logger.info(f"[PJ DEBUG] Tentative de téléchargement du fichier vers {full_path}")
                
                # Utiliser asyncio.wait_for pour ajouter un timeout au téléchargement
                upload_timeout = 30.0  # 30 secondes de timeout pour l'upload
                logger.info(f"[PJ DEBUG] Début du téléchargement avec timeout de {upload_timeout}s")
                
                try:
                    upload_success = await asyncio.wait_for(
                        webdav_service.upload_file(file_content, full_path),
                        timeout=upload_timeout
                    )
                    logger.info(f"[PJ DEBUG] Téléchargement terminé: succès={upload_success}")
                except asyncio.TimeoutError:
                    logger.error(f"[PJ DEBUG] TIMEOUT: Le téléchargement a dépassé {upload_timeout}s")
                    raise TimeoutError(f"Le téléchargement a pris trop de temps (>{upload_timeout}s)")
                
                if not upload_success:
                    # Si le téléchargement échoue
                    error_message = "Le serveur a retourné une erreur lors du téléchargement."
                    logger.error(f"[PJ DEBUG] Échec du téléchargement vers WebDAV pour {full_path}: {error_message}")
                    
                    # Envoyer un message d'erreur
                    await matrix_client.send_markdown_message(
                        room_id,
                        f"⚠️ Impossible de classer le fichier dans NextCloud. Le serveur a retourné une erreur."
                    )
                    
                    # Marquer la fin du thread avec un statut d'erreur
                    await CommandThread.end(
                        room_id, 
                        sender, 
                        "pj", 
                        config,
                        action="erreur",
                        error_message="Échec du téléchargement WebDAV",
                        étape_finale="erreur_webdav",
                        file_name=file_info.get('filename')
                    )
                    
                    logger.info(f"[PJ DEBUG] Fin du thread marquée avec état d'erreur WebDAV")
                    return None
                    
            except Exception as upload_error:
                # Gestion des erreurs de téléchargement
                error_message = f"Erreur lors du téléchargement WebDAV: {str(upload_error)}"
                logger.error(f"[PJ DEBUG] {error_message}\n{traceback.format_exc()}")
                
                # Envoyer un message d'erreur à l'utilisateur
                await matrix_client.send_markdown_message(
                    room_id,
                    f"⚠️ Impossible de classer le fichier dans NextCloud. Erreur: {str(upload_error)[:100]}..."
                )
                
                # Marquer la fin du thread avec un statut d'erreur
                await CommandThread.end(
                    room_id, 
                    sender, 
                    "pj", 
                    config,
                    action="erreur",
                    error=str(upload_error),
                    étape_finale="exception_webdav",
                    file_name=file_info.get('filename')
                )
                
                logger.info(f"[PJ DEBUG] Fin du thread marquée avec état d'exception WebDAV")
                return None
            
            # Nettoyer le fichier temporaire après l'upload
            try:
                logger.info(f"[PJ DEBUG] Nettoyage du fichier temporaire: {temp_file_path}")
                if os.path.exists(temp_file_path):
                    os.unlink(temp_file_path)
                    logger.info(f"[PJ DEBUG] Fichier temporaire supprimé avec succès")
                else:
                    logger.warning(f"[PJ DEBUG] Le fichier temporaire n'existe plus: {temp_file_path}")
            except Exception as clean_err:
                logger.warning(f"[PJ DEBUG] Erreur lors de la suppression du fichier temporaire: {str(clean_err)}")
                
            # Construire le message de confirmation exactement au format souhaité
            confirmation_message = f"✅ Le fichier '{file_name}' a été classé avec succès dans '{decode_path_for_display(chosen_path)}'."
            
            # Envoyer le message de confirmation
            logger.info(f"[PJ DEBUG] Envoi du message de confirmation")
            await matrix_client.send_markdown_message(
                room_id,
                confirmation_message
            )
            
            logger.info(f"[PJ DEBUG] Message de confirmation envoyé: '{confirmation_message}'")
            logger.info(f"[PJ DEBUG] Marquage de fin de thread immédiatement après confirmation...")
            
            # Marquer explicitement la fin du thread de commande JUSTE APRÈS l'envoi du message de confirmation
            await CommandThread.end(
                room_id, 
                sender, 
                "pj", 
                config,
                action="classé",  # Action finale : fichier classé avec succès
                file_name=file_info.get('filename'),
                target_path=chosen_path,
                étape_finale="classer_terminé"  # Indiquer clairement l'étape finale
            )
            
            logger.info(f"[PJ DEBUG] Fin du thread marquée avec succès, action finale: classé")
            logger.info(f"[PJ DEBUG] Commande !pj terminée pour le fichier {file_info.get('filename')}. Prêt pour le dialogue.")
            
            # Fermer proprement le service WebDAV
            logger.info(f"[PJ DEBUG] Fermeture du service WebDAV")
            await webdav_service.close()
            logger.info(f"[PJ DEBUG] Service WebDAV fermé")
            
            # Retourner explicitement None pour permettre à handle_conversation de traiter les réponses
            return None
            
        except Exception as e:
            # Gestion des erreurs générales
            error_message = f"Erreur lors du classement du fichier: {str(e)}"
            logger.error(f"[PJ DEBUG] {error_message}\n{traceback.format_exc()}")
            
            # Récupérer le nom du fichier de façon sécurisée
            file_name = "inconnu"
            try:
                # Tenter de récupérer le nom du fichier depuis le contexte de session
                session_context = await get_unified_session_context(config, room_id, sender)
                conversation_state = session_context.conversation_state
                file_info_safe = conversation_state.get("file_info", {})
                if isinstance(file_info_safe, dict):
                    file_name = file_info_safe.get("filename", "inconnu")
            except Exception as ctx_err:
                logger.error(f"[PJ DEBUG] Erreur supplémentaire lors de la récupération du contexte: {str(ctx_err)}")
            
            # Construire et envoyer un message d'erreur formaté pour l'utilisateur
            user_error_message = f"❌ Une erreur est survenue lors du classement du fichier '{file_name}': {str(e)}"
            
            # Envoyer le message d'erreur à l'utilisateur
            await matrix_client.send_markdown_message(
                room_id,
                user_error_message
            )
            
            logger.info(f"[PJ DEBUG] Message d'erreur envoyé: '{user_error_message}'")
            logger.info(f"[PJ DEBUG] Marquage de fin de thread après message d'erreur...")
            
            # Marquer également la fin du thread en cas d'erreur - JUSTE APRÈS l'envoi du message d'erreur
            try:
                await CommandThread.end(
                    room_id, 
                    sender, 
                    "pj", 
                    config,
                    action="erreur",
                    error=str(e),
                    étape_finale="erreur_générale",
                    file_name=file_name
                )
                
                logger.info(f"[PJ DEBUG] Fin du thread marquée avec succès malgré l'erreur")
                logger.info(f"[PJ DEBUG] Thread de commande 'pj' terminé avec erreur: {str(e)}")
            except Exception as mark_error:
                logger.error(f"[PJ DEBUG] Erreur lors du marquage de fin de thread: {str(mark_error)}")
                # Tenter une dernière fois avec des données minimales
                try:
                    await CommandThread.end(
                        room_id, 
                        sender, 
                        "pj", 
                        config,
                        action="erreur",
                        error="Erreur critique",
                        étape_finale="erreur_critique"
                    )
                    logger.info(f"[PJ DEBUG] Seconde tentative de marquage de fin de thread réussie")
                except Exception as final_error:
                    logger.error(f"[PJ DEBUG] Échec définitif du marquage de fin de thread: {str(final_error)}")
            
            # Retourner explicitement None pour permettre à handle_conversation de traiter les réponses
            return None
    except Exception as global_error:
        # Capture globale pour éviter que l'erreur ne se propage
        logger.error(f"[PJ DEBUG] ERREUR GLOBALE dans handle_attachments_response: {str(global_error)}")
        logger.error(f"[PJ DEBUG] Traceback: {traceback.format_exc()}")
        
        # Tenter d'envoyer un message d'erreur à l'utilisateur
        try:
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"❌ Une erreur critique s'est produite lors du traitement de votre réponse. Les administrateurs ont été notifiés.",
                msgtype="m.notice"
            )
        except Exception as msg_err:
            logger.error(f"[PJ DEBUG] Impossible d'envoyer le message d'erreur: {str(msg_err)}")
        
        # Toujours retourner None pour éviter la propagation de l'erreur
        return None
    finally:
        logger.info(f"[PJ DEBUG] ====== FIN handle_attachments_response ======") 