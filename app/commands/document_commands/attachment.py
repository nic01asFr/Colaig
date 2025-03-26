"""
Module de gestion des pièces jointes.

Ce module contient les commandes pour gérer les pièces jointes dans les messages,
permettant de les télécharger, les analyser et les intégrer à l'index documentaire.
"""

from typing import List, Dict, Any, Optional, Tuple

import asyncio
import traceback
from datetime import datetime
import os
import io
import re
import tempfile
import mimetypes
from pathlib import Path
import shutil

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser
from nio import Event, RoomEncryptedFile, RoomMessageText, RoomMessageFile

from app.bot_msg import AlbertMsg
from app.config import Config
from app.services.webdav import WebDAVService
from app.services.document_index import DocumentIndex
from app.services.index_service import IndexService
from app.services.context import ContextManager, ContextType
from app.tchap_utils import (
    get_decrypted_file,
    isa_reply_to
)
from app.commands.registry import register_feature, only_allowed_user
from app.commands import get_context_manager
from app.core_llm import generate, AlbertApiClient

@register_feature(
    group="document",
    onEvent=RoomMessageText,
    command="pj",
    help="!pj - Analyser les pièces jointes d'un message (utilisez cette commande en réponse à un message)"
)
@only_allowed_user
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
        
        # Le reste du code pour l'analyse et la classification reste similaire
        # Continuer avec le code existant pour le WebDAV, les suggestions, etc.
        
        # Informer l'utilisateur que l'analyse commence
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            "🔄 Analyse du contenu et suggestion de classement...",
            msgtype="m.notice"
        )
        
        # Récupérer la configuration pour les services
        config = getattr(matrix_client, "albert_config", matrix_client.config)
        
        # Initialiser le gestionnaire de contexte
        context_manager = get_context_manager(config)
        
        # Créer ou récupérer le contexte de session
        session_id = f"{ep.room.room_id}_{ep.sender}"
        session_context = await context_manager.get_context(session_id, ContextType.SESSION)
        
        # Si le contexte n'existe pas, le créer
        if not session_context:
            session_data = {
                "session_id": session_id,
                "room_id": ep.room.room_id,
                "user_id": ep.sender,
                "history": [],
                "conversation_state": {
                    "command_type": "pj",  # Préfixe unique pour identifier le type de commande
                    "last_command": "pj",
                    "current_action": "classify"
                }
            }
            session_context = await context_manager.create_context(
                session_id,
                ContextType.SESSION,
                session_data
            )
        else:
            # Mettre à jour l'état de conversation si le contexte existe déjà
            session_context.conversation_state = {
                "command_type": "pj",
                "last_command": "pj",
                "current_action": "classify"
            }
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
Explique brièvement pourquoi chaque dossier est approprié.

Format de réponse:
1. Classification principale : [nom du dossier] (niveau de confiance %)
2. Alternatives : 
   - [nom du dossier alternatif 1] (niveau de confiance %)
   - [nom du dossier alternatif 2] (niveau de confiance %)
