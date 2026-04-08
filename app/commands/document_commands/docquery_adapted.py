"""
Version adaptée de la commande chercher utilisant le nouveau décorateur albert_command.

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

from app.matrix_bot.client import MatrixClient
from app.matrix_bot.config import logger
from app.matrix_bot.eventparser import EventParser

from app.commands.decorators import albert_command
from app.config import Config, get_config
from app.index.types import IndexService, get_index_service
from app.llm import infer_query_plan, QueryPlan, format_answer
from app.services.webdav import WebDAVService
from app.services.webdav_instance import ensure_webdav_initialized
from nio.events.room_events import RoomMessageText
from app.commands import get_room_context

logger = logging.getLogger(__name__)

@albert_command(
    group="document",
    command="chercher",
    aliases=["docq-new"],
    help_text="!chercher [question] - Interroger les documents indexés avec une question en langage naturel",
    preserve_context=True,
    timeout=90.0  # 90 secondes maximum
)
@ensure_webdav_initialized
async def doc_query_adapted_command(ep: EventParser, matrix_client: MatrixClient, webdav_service: Optional[WebDAVService] = None):
    """Interroge les documents indexés avec une question en langage naturel."""
    # Logs de début
    logger.info(f"[CHERCHER] Démarrage de la commande chercher")
    logger.info(f"[CHERCHER] Sender: {ep.sender}, Room ID: {ep.room.room_id}")
    
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    
    # Fonction utilitaire pour construire l'URL WebDAV correctement avec améliorations
    async def build_document_link(base_url: str, username: str, doc_path: str, webdav_service=None, target_user: Optional[str] = None) -> str:
        """
        Construit un lien de document pour le service WebDAV avec gestion du contexte.
        Utilise la stratégie optimale selon le contexte:
        - Partage OCS si target_user est fourni
        - URL WebDAV directe sinon
        
        Args:
            base_url: URL de base du serveur WebDAV
            username: Nom d'utilisateur pour WebDAV
            doc_path: Chemin du document
            webdav_service: Service WebDAV à utiliser (optionnel)
            target_user: Utilisateur cible pour le lien
            
        Returns:
            URL du lien de partage ou WebDAV direct
        """
        # Si un service WebDAV est fourni, l'utiliser
        if webdav_service:
            logger.info(f"[BUILD_LINK] Service WebDAV disponible, tentative de création de lien OCS")
            
            # Nettoyer le chemin
            real_path = doc_path.strip()
            if real_path.startswith('/'):
                real_path = real_path[1:]
            
            # Extraire le nom d'utilisateur local
            user_local = username
            if '@' in user_local:
                user_local = user_local.split('@')[0]
            logger.info(f"[BUILD_LINK] Nom d'utilisateur: complet='{username}', local='{user_local}'")
                
            # Cas 1: Le document est dans un sous-répertoire de l'utilisateur
            if user_local and real_path.startswith(f"{user_local}/"):
                real_path = real_path[len(user_local)+1:]
                logger.info(f"[BUILD_LINK] Cas 1: Suppression du préfixe utilisateur: '{real_path}'")
                
            # Cas 2: Le document est dans WebDAV avec chemin complet
            elif 'remote.php/dav/files/' in real_path:
                parts = real_path.split('remote.php/dav/files/')
                if len(parts) > 1 and '/' in parts[1]:
                    # Extraire la partie après le nom d'utilisateur
                    username_part, file_path = parts[1].split('/', 1)
                    real_path = file_path
                    logger.info(f"[BUILD_LINK] Cas 2: Extraction du chemin après l'utilisateur: '{real_path}'")
            
            # Cas 3: Aucune transformation nécessaire
            else:
                logger.info(f"[BUILD_LINK] Cas 3: Aucune transformation nécessaire: '{real_path}'")

            # S'assurer que le chemin inclut l'espace de travail
            workspace_path = "dossiers-colaig-assistant"  # Valeur par défaut
            if hasattr(webdav_service, 'workspace_path') and webdav_service.workspace_path:
                workspace_path = webdav_service.workspace_path
            elif hasattr(webdav_service, 'config') and hasattr(webdav_service.config, 'webdav_workspace_path'):
                workspace_path = webdav_service.config.webdav_workspace_path
                
            # Vérifier si le chemin contient déjà l'espace de travail
            if not real_path.startswith(f"{workspace_path}/"):
                # Construire le chemin complet incluant l'espace de travail
                if "/" not in real_path:
                    # Récupérer le chemin racine configuré
                    webdav_root_path = "documents"
                    if hasattr(webdav_service, 'config') and hasattr(webdav_service.config, 'webdav_root_path'):
                        webdav_root_path = webdav_service.config.webdav_root_path.strip('/')
                    real_path = f"{workspace_path}/{webdav_root_path}/{real_path}"
                else:
                    real_path = f"{workspace_path}/{real_path}"
                logger.info(f"[BUILD_LINK] Chemin final pour OCS (sans slash): '{real_path}'")
            
            # Si un utilisateur cible est fourni, tenter de créer un lien de partage
            if target_user:
                # Extraire le nom d'utilisateur pour le partage (éliminer @domain:server)
                target_username = target_user
                if '@' in target_username:
                    if ':' in target_username:  # Format Matrix @user:domain
                        target_username = target_username.split(':')[0]
                        if target_username.startswith('@'):
                            target_username = target_username[1:]  # Enlever le @ initial
                    else:  # Format email
                        target_username = target_user.split('@')[0]
                        
                # Gérer les formats spéciaux (prénom.nom-domaine.gouv.fr1)
                if '-' in target_username and '.' in target_username:
                    dot_pos = target_username.find('.')
                    dash_pos = target_username.find('-', dot_pos)
                    if dash_pos > dot_pos:
                        target_username = target_username[:dash_pos]
                        logger.info(f"[BUILD_LINK] Extraction prénom.nom de l'utilisateur cible: {target_user} -> {target_username}")
                
                logger.info(f"[BUILD_LINK] Utilisateur cible traité: {target_user} -> {target_username}")
                
                # Créer un lien de partage OCS
                try:
                    logger.info(f"[BUILD_LINK] Appel create_share_link pour: '{real_path}' avec target_user='{target_username}'")
                    
                    # Récupérer le nom de l'espace de travail pour le logging
                    workspace_name = "dossiers-colaig-assistant"  # Valeur par défaut
                    if hasattr(webdav_service, 'workspace_path') and webdav_service.workspace_path:
                        workspace_name = webdav_service.workspace_path
                    elif hasattr(webdav_service, 'config') and hasattr(webdav_service.config, 'webdav_workspace_path'):
                        workspace_name = webdav_service.config.webdav_workspace_path
                    
                    # Log complet du chemin attendu avec espace de travail
                    logger.info(f"[BUILD_LINK] Chemin attendu avec espace de travail: '{workspace_name}/{real_path}'")
                    
                    share_link = await webdav_service.create_share_link(
                        path=real_path,
                        target_user=target_username,
                        expiration_days=7  # Expiration après 7 jours
                    )
                    
                    # Vérifier si le lien est au format OCS
                    if share_link and ("/s/" in share_link or "/f/" in share_link):
                        logger.info(f"[BUILD_LINK] ✅ Lien OCS créé avec succès: {share_link}")
                        return share_link
                    elif share_link:
                        logger.warning(f"[BUILD_LINK] ⚠️ Lien créé mais format non-OCS: {share_link}")
                        return share_link
                    else:
                        # Si aucun lien n'est retourné, générer un lien WebDAV direct
                        logger.warning("[BUILD_LINK] Aucun lien retourné, génération d'un lien WebDAV direct")
                except Exception as e:
                    logger.error(f"[BUILD_LINK] Erreur création lien OCS: {str(e)}")
                    # Continuer avec le fallback
                
                # Si l'API OCS a échoué ou n'a pas retourné de lien, générer un lien WebDAV direct
                # Extraire prénom.nom pour le fallback WebDAV
                fallback_username = target_username
                
                # Nettoyer l'URL de base
                if "/remote.php/dav/files/" in base_url:
                    base_url = base_url.split("/remote.php/dav/files/")[0]
                elif "/remote.php/webdav/" in base_url:
                    base_url = base_url.split("/remote.php/webdav/")[0]
                elif "/dav/" in base_url:
                    base_url = base_url.split("/dav/")[0]
                elif base_url.endswith("/dav"):
                    base_url = base_url[:-4]
                base_url = base_url.rstrip('/')
                
                # Récupérer l'espace de travail depuis le service WebDAV ou la configuration
                workspace_path = "dossiers-colaig-assistant"  # Valeur par défaut
                
                # Essayer de récupérer l'espace de travail depuis le service WebDAV
                try:
                    # Vérifier si le service WebDAV a un attribut workspace_path
                    if hasattr(webdav_service, 'workspace_path') and webdav_service.workspace_path:
                        workspace_path = webdav_service.workspace_path
                        logger.info(f"[BUILD_LINK] Espace de travail trouvé dans le service WebDAV: {workspace_path}")
                    # Sinon, essayer de le récupérer depuis la configuration
                    elif hasattr(webdav_service, 'config') and hasattr(webdav_service.config, 'webdav_workspace_path'):
                        workspace_path = webdav_service.config.webdav_workspace_path
                        logger.info(f"[BUILD_LINK] Espace de travail trouvé dans la configuration: {workspace_path}")
                    # Sinon, tenter de récupérer depuis le contexte de la room
                    else:
                        # Récupérer le contexte de la room depuis l'EventParser
                        from app.commands import get_room_context
                        room_id = ep.room.room_id if hasattr(ep, 'room') else None
                        if room_id:
                            room_context = await get_room_context(config, room_id)
                            if room_context and hasattr(room_context, 'webdav_workspace'):
                                workspace_path = room_context.webdav_workspace
                                logger.info(f"[BUILD_LINK] Espace de travail trouvé dans le contexte de la room: {workspace_path}")
                except Exception as e:
                    logger.warning(f"[BUILD_LINK] Erreur lors de la récupération de l'espace de travail: {str(e)}")
                    logger.warning(f"[BUILD_LINK] Utilisation de l'espace de travail par défaut: {workspace_path}")
                
                # Construire l'URL WebDAV de fallback avec le chemin complet et l'espace de travail
                fallback_url = f"{base_url}/remote.php/dav/files/{fallback_username}/{workspace_path}/{real_path}?download=1"
                
                logger.info(f"[BUILD_LINK] URL WebDAV fallback construite: {fallback_url}")
                return fallback_url
            else:
                # Sans utilisateur cible, impossible de créer un lien
                logger.error("[BUILD_LINK] Impossible de créer un lien sans utilisateur cible")
                return ""
        else:
            # Pas de service WebDAV disponible
            logger.warning("[BUILD_LINK] Service WebDAV non disponible, impossible de créer un lien")
            return ""
    
    # Fonction pour construire une URL WebDAV avec gestion correcte des caractères spéciaux
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
            
            logger.info(f"[BUILD_WEBDAV] Cas 1: Chemin contient déjà WebDAV pattern: {doc_path}")
            url = f"{base_url}/{doc_path}"
            # Ajouter le paramètre de téléchargement
            return f"{url}?download=1"
        
        # Cas 2: Extraire le vrai chemin du document en supprimant l'utilisateur s'il est au début
        real_doc_path = doc_path
        
        # Vérifier si le chemin commence par le nom d'utilisateur
        if user_local and doc_path.startswith(f"{user_local}/"):
            # Supprimer le préfixe utilisateur
            real_doc_path = doc_path[len(user_local)+1:]
            logger.info(f"[BUILD_WEBDAV] Cas 2: Suppression préfixe utilisateur: {real_doc_path}")
        
        # Vérifier également si le chemin complet commence par le nom d'utilisateur
        elif username and doc_path.startswith(f"{username}/"):
            # Supprimer le préfixe utilisateur complet
            real_doc_path = doc_path[len(username)+1:]
            logger.info(f"[BUILD_WEBDAV] Cas 2: Suppression préfixe email: {real_doc_path}")
        
        # Décoder d'abord si le chemin contient des caractères encodés
        if "%" in real_doc_path:
            decoded_path = urllib.parse.unquote(real_doc_path)
            logger.info(f"[BUILD_WEBDAV] Décodage avant encodage: '{real_doc_path}' -> '{decoded_path}'")
            real_doc_path = decoded_path
            
        # Normaliser le chemin pour traiter uniformément les caractères Unicode
        import unicodedata
        normalized_path = unicodedata.normalize('NFC', real_doc_path)
        if normalized_path != real_doc_path:
            logger.info(f"[BUILD_WEBDAV] Normalisation Unicode: '{real_doc_path}' -> '{normalized_path}'")
            real_doc_path = normalized_path
        
        # Encoder correctement le chemin pour les caractères spéciaux
        # Séparer le chemin en parties et encoder chaque partie
        path_parts = real_doc_path.split('/')
        encoded_parts = []
        for part in path_parts:
            # Encoder la partie tout en gardant les caractères sûrs
            # Ne pas encoder à nouveau les parties déjà encodées
            if "%" in part and all(c in "0123456789ABCDEFabcdef%" for c in part if c not in "0123456789ABCDEFabcdef%"):
                # Cette partie semble déjà encodée
                encoded_parts.append(part)
                logger.info(f"[BUILD_WEBDAV] Partie déjà encodée préservée: '{part}'")
            else:
                encoded_part = urllib.parse.quote(part, safe='')
                encoded_parts.append(encoded_part)
        encoded_path = '/'.join(encoded_parts)
        
        logger.info(f"[BUILD_WEBDAV] Encodage URL: '{real_doc_path}' -> '{encoded_path}'")
        
        # Cas 3: Construire l'URL WebDAV correcte avec l'utilisateur réel
        # Vérifier si base_url contient déjà le pattern WebDAV
        if re.search(webdav_pattern, base_url):
            # Si l'URL de base contient déjà la structure WebDAV, l'utiliser directement
            webdav_url = f"{base_url}/{encoded_path}"
        else:
            # Sinon, construire la structure WebDAV complète avec l'utilisateur
            webdav_url = f"{base_url}/remote.php/dav/files/{username}/{encoded_path}"
        
        logger.info(f"[BUILD_WEBDAV] ⚠️ URL WebDAV fallback construite: {webdav_url}")
        return f"{webdav_url}?download=1"
    
    def extract_username_for_webdav(tchap_user_id: str) -> str:
        """
        Extrait le nom d'utilisateur utilisable pour WebDAV depuis un identifiant Tchap.
        
        Args:
            tchap_user_id: Identifiant Tchap (ex: @nicolas.laval-developpement-durable.gouv.fr1:agent.dev-durable.tchap.gouv.fr)
            
        Returns:
            Nom d'utilisateur pour WebDAV (ex: prénom.nom uniquement)
        """
        if not tchap_user_id:
            return ""
        
        # Extraire la partie utilisateur de l'identifiant Tchap
        # Format: @user:domain -> user
        username = tchap_user_id
        if username.startswith('@') and ':' in username:
            username = username.split(':')[0][1:]  # Enlever @ et prendre avant :
        
        # Extraire seulement prénom.nom
        if '.' in username:
            # Cas 1: Format prénom.nom-domaine.gouv.fr1
            if '-' in username:
                # Trouver la première occurrence de tiret après le premier point
                first_dot_pos = username.find('.')
                first_dash_pos = username.find('-', first_dot_pos)
                
                if first_dash_pos > first_dot_pos:
                    # Extraire seulement prénom.nom
                    username = username[:first_dash_pos]
                    logger.info(f"[USER_EXTRACT] Extraction prénom.nom: {tchap_user_id} -> {username}")
            # Cas 2: Format prénom.nom
            else:
                # Vérifier s'il y a un nombre à la fin (ex: prénom.nom1)
                import re
                match = re.search(r'(.*?\.\D+)(\d+)$', username)
                if match:
                    # Enlever le numéro à la fin
                    username = match.group(1)
                    logger.info(f"[USER_EXTRACT] Extraction prénom.nom sans numéro: {tchap_user_id} -> {username}")
        
        return username
    
    # Fonction utilitaire pour obtenir une icône selon le type de fichier
    def get_file_icon(file_path: str) -> str:
        """
        Retourne une puce simple pour tous les types de fichiers.
        
        Args:
            file_path: Chemin du fichier
            
        Returns:
            Puce simple
        """
        return "-"  # Puce simple au lieu d'emojis
    
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
    
    # Extraire la question
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    command_parts = message_text.split(maxsplit=1)
    
    # Si le message est uniquement "!chercher" sans arguments
    if len(command_parts) <= 1:
        logger.info(f"[CHERCHER] Pas de question fournie, envoi des instructions")
        await matrix_client.send_markdown_message(
            room_id,
            """❓ **Comment utiliser !chercher**

```
!chercher Votre question sur les documents indexés
```

Posez une question en langage naturel, et je chercherai des réponses dans les documents indexés.""",
            msgtype="m.notice"
        )
        return
    
    # Extraire la question (tout ce qui suit après la commande)
    question = command_parts[1]
    logger.info(f"[CHERCHER] Question: '{question}'")
    
    # Envoyer un message de chargement
    await matrix_client.send_markdown_message(
        room_id,
        "🔍 Recherche en cours dans les documents indexés...",
        msgtype="m.notice"
    )
    
    try:
        # Obtenir le service d'index
        index_service = await get_index_service(config)
        logger.info(f"[CHERCHER] Service d'index obtenu")
        
        # Obtenir le contexte du salon pour déterminer l'espace documentaire
        room_context = await get_room_context(config, room_id)
        
        # Variable pour le chemin de l'espace documentaire (None = global)
        space_path = None
        
        # Vérifier si le salon est associé à un espace documentaire
        if room_context and hasattr(room_context, 'webdav_context') and room_context.webdav_context:
            space_path = room_context.webdav_context
            logger.info(f"Recherche dans l'espace documentaire associé: {space_path}")
        
        # Effectuer la recherche avec ou sans filtre d'espace documentaire
        start_time = time.time()
        
        # Si un espace documentaire est défini, utiliser search_in_space
        if space_path:
            search_results = await index_service.search_in_space(
                question, 
                space_path=space_path,
                limit=10  # Valeur par défaut au lieu de config.max_results
            )
        else:
            # Recherche globale ou guidée par le contexte du salon
            search_results = await index_service.search(
                question,
                limit=10,  # Valeur par défaut au lieu de config.max_results
                room_id=room_id
            )
        
        # Logs détaillés pour les résultats de recherche
        logger.info(f"[CHERCHER] Détails des résultats de recherche:")
        for i, result in enumerate(search_results):
            source = result.get("source", "Document inconnu")
            score = result.get("score", 0)
            text_preview = result.get("text", "")[:100] + "..." if result.get("text") else "Pas de texte"
            metadata = result.get("metadata", {})
            logger.info(f"[CHERCHER] Résultat #{i+1}: Source={source}, Score={score}")
            logger.info(f"[CHERCHER] Aperçu du texte: {text_preview}")
            logger.info(f"[CHERCHER] Métadonnées: {metadata}")
        
        # Si aucun résultat n'est trouvé
        if not search_results:
            logger.warning(f"[CHERCHER] Aucun résultat trouvé pour la question: '{question}'")
            await matrix_client.send_markdown_message(
                room_id,
                """⚠️ Je n'ai trouvé aucun document pertinent pour répondre à votre question.