3. Explication : [justification de la classification]
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
                for line in analysis_response.split('\n'):
                    if "Classification principale" in line or "dossier principal" in line:
                        # Extraire le nom du dossier et la confiance
                        folder_match = re.search(r':\s*(?:\[)?([^(\]]+)(?:\])?', line)
                        confidence_match = re.search(r'(\d+)(?:\.\d+)?(?:\s*)?%', line)
                        
                        if folder_match:
                            suggested_folder = folder_match.group(1).strip()
                            confidence = float(confidence_match.group(1))/100 if confidence_match else 0.7
                    
                    elif "Alternatives" in line or "Alternative" in line:
                        continue  # Juste un titre
                    
                    elif line.strip().startswith("-") or line.strip().startswith("*"):
                        # Extraire le dossier alternatif et la confiance
                        alt_folder_match = re.search(r'(?:\[)?([^(\]]+)(?:\])?', line)
                        alt_confidence_match = re.search(r'(\d+)(?:\.\d+)?(?:\s*)?%', line)
                        
                        if alt_folder_match:
                            alt_folder = alt_folder_match.group(1).strip()
                            alt_confidence = float(alt_confidence_match.group(1))/100 if alt_confidence_match else 0.5
                            alternatives.append((alt_folder, alt_confidence))
                    
                    elif "Explication" in line:
                        # Récupérer le texte après "Explication :"
                        explanation_match = re.search(r'Explication\s*:\s*(.+)', line)
                        if explanation_match:
                            explanation = explanation_match.group(1).strip()
                    
                    elif explanation and not line.startswith("Classification") and not line.startswith("Alternative"):
                        # Continuer l'explication sur les lignes suivantes
                        explanation += " " + line.strip()
                
                # Si aucun dossier n'est suggéré, utiliser un dossier par défaut
                if not suggested_folder and available_folders:
                    suggested_folder = available_folders[0]['name']
                    explanation = "Aucune suggestion spécifique n'a été trouvée. Utilisation du dossier par défaut."
                
                # Formater le chemin complet
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
                
                # Stocker les informations dans le contexte de session
                session_context.conversation_state = {
                    "command_type": "pj",
                    "action": "classify",
                    "file_info": file_info,
                    "suggested_path": target_path,
                    "suggested_folder": suggested_folder,
                    "alternatives": alternatives,
                    "explanation": explanation,
                    "temp_file_path": temp_file_path
                }
                
                await context_manager.update_context(
                    session_id,
                    ContextType.SESSION,
                    session_context.to_dict()
                )
                
                # Formatter les alternatives pour l'affichage
                alternatives_text = ""
                if alternatives:
                    alternatives_text = "Autres suggestions :\n"
                    for i, (folder, confidence) in enumerate(alternatives[:3]):
                        alternatives_text += f"{i+1}. {folder} ({confidence:.0%})\n"
                
                # Créer le message de réponse
                response_message = f"""📋 **Analyse du fichier** : {file_info["filename"]}

📁 **Classification suggérée** : {suggested_folder} ({session_context.conversation_state.get('confidence', 0.7):.0%})
{explanation}

{alternatives_text}
🔄 **Actions possibles** :
1. Utiliser la suggestion principale
2. Choisir une alternative
3. Spécifier un autre emplacement

Répondez avec le numéro de votre choix (1, 2 ou 3).
"""
                
                # Envoyer le message final
                await matrix_client.send_markdown_message(
                    ep.room.room_id,
                    response_message,
                    msgtype="m.notice"
                )
                
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
async def handle_attachments_response(ep: EventParser, matrix_client: MatrixClient):
    """
    Fonction auxiliaire pour traiter les réponses aux commandes de pièces jointes.
    Cette fonction gère les réponses après l'analyse d'une pièce jointe.
    """
    # Récupérer le contexte de session
    session_id = f"{ep.room.room_id}_{ep.sender}"
    context_manager = get_context_manager(matrix_client.config)
    
    # Récupérer le contexte de session
    session_context = await context_manager.get_context(session_id, ContextType.SESSION)
    
    # Si pas de contexte ou pas d'état de conversation, ce n'est pas une réponse à !pj
    if not session_context or not session_context.conversation_state:
        return
    
    # Vérifier si la dernière commande était !pj
    if session_context.conversation_state.get("command_type") != "pj":
        return
    
    # Récupérer les données du contexte de session
    temp_file_path = session_context.conversation_state.get("temp_file_path")
    
    # Vérifier si le fichier temporaire existe encore
    if not temp_file_path or not os.path.exists(temp_file_path):
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            "❌ Le fichier temporaire n'est plus disponible. Veuillez relancer la commande !pj.",
            msgtype="m.notice"
        )
        return
    
    # Récupérer les informations du fichier du contexte
    file_info = session_context.conversation_state.get("file_info", {})
    if not file_info:
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            "❌ Informations de fichier manquantes. Veuillez relancer la commande !pj.",
            msgtype="m.notice"
        )
        return
    
    # Récupérer le message de l'utilisateur
    user_message = ep.event.body if hasattr(ep.event, 'body') else ""
    user_choice = user_message.strip().split(maxsplit=1)
    
    # Configuration
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    
    # Si on attend un chemin personnalisé
    if session_context.conversation_state.get("waiting_for_path"):
        custom_path = user_message.strip()
        if not custom_path:
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                "❌ Le chemin ne peut pas être vide.",
                msgtype="m.notice"
            )
            return
        
        # Normaliser le chemin
        custom_path = custom_path.replace('\\', '/')
        if not custom_path.startswith('/'):
            custom_path = '/' + custom_path
        
        # Vérifier si le chemin contient le nom du fichier
        if not custom_path.endswith('/'):
            # Vérifier si le dernier segment contient une extension
            last_segment = custom_path.split('/')[-1]
            if '.' not in last_segment:
                # Pas d'extension, donc c'est probablement un dossier
                custom_path += '/'
        
        # Ajouter le nom du fichier si nécessaire
        if custom_path.endswith('/'):
            # Nettoyer le nom de fichier pour éviter les caractères problématiques
            safe_filename = file_info.get("filename", "fichier.bin")
            safe_filename = safe_filename.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
            
            # Assainir le chemin pour éviter les problèmes d'encodage
            custom_path = custom_path.strip()
            
            # Vérifier les caractères spéciaux problématiques en début de dossier
            path_parts = custom_path.split('/')
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
            clean_custom_path = '/' + '/'.join(filter(None, clean_parts))
            
            if not clean_custom_path.endswith('/'):
                clean_custom_path += '/'
            
            # Ajouter le nom de fichier sécurisé
            clean_custom_path += safe_filename
            
            # Mise à jour du chemin
            custom_path = clean_custom_path
        
        # Initialiser le service WebDAV
        webdav = WebDAVService(config)
        await webdav.initialize()
        
        try:
            # Créer le dossier parent si nécessaire
            folder_path = os.path.dirname(custom_path)
            if folder_path != '/':
                try:
                    await webdav.create_directory(folder_path)
                    logger.info(f"Dossier créé: {folder_path}")
                except Exception as folder_err:
                    logger.warning(f"Erreur lors de la création du dossier (peut-être existe-t-il déjà): {str(folder_err)}")
            
            # Téléverser le fichier
            with open(temp_file_path, "rb") as f:
                await webdav.upload_file(f, custom_path)
            
            # Informer l'utilisateur
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"✅ Le fichier '{file_info.get('filename')}' a été classé avec succès dans '{folder_path}'.",
                msgtype="m.notice"
            )
            
            # Nettoyer le fichier temporaire
            try:
                os.unlink(temp_file_path)
                logger.debug(f"Fichier temporaire supprimé: {temp_file_path}")
            except Exception as clean_err:
                logger.warning(f"Erreur lors de la suppression du fichier temporaire: {str(clean_err)}")
            
            # Réinitialiser le contexte de session
            session_context.conversation_state = {}
            await context_manager.update_context(
                session_id,
                ContextType.SESSION,
                session_context.to_dict()
            )
            return
            
        except Exception as upload_err:
            logger.error(f"Erreur lors du téléversement du fichier: {str(upload_err)}")
            traceback.print_exc()
            
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"❌ Erreur lors du classement du fichier: {str(upload_err)}",
                msgtype="m.notice"
            )
            return
    
    # Réponse à un choix d'alternative
    elif session_context.conversation_state.get("waiting_for_alternative"):
        # Vérifier que le message est un chiffre
        if not user_message.strip().isdigit():
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                "❌ Veuillez répondre avec le numéro de l'alternative choisie.",
                msgtype="m.notice"
            )
            return
            
        alt_index = int(user_message.strip()) - 1
        alternatives = session_context.conversation_state.get("alternatives", [])
        
        if alt_index < 0 or alt_index >= len(alternatives):
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"❌ Choix invalide. Veuillez choisir un nombre entre 1 et {len(alternatives)}.",
                msgtype="m.notice"
            )
            return
            
        # Récupérer le dossier choisi
        chosen_folder, _ = alternatives[alt_index]
        
        # Construire le chemin complet
        target_path = chosen_folder
        if not target_path.startswith('/'):
            target_path = '/' + target_path
        
        if not target_path.endswith('/'):
            target_path += '/'
            
        # Nettoyer le nom de fichier pour éviter les caractères problématiques
        safe_filename = file_info.get("filename", "fichier.bin")
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
        
        # Mettre à jour le contexte
        session_context.conversation_state["waiting_for_alternative"] = False
        session_context.conversation_state["target_path"] = target_path
        await context_manager.update_context(
            session_id,
            ContextType.SESSION,
            session_context.to_dict()
        )
        
        # Initialiser le service WebDAV et téléverser le fichier
        webdav = WebDAVService(config)
        await webdav.initialize()
        
        try:
            # Informer l'utilisateur
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"🔄 Classement du fichier dans '{chosen_folder}'...",
                msgtype="m.notice"
            )
            
            # Créer le dossier parent si nécessaire
            folder_path = os.path.dirname(target_path)
            if folder_path != '/':
                try:
                    await webdav.create_directory(folder_path)
                    logger.info(f"Dossier créé: {folder_path}")
                except Exception as folder_err:
                    logger.warning(f"Erreur lors de la création du dossier (peut-être existe-t-il déjà): {str(folder_err)}")
            
            # Téléverser le fichier
            with open(temp_file_path, "rb") as f:
                await webdav.upload_file(f, target_path)
            
            # Informer l'utilisateur
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"✅ Le fichier '{file_info.get('filename')}' a été classé avec succès dans '{chosen_folder}'.",
                msgtype="m.notice"
            )
            
            # Nettoyer le fichier temporaire
            try:
                os.unlink(temp_file_path)
                logger.debug(f"Fichier temporaire supprimé: {temp_file_path}")
            except Exception as clean_err:
                logger.warning(f"Erreur lors de la suppression du fichier temporaire: {str(clean_err)}")
            
            # Réinitialiser le contexte de session
            session_context.conversation_state = {}
            await context_manager.update_context(
                session_id,
                ContextType.SESSION,
                session_context.to_dict()
            )
            
        except Exception as upload_err:
            logger.error(f"Erreur lors du téléversement du fichier: {str(upload_err)}")
            traceback.print_exc()
            
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                f"❌ Erreur lors du classement du fichier: {str(upload_err)}",
                msgtype="m.notice"
            )
        
        return
    
    # Traitement des choix principaux (1-4)
    elif not user_choice or not user_choice[0].isdigit():
        return
        
    choice = int(user_choice[0])
    target_path = None
    
    # Mode d'erreur (options plus limitées)
    if session_context.conversation_state.get("error_mode"):
        if choice == 1:
            # Option 1: Enregistrer dans le dossier racine
            # Nettoyer le nom de fichier pour éviter les caractères problématiques
            safe_filename = file_info.get("filename", "fichier.bin")
            safe_filename = safe_filename.replace('\t', ' ').replace('\n', ' ').replace('\r', ' ')
            target_path = "/" + safe_filename
        elif choice == 2:
            # Option 2: Spécifier manuellement
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                "Veuillez spécifier le chemin complet où enregistrer le fichier:",
                msgtype="m.notice"
            )
            session_context.conversation_state["waiting_for_path"] = True
            await context_manager.update_context(
                session_id,
                ContextType.SESSION,
                session_context.to_dict()
            )
            return
        elif choice == 3:
            # Option 3: Annuler
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                "❌ Opération annulée.",
                msgtype="m.notice"
            )
            
            # Nettoyer le fichier temporaire
            try:
                os.unlink(temp_file_path)
                logger.debug(f"Fichier temporaire supprimé: {temp_file_path}")
            except Exception as clean_err:
                logger.warning(f"Erreur lors de la suppression du fichier temporaire: {str(clean_err)}")
            
            # Réinitialiser le contexte de session
            session_context.conversation_state = {}
            await context_manager.update_context(
                session_id,
                ContextType.SESSION,
                session_context.to_dict()
            )
            return
        else:
            # Choix invalide
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                "❌ Choix invalide. Veuillez répondre avec 1, 2 ou 3.",
                msgtype="m.notice"
            )
            return
    else:
        # Mode normal
        if choice == 1:
            # Option 1: Utiliser la suggestion principale
            target_path = session_context.conversation_state.get("suggested_path")
        elif choice == 2:
            # Option 2: Choisir une alternative
            alternatives = session_context.conversation_state.get("alternatives", [])
            
            if not alternatives:
                await matrix_client.send_markdown_message(
                    ep.room.room_id,
                    "❌ Aucune alternative disponible. Veuillez choisir une autre option.",
                    msgtype="m.notice"
                )
                return
                
            # Afficher les alternatives
            alt_message = "📋 **Alternatives disponibles**:\n\n"
            for i, (folder, confidence) in enumerate(alternatives):
                alt_message += f"{i+1}. {folder} ({confidence:.0%})\n"
            
            alt_message += "\nRépondez avec le numéro de l'alternative choisie."
            
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                alt_message,
                msgtype="m.notice"
            )
            
            # Mettre à jour le contexte pour attendre le choix d'une alternative
            session_context.conversation_state["waiting_for_alternative"] = True
            await context_manager.update_context(
                session_id,
                ContextType.SESSION,
                session_context.to_dict()
            )
            return
            
        elif choice == 3:
            # Option 3: Spécifier un autre emplacement
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                "Veuillez spécifier le chemin complet où enregistrer le fichier:",
                msgtype="m.notice"
            )
            session_context.conversation_state["waiting_for_path"] = True
            await context_manager.update_context(
                session_id,
                ContextType.SESSION,
                session_context.to_dict()
            )
            return
            
        else:
            # Choix invalide
            await matrix_client.send_markdown_message(
                ep.room.room_id,
                "❌ Choix invalide. Veuillez répondre avec 1, 2 ou 3.",
                msgtype="m.notice"
            )
            return
    
    # Si nous arrivons ici, nous avons un chemin cible
    if not target_path:
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            "❌ Erreur lors de la détermination du chemin cible.",
            msgtype="m.notice"
        )
        return
    
    # Initialiser le service WebDAV et téléverser le fichier
    webdav = WebDAVService(config)
    await webdav.initialize()
    
    try:
        # Informer l'utilisateur
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            f"🔄 Classement du fichier en cours...",
            msgtype="m.notice"
        )
        
        # Créer le dossier parent si nécessaire
        folder_path = os.path.dirname(target_path)
        if folder_path != '/':
            try:
                await webdav.create_directory(folder_path)
                logger.info(f"Dossier créé: {folder_path}")
            except Exception as folder_err:
                logger.warning(f"Erreur lors de la création du dossier (peut-être existe-t-il déjà): {str(folder_err)}")
        
        # Téléverser le fichier
        with open(temp_file_path, "rb") as f:
            await webdav.upload_file(f, target_path)
        
        # Informer l'utilisateur
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            f"✅ Le fichier '{file_info.get('filename')}' a été classé avec succès dans '{folder_path}'.",
            msgtype="m.notice"
        )
        
        # Nettoyer le fichier temporaire
        try:
            os.unlink(temp_file_path)
            logger.debug(f"Fichier temporaire supprimé: {temp_file_path}")
        except Exception as clean_err:
            logger.warning(f"Erreur lors de la suppression du fichier temporaire: {str(clean_err)}")
        
        # Réinitialiser le contexte de session
        session_context.conversation_state = {}
        await context_manager.update_context(
            session_id,
            ContextType.SESSION,
            session_context.to_dict()
        )
        
    except Exception as upload_err:
        logger.error(f"Erreur lors du téléversement du fichier: {str(upload_err)}")
        traceback.print_exc()
        
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            f"❌ Erreur lors du classement du fichier: {str(upload_err)}",
            msgtype="m.notice"
        )
        
        # Ne pas nettoyer le fichier temporaire en cas d'erreur pour permettre une nouvelle tentative 