Suggestions:
- Essayez de reformuler votre question
- Vérifiez si les documents sont bien indexés
- Utilisez des mots-clés plus généraux""",
                msgtype="m.notice"
            )
            return
        
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
            
            # Si document_path n'est pas disponible mais document_name l'est, construire le chemin
            if not document_path and document_name:
                # Construire le chemin relatif basé sur le nom du document
                document_path = document_name
                logger.info(f"[CHERCHER] Chemin construit à partir du nom: {document_name} -> {document_path}")
            
            # Récupérer le nom de l'espace de travail pour construire le chemin complet
            workspace_path = "dossiers-colaig-assistant"  # Valeur par défaut
            
            # Tenter de récupérer l'espace de travail depuis le service WebDAV ou la configuration
            if webdav_service:
                try:
                    # Vérifier si le service WebDAV a un attribut workspace_path
                    if hasattr(webdav_service, 'workspace_path') and webdav_service.workspace_path:
                        workspace_path = webdav_service.workspace_path
                        logger.info(f"[CHERCHER] Espace de travail trouvé dans le service WebDAV: {workspace_path}")
                    # Sinon, essayer de le récupérer depuis la configuration
                    elif hasattr(webdav_service, 'config') and hasattr(webdav_service.config, 'webdav_workspace_path'):
                        workspace_path = webdav_service.config.webdav_workspace_path
                        logger.info(f"[CHERCHER] Espace de travail trouvé dans la configuration: {workspace_path}")
                except Exception as e:
                    logger.warning(f"[CHERCHER] Erreur lors de la récupération de l'espace de travail: {str(e)}")
            
            # Construire le chemin complet du document avec l'espace de travail
            full_document_path = document_path
            if not document_path.startswith(f"{workspace_path}/"):
                full_document_path = f"{workspace_path}/{document_path}"
                logger.info(f"[CHERCHER] Chemin complet construit: {document_path} -> {full_document_path}")
                
            page = metadata.get("page", "")
            section_title = metadata.get("section_title", "")
            
            # Si nous avons un nom de document, ajouter à la liste des sources
            if document_name:
                # Créer une entrée source avec les informations disponibles
                source_info = {
                    "name": document_name,
                    "path": full_document_path,  # Utiliser le chemin complet avec l'espace de travail
                    "page": page,
                    "section": section_title,
                    "score": metadata.get("similarity_score", 0)
                }
                document_sources.append(source_info)
                logger.info(f"[CHERCHER] Source ajoutée: nom='{document_name}', chemin='{full_document_path}', page={page}")
            
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
            logger.info(f"[CHERCHER] Structure complète du résultat: {result}")
            
            # Ajouter le contexte seulement si nous avons du contenu
            if content and content.strip():
                contexts.append(content)
                # Ajouter également à la liste des sources pour compatibilité
                sources.append(document_name or "Document inconnu")
            else:
                logger.warning(f"[CHERCHER] Résultat ignoré car contenu vide")
        
        # Si aucun contexte valide n'a été trouvé après le filtrage
        if not contexts:
            logger.warning(f"[CHERCHER] Aucun contexte textuel valide trouvé pour la question: '{question}'")
            await matrix_client.send_markdown_message(
                room_id,
                """⚠️ J'ai trouvé des documents potentiellement pertinents, mais je n'ai pas pu en extraire le contenu textuel.

Suggestions:
- Vérifiez l'indexation des documents avec la commande !index verify
- Essayez de reconstruire l'index avec !index rebuild
- Contactez l'administrateur pour vérifier l'extraction de texte""",
                msgtype="m.notice"
            )
            return
        
        # Marquer le début de la génération
        generation_start = time.time()
        
        # Générer et envoyer la réponse
        try:
            # Générer une réponse avec les références
            logger.info(f"[CHERCHER] Génération de la réponse avec {len(contexts)} contextes")
            
            # Tracer les contextes pour le débogage
            for i, context in enumerate(contexts):
                logger.info(f"[CHERCHER] Contexte {i+1}: {context[:100]}...")
            
            # Déterminer le plan de requête avec l'inférence
            query_plan = await infer_query_plan(
                config,
                query=question,
                contexts=contexts
            )
            logger.info(f"[CHERCHER] Plan de requête déterminé: {query_plan}")
            
            # Générer la réponse formatée
            response = await format_answer(
                config,
                query=question,
                contexts=contexts,
                query_plan=query_plan
            )
            logger.info(f"[CHERCHER] Réponse générée: {response[:100]}...")
            
            # Ajouter les sources à la réponse (TOUJOURS inclure une section de sources)
            logger.info(f"[CHERCHER] Préparation des sources, {len(document_sources)} documents trouvés")
            
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
            
            # Construire la section des sources avec formatage amélioré
            formatted_sources = "\n\n## Sources consultées :"
            
            if document_groups:
                # Base WebDAV URL du serveur - utiliser l'utilisateur réel
                base_webdav_url = config.webdav_url if hasattr(config, 'webdav_url') else ""
                
                # Extraire l'identifiant utilisateur réel depuis Tchap
                sender_user = ep.event.sender if hasattr(ep.event, 'sender') else None
                actual_user_id = None
                
                if sender_user:
                    # Extraire le nom d'utilisateur utilisable depuis l'identifiant Tchap
                    # Format: @nicolas.laval-developpement-durable.gouv.fr1:agent.dev-durable.tchap.gouv.fr
                    # -> nicolas.laval-developpement-durable.gouv.fr1
                    actual_user_id = extract_username_for_webdav(sender_user)
                    logger.info(f"[CHERCHER] Utilisateur extrait: {sender_user} -> {actual_user_id}")
                else:
                    # Fallback vers la configuration si impossible d'extraire l'utilisateur
                    actual_user_id = config.webdav_username if hasattr(config, 'webdav_username') else ""
                    logger.warning(f"[CHERCHER] Fallback vers config webdav_username: {actual_user_id}")
                
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
                
                logger.info(f"[CHERCHER] Base WebDAV URL: {webdav_base_url}")
                logger.info(f"[CHERCHER] Identifiant utilisateur pour liens: {actual_user_id}")
                
                # Trier les documents par score de similarité
                sorted_docs = sorted(document_groups.items(), key=lambda x: x[1]["score"], reverse=True)
                
                # Ajouter chaque document unique avec un lien correct
                for doc_name, doc_info in sorted_docs:
                    doc_path = doc_info["path"]
                    pages = sorted(list(doc_info["pages"]))
                    sections = sorted(list(doc_info["sections"]))
                    
                    # Nettoyer le nom du document pour l'affichage
                    clean_name = clean_document_name(doc_name)
                    
                    # Obtenir l'icône appropriée (maintenant une puce simple)
                    icon = get_file_icon(doc_path)
                    
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
                            # Tronquer les sections longues
                            section_name = sections[0]
                            if len(section_name) > 30:
                                section_name = section_name[:27] + "..."
                            context_info += f"section: {section_name}"
                        else:
                            # Limiter le nombre de sections affichées
                            if len(sections) <= 3:
                                section_list = [s[:20] + "..." if len(s) > 20 else s for s in sections]
                            else:
                                section_list = [s[:20] + "..." if len(s) > 20 else s for s in sections[:2]] + ["..."]
                            
                            context_info += f"sections: {', '.join(section_list)}"
                    
                    # Ajouter les parenthèses seulement s'il y a du contenu
                    if context_info:
                        context_info = f" ({context_info})"
                    
                    # Construire l'URL WebDAV ou lien de partage
                    logger.info(f"[CHERCHER] Tentative de création de lien pour: {doc_path}")
                    
                    # Extraire le nom d'utilisateur du sender pour le partage direct
                    target_username = None
                    
                    if sender_user:
                        # Extraire le nom d'utilisateur de l'ID Matrix (@user:domain -> user)
                        if sender_user.startswith('@') and ':' in sender_user:
                            target_username = sender_user.split(':')[0][1:]  # Enlever @ et prendre avant :
                            logger.info(f"[CHERCHER] Utilisateur cible extrait: {sender_user} -> {target_username}")
                        else:
                            target_username = sender_user
                            logger.info(f"[CHERCHER] Utilisateur cible direct: {target_username}")
                    
                    webdav_url = await build_document_link(
                        webdav_base_url, 
                        actual_user_id,  # Utiliser l'identifiant utilisateur réel au lieu de webdav_username
                        doc_path, 
                        webdav_service=webdav_service,
                        target_user=target_username
                    )
                    
                    # Partie qui gère l'affichage des sources dans la réponse (vers ligne ~700)
                    if webdav_url:
                        # Vérifier que l'URL est valide avant de créer le lien markdown
                        if webdav_url.startswith(('http://', 'https://')):
                            # Échapper les caractères spéciaux pour le markdown
                            # Cette étape est critique pour assurer que le markdown est correctement rendu
                            safe_clean_name = clean_name.replace('[', '\\[').replace(']', '\\]').replace('(', '\\(').replace(')', '\\)')
                            
                            # S'assurer que l'URL est correctement encodée pour le markdown
                            if '%' in webdav_url:
                                # Si l'URL contient déjà des caractères encodés, ne pas les réencoder
                                safe_webdav_url = webdav_url
                            else:
                                # Encoder uniquement les caractères spéciaux dans l'URL
                                safe_webdav_url = webdav_url
                                # Utiliser une approche segment par segment si nécessaire pour les URLs complexes
                                if ' ' in safe_webdav_url or any(c in safe_webdav_url for c in '[]()'):
                                    url_parts = urllib.parse.urlparse(safe_webdav_url)
                                    # Encoder le chemin mais pas le schéma ni le domaine
                                    encoded_path = '/'.join(urllib.parse.quote(segment, safe='') 
                                                          for segment in url_parts.path.split('/'))
                                    # Reconstruire l'URL avec le chemin encodé
                                    safe_webdav_url = urllib.parse.urlunparse((
                                        url_parts.scheme,
                                        url_parts.netloc,
                                        encoded_path,
                                        url_parts.params,
                                        url_parts.query,
                                        url_parts.fragment
                                    ))
                                    
                            # Ajouter avec lien markdown valide
                            source_line = f"\n{icon} [{safe_clean_name}]({safe_webdav_url}){context_info}"
                            logger.info(f"[CHERCHER] Lien markdown créé: [{safe_clean_name}]({safe_webdav_url})")
                        else:
                            # URL invalide, afficher sans lien
                            source_line = f"\n{icon} {clean_name}{context_info}"
                            logger.warning(f"[CHERCHER] URL invalide, affichage sans lien: {webdav_url}")
                    else:
                        # Ajouter sans lien mais avec puce simple
                        source_line = f"\n{icon} {clean_name}{context_info}"
                        logger.warning(f"[CHERCHER] Impossible de créer un lien pour: {doc_path}")
                    
                    formatted_sources += source_line
                    logger.info(f"[CHERCHER] Source ajoutée: {source_line}")
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
                            clean_name = clean_document_name(doc_name)
                            icon = get_file_icon(doc_name)
                            formatted_sources += f"\n{icon} {clean_name}"
                    else:
                        formatted_sources += "\n- Document (aucun lien disponible)"
                    
                    logger.info("[CHERCHER] Ajout des sources basé sur les résultats bruts")
                else:
                    formatted_sources += "\n*Aucune source spécifique identifiée.*"
                    logger.warning("[CHERCHER] Aucune source unique n'a été identifiée")
            
            # Ajouter une note sur l'accessibilité des documents
            formatted_sources += "\n\n💡 *Cliquez sur les noms des documents pour les télécharger directement. Si vous n'avez pas accès à certains documents, veuillez contacter votre administrateur.*"
            
            # Ajouter les sources à la réponse
            response += formatted_sources
            
            # Ajouter une section note de bas de page
            response += "\n\n---\n*Cette réponse a été générée automatiquement à partir des documents indexés. Pour toute question, précision ou information complémentaire, n'hésitez pas à demander.*"
            
            # Log de la réponse complète pour débogage
            logger.info(f"[CHERCHER] Réponse finale générée: \n{response}")
            
            # Envoyer la réponse finale
            logger.info(f"[CHERCHER] Envoi de la réponse finale au chat")
            response_event = await matrix_client.send_markdown_message(
                room_id,
                response,
                reply_to=ep.event.event_id
            )
            
            logger.info(f"[CHERCHER] Réponse envoyée avec event_id: {response_event}")
            return None
            
        except Exception as e:
            error_message = f"Une erreur est survenue lors de la génération de la réponse: {str(e)}"
            logger.error(f"[CHERCHER] {error_message}")
            logger.exception(e)
            
            # Envoyer un message d'erreur
            await matrix_client.send_markdown_message(
                room_id,
                f"⚠️ {error_message}",
                reply_to=ep.event.event_id
            )
            return None
        
    except Exception as e:
        logger.error(f"[CHERCHER] Erreur lors du traitement de la commande: {str(e)}")
        # Envoyer le message d'erreur directement
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Une erreur est survenue lors de la recherche: {str(e)}",
            msgtype="m.notice"
        )
        return None 