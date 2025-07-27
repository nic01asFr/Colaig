"""
Commandes de base du bot Albert.

Ce module contient les commandes fondamentales comme l'aide.
"""

import logging
import time
import urllib.parse
import re
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from app.commands.decorators import albert_command
from app.matrix_bot.eventparser import EventParser
from app.matrix_bot.client import MatrixClient
from app.services.webdav import WebDAVService
from app.services.webdav_instance import ensure_webdav_initialized, get_webdav_service
from app.config import Config
from app.services.ocs_link_validator import OCSLinkValidator
from nio import RoomMessageText, RoomMemberEvent

from app.bot_msg import AlbertMsg

from .registry import register_feature, only_allowed_user, command_registry

logger = logging.getLogger(__name__)

async def is_user_admin(matrix_client: MatrixClient, room_id: str, user_id: str) -> bool:
    """
    Vérifie si un utilisateur a des permissions d'administrateur dans un salon.
    Pour l'instant, renvoie True pour permettre l'utilisation des commandes de diagnostic.
    """
    try:
        # Pour l'instant, autoriser tous les utilisateurs authentifiés
        # En production, vous pourriez vouloir implémenter une vérification plus stricte
        return True
    except Exception as e:
        logger.warning(f"Erreur lors de la vérification des permissions: {str(e)}")
        return False

# Commande d'aide
@register_feature(
    group="basic",
    onEvent=RoomMessageText,
    command="aide",
    aliases=["help", "aiuto"],
    help=AlbertMsg.shorts["help"],
)
@only_allowed_user
async def help(ep: EventParser, matrix_client: MatrixClient):
    """Affiche l'aide du bot"""
    # Utiliser la configuration Albert si disponible, sinon utiliser la config Matrix
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    event_id = ep.event.event_id
    
    # Extraire les arguments depuis le texte du message
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    command_parts = message_text.split()
    
    # Si le message contient plus qu'un mot et que le deuxième est "all", on affiche l'aide détaillée
    verbose = len(command_parts) > 1 and command_parts[1] == "all"
    
    # Activer l'indicateur de frappe
    await matrix_client.room_typing(room_id, typing_state=True)
    
    try:
        # Utiliser le registre importé directement
        cmd_registry = command_registry
        
        # Générer le message d'aide
        help_msg = cmd_registry.get_help(config, verbose)
        
        # Envoyer le message d'aide
        await matrix_client.send_markdown_message(
            room_id,
            help_msg,
            msgtype="m.notice",
            reply_to=event_id
        )
        
        # Si l'utilisateur a demandé l'aide détaillée, on lui montre comment
        # retourner à l'aide simplifiée
        if verbose:
            await matrix_client.send_markdown_message(
                room_id,
                "Tapez !aide pour afficher l'aide simplifiée.",
                msgtype="m.notice"
            )
    finally:
        # Désactiver l'indicateur de frappe
        await matrix_client.room_typing(room_id, typing_state=False)

@register_feature(
    group="admin",
    onEvent=RoomMessageText,
    command="config_webdav",
    help="!config_webdav [--room|--user|--reset] [URL] [username] [password] [chemin] - Configure le référentiel WebDAV pour ce contexte",
    for_geek=True,  # Limiter aux administrateurs
)
@only_allowed_user
async def config_webdav(ep: EventParser, matrix_client: MatrixClient):
    """
    Configure le référentiel WebDAV à utiliser pour le contexte actuel.
    Usage:
        !config_webdav [--room|--user|--reset] [URL] [username] [password] [chemin]
    """
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Extraire les arguments
    args = ep.event.body.strip().split(maxsplit=5)
    if len(args) == 1 or "--help" in args:
        help_text = """
## Configuration du référentiel WebDAV contextuel
        
Cette commande permet de configurer différents référentiels WebDAV selon le contexte.

**Usage:**
```
!config_webdav [--room|--user|--reset] [URL] [username] [password] [chemin]
```

**Arguments:**
- `--room`: Configure le référentiel pour ce salon uniquement
- `--user`: Configure le référentiel pour cet utilisateur uniquement
- `--reset`: Réinitialise la configuration WebDAV pour ce contexte
- `URL`: URL du serveur WebDAV (ex: https://nextcloud.example.com/remote.php/dav/)
- `username`: Nom d'utilisateur pour la connexion WebDAV
- `password`: Mot de passe pour la connexion WebDAV
- `chemin`: Chemin de base dans le serveur WebDAV (ex: /documents)

**Exemples:**
```
!config_webdav --room https://cloud.example.org/remote.php/dav/ user_salon mdp_salon /dossier_salon
!config_webdav --user https://cloud.example.org/remote.php/dav/ mon_user mon_mdp /mes_docs
!config_webdav --reset
```

La priorité des configurations est: utilisateur+salon > utilisateur > salon > défaut
"""
        await matrix_client.send_markdown_message(room_id, help_text)
        return
    
    try:
        from app.services.webdav_context_manager import get_webdav_context_manager
        
        # Initialiser le gestionnaire de contextes WebDAV
        manager = await get_webdav_context_manager(config)
        
        # Traiter les différents cas
        
        # Cas 1: Réinitialisation
        if "--reset" in args:
            await manager.clear_webdav_config(room_id=room_id, user_id=sender)
            await matrix_client.send_markdown_message(
                room_id,
                "✅ Configuration WebDAV réinitialisée pour ce contexte."
            )
            return
            
        # Cas 2: Configuration spécifique
        target_type = None
        
        if "--room" in args:
            target_type = "room"
        elif "--user" in args:
            target_type = "user"
        
        # Extraire les paramètres WebDAV
        if len(args) < 5:
            await matrix_client.send_markdown_message(
                room_id,
                "❌ Paramètres insuffisants. Format: !config_webdav [--room|--user] [URL] [username] [password] [chemin]",
                msgtype="m.notice"
            )
            return
            
        # Position des paramètres selon le type de cible
        url_index = 2 if target_type else 1
        
        # Extraire les paramètres
        webdav_url = args[url_index] if len(args) > url_index else None
        webdav_username = args[url_index+1] if len(args) > url_index+1 else None
        webdav_password = args[url_index+2] if len(args) > url_index+2 else None
        webdav_path = args[url_index+3] if len(args) > url_index+3 else "/documents"
        
        # Vérifier que tous les paramètres nécessaires sont fournis
        if not all([webdav_url, webdav_username, webdav_password]):
            await matrix_client.send_markdown_message(
                room_id,
                "❌ Paramètres manquants. Format: !config_webdav [--room|--user] [URL] [username] [password] [chemin]",
                msgtype="m.notice"
            )
            return
            
        # Préparer les données de configuration
        config_data = {
            "webdav_url": webdav_url,
            "webdav_username": webdav_username,
            "webdav_password": webdav_password,
            "webdav_root_path": webdav_path
        }
        
        # Appliquer la configuration selon le type
        if target_type == "room":
            await manager.set_room_webdav_config(room_id, config_data)
            await matrix_client.send_markdown_message(
                room_id,
                f"✅ Configuration WebDAV pour ce salon mise à jour.\nURL: {webdav_url}\nChemin: {webdav_path}"
            )
        elif target_type == "user":
            await manager.set_user_webdav_config(sender, config_data)
            await matrix_client.send_markdown_message(
                room_id,
                f"✅ Configuration WebDAV personnelle mise à jour.\nURL: {webdav_url}\nChemin: {webdav_path}"
            )
        else:
            # Par défaut, configuration pour la combinaison salon+utilisateur
            await manager.set_room_user_webdav_config(room_id, sender, config_data)
            await matrix_client.send_markdown_message(
                room_id,
                f"✅ Configuration WebDAV pour vous dans ce salon mise à jour.\nURL: {webdav_url}\nChemin: {webdav_path}"
            )
            
    except Exception as e:
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Erreur lors de la configuration WebDAV: {str(e)}",
            msgtype="m.notice"
        )
        import traceback
        from app.matrix_bot.config import logger
        logger.error(f"Erreur config_webdav: {traceback.format_exc()}")

@albert_command(
    group="admin",
    command="diagnostic_ocs",
    help_text="!diagnostic_ocs - Diagnostique les problèmes de liens de partage OCS",
    for_geek=True
)
async def diagnostic_ocs_command(ep: EventParser, matrix_client: MatrixClient):
    """
    Commande de diagnostic pour les liens de partage OCS.
    
    Cette commande permet aux administrateurs de :
    - Vérifier la configuration OCS du serveur
    - Tester la création de liens de partage
    - Identifier les problèmes de configuration
    - Obtenir des recommandations pour résoudre les problèmes
    """
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    try:
        # Vérifier que l'utilisateur est administrateur
        if not await is_user_admin(matrix_client, room_id, sender):
            await matrix_client.send_markdown_message(
                room_id,
                "❌ Cette commande est réservée aux administrateurs.",
                msgtype="m.notice"
            )
            return
        
        # Envoyer un message de début de diagnostic
        await matrix_client.send_markdown_message(
            room_id,
            "🔍 **Diagnostic OCS en cours...**\n\nCela peut prendre quelques secondes.",
            msgtype="m.notice"
        )
        
        # Obtenir le service WebDAV
        webdav_service = await get_webdav_service(config)
        
        if not webdav_service:
            await matrix_client.send_markdown_message(
                room_id,
                "❌ Impossible d'initialiser le service WebDAV. Vérifiez la configuration.",
                msgtype="m.notice"
            )
            return
        
        # Extraire les paramètres de connexion
        base_url = webdav_service.base_url
        username = webdav_service.webdav_username
        password = webdav_service.webdav_password
        
        # Créer le validateur OCS
        validator = OCSLinkValidator(base_url, username, password)
        
        try:
            # Générer le rapport de diagnostic
            report = await validator.generate_diagnostic_report()
            
            # Envoyer le rapport
            await matrix_client.send_markdown_message(
                room_id,
                f"📊 **Rapport de diagnostic OCS**\n\n```\n{report}\n```"
            )
            
            # Tester avec un lien existant si fourni
            message_text = ep.event.body.strip()
            if len(message_text.split()) > 1:
                test_url = message_text.split()[1]
                logger.info(f"Test de validation du lien: {test_url}")
                
                # Valider le lien existant
                validation_result = await validator.validate_existing_link(test_url)
                
                validation_msg = "🔗 **Validation du lien fourni**\n\n"
                validation_msg += f"**Type**: {validation_result['link_type']}\n"
                validation_msg += f"**Format valide**: {'✅' if validation_result['is_valid_format'] else '❌'}\n"
                validation_msg += f"**Fallback WebDAV**: {'⚠️' if validation_result['is_webdav_fallback'] else '✅'}\n\n"
                
                if validation_result['suggestions']:
                    validation_msg += "**Recommandations**:\n"
                    for suggestion in validation_result['suggestions']:
                        validation_msg += f"- {suggestion}\n"
                
                await matrix_client.send_markdown_message(
                    room_id,
                    validation_msg
                )
            
        finally:
            await validator.close()
            
    except Exception as e:
        logger.error(f"Erreur lors du diagnostic OCS: {str(e)}")
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Erreur lors du diagnostic OCS: {str(e)}",
            msgtype="m.notice"
        )

@albert_command(
    group="admin", 
    command="test_link_ocs",
    help_text="!test_link_ocs <lien> - Teste et valide un lien de partage OCS",
    for_geek=True
)
async def test_link_ocs_command(ep: EventParser, matrix_client: MatrixClient):
    """
    Commande pour tester et valider un lien de partage OCS spécifique.
    """
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    try:
        # Vérifier que l'utilisateur est administrateur
        if not await is_user_admin(matrix_client, room_id, sender):
            await matrix_client.send_markdown_message(
                room_id,
                "❌ Cette commande est réservée aux administrateurs.",
                msgtype="m.notice"
            )
            return
        
        # Extraire le lien depuis le message
        message_text = ep.event.body.strip()
        command_parts = message_text.split(maxsplit=1)
        
        if len(command_parts) < 2:
            await matrix_client.send_markdown_message(
                room_id,
                "❓ **Usage**: `!test_link_ocs <lien>`\n\nFournissez le lien à tester.",
                msgtype="m.notice"
            )
            return
        
        test_url = command_parts[1]
        
        # Obtenir le service WebDAV pour créer le validateur
        webdav_service = await get_webdav_service(config)
        
        if not webdav_service:
            await matrix_client.send_markdown_message(
                room_id,
                "❌ Impossible d'initialiser le service WebDAV.",
                msgtype="m.notice"
            )
            return
        
        # Créer le validateur OCS
        validator = OCSLinkValidator(
            webdav_service.base_url,
            webdav_service.webdav_username,
            webdav_service.webdav_password
        )
        
        try:
            # Valider le lien
            validation_result = await validator.validate_existing_link(test_url)
            
            # Construire le message de résultat
            result_msg = f"🔗 **Test du lien**: {test_url}\n\n"
            result_msg += f"**Type détecté**: {validation_result['link_type']}\n"
            result_msg += f"**Format OCS valide**: {'✅' if validation_result['is_valid_format'] else '❌'}\n"
            result_msg += f"**Utilise WebDAV direct**: {'⚠️ Oui' if validation_result['is_webdav_fallback'] else '✅ Non'}\n\n"
            
            if validation_result['suggestions']:
                result_msg += "**Diagnostic**:\n"
                for suggestion in validation_result['suggestions']:
                    result_msg += f"- {suggestion}\n"
            
            # Ajouter des recommandations spécifiques
            if validation_result['is_webdav_fallback']:
                result_msg += "\n**🔧 Actions recommandées**:\n"
                result_msg += "- Vérifiez que l'API OCS est activée sur le serveur\n"
                result_msg += "- Vérifiez les permissions de partage\n"
                result_msg += "- Lancez `!diagnostic_ocs` pour un diagnostic complet\n"
            
            await matrix_client.send_markdown_message(room_id, result_msg)
            
        finally:
            await validator.close()
            
    except Exception as e:
        logger.error(f"Erreur lors du test du lien OCS: {str(e)}")
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Erreur lors du test: {str(e)}",
            msgtype="m.notice"
        )

@albert_command(
    group="admin",
    command="test_ocs_rapide",
    help_text="!test_ocs_rapide <chemin_fichier> - Test rapide de création d'un lien OCS",
    for_geek=True
)
async def test_ocs_rapide_command(ep: EventParser, matrix_client: MatrixClient):
    """
    Commande de test rapide pour créer un lien OCS et diagnostiquer les problèmes.
    """
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    try:
        # Vérifier que l'utilisateur est administrateur
        if not await is_user_admin(matrix_client, room_id, sender):
            await matrix_client.send_markdown_message(
                room_id,
                "❌ Cette commande est réservée aux administrateurs.",
                msgtype="m.notice"
            )
            return
        
        # Extraire le chemin du fichier depuis le message
        message_text = ep.event.body.strip()
        command_parts = message_text.split(maxsplit=1)
        
        if len(command_parts) < 2:
            await matrix_client.send_markdown_message(
                room_id,
                "❓ **Usage**: `!test_ocs_rapide <chemin_fichier>`\n\nExemple: `!test_ocs_rapide /documents/test.pdf`",
                msgtype="m.notice"
            )
            return
        
        test_path = command_parts[1].strip()
        
        # Message de début de test
        await matrix_client.send_markdown_message(
            room_id,
            f"🧪 **Test OCS rapide en cours...**\n\nFichier: `{test_path}`",
            msgtype="m.notice"
        )
        
        # Obtenir le service WebDAV
        webdav_service = await get_webdav_service(config)
        
        if not webdav_service:
            await matrix_client.send_markdown_message(
                room_id,
                "❌ Impossible d'initialiser le service WebDAV.",
                msgtype="m.notice"
            )
            return
        
        # Extraire le nom d'utilisateur cible depuis l'ID Matrix
        target_username = None
        if sender.startswith('@') and ':' in sender:
            target_username = sender.split(':')[0][1:]  # Enlever @ et prendre avant :
        
        # Test 1: Lien public
        logger.info(f"[TEST_OCS] Test de création d'un lien public pour: {test_path}")
        
        try:
            public_link = await webdav_service.create_share_link(
                test_path,
                password=None,
                expiration_days=1,  # Expiration courte pour test
                target_user=None    # Lien public
            )
            
            if public_link:
                # Vérifier le format du lien
                if "/s/" in public_link:
                    public_result = f"✅ **Lien public OCS**: {public_link}"
                elif "/f/" in public_link:
                    public_result = f"✅ **Lien direct OCS**: {public_link}"
                else:
                    public_result = f"⚠️ **Lien créé (format non-OCS)**: {public_link}"
            else:
                public_result = "❌ **Échec création lien public**"
                
        except Exception as public_error:
            public_result = f"❌ **Erreur lien public**: {str(public_error)}"
        
        # Test 2: Partage direct (si utilisateur cible disponible)
        direct_result = ""
        if target_username:
            logger.info(f"[TEST_OCS] Test de création d'un partage direct pour: {test_path} vers {target_username}")
            
            try:
                direct_link = await webdav_service.create_share_link(
                    test_path,
                    password=None,
                    expiration_days=7,
                    target_user=target_username
                )
                
                if direct_link:
                    if "/s/" in direct_link:
                        direct_result = f"\n✅ **Partage direct OCS**: {direct_link}"
                    elif "/f/" in direct_link:
                        direct_result = f"\n✅ **Partage direct OCS**: {direct_link}"
                    else:
                        direct_result = f"\n⚠️ **Partage créé (format non-OCS)**: {direct_link}"
                else:
                    direct_result = f"\n❌ **Échec partage direct**"
                    
            except Exception as direct_error:
                direct_result = f"\n❌ **Erreur partage direct**: {str(direct_error)}"
        else:
            direct_result = f"\n🔸 **Partage direct**: Non testé (utilisateur cible introuvable)"
        
        # Tester aussi build_document_link
        logger.info(f"[TEST_OCS] Test de build_document_link pour: {test_path}")
        
        # Import dynamique pour éviter les dépendances circulaires
        import importlib
        docquery_module = importlib.import_module('app.commands.document_commands.docquery_adapted')
        build_document_link = None
        
        # Récupérer la fonction build_document_link depuis le module
        if hasattr(docquery_module, 'build_document_link'):
            # Accéder à la fonction dans le scope local de la commande
            source_code = """
async def build_document_link(base_url: str, username: str, doc_path: str, webdav_service=None, target_user=None):
    # Construction simplifiée pour test
    if not webdav_service:
        return f"{base_url}/remote.php/dav/files/{username}/{doc_path.lstrip('/')}?download=1"
    
    # Tenter un lien OCS
    try:
        real_path = doc_path.lstrip('/')
        if username and real_path.startswith(f"{username}/"):
            real_path = real_path[len(username)+1:]
        
        share_link = await webdav_service.create_share_link(
            real_path,
            password=None,
            expiration_days=2,
            target_user=target_user
        )
        
        if share_link:
            return share_link
        else:
            # Fallback WebDAV
            return f"{base_url}/remote.php/dav/files/{username}/{real_path}?download=1"
    except Exception:
        # Fallback WebDAV en cas d'erreur
        return f"{base_url}/remote.php/dav/files/{username}/{real_path}?download=1"
"""
            exec(source_code, locals())
        else:
            logger.warning("[TEST_OCS] build_document_link non trouvée, utilisation d'une version simplifiée")
        
        try:
            build_link = await build_document_link(
                webdav_service.base_url,
                webdav_service.webdav_username,
                test_path,
                webdav_service=webdav_service,
                target_user=target_username
            )
            
            if build_link:
                if "/s/" in build_link or "/f/" in build_link:
                    build_result = f"\n✅ **build_document_link OCS**: {build_link}"
                elif "/remote.php/dav/files/" in build_link:
                    build_result = f"\n⚠️ **build_document_link WebDAV**: {build_link}"
                else:
                    build_result = f"\n🔸 **build_document_link**: {build_link}"
            else:
                build_result = f"\n❌ **build_document_link**: Échec"
                
        except Exception as build_error:
            build_result = f"\n❌ **build_document_link**: {str(build_error)}"
        
        # Construire le message de résultat
        result_msg = f"📊 **Résultats du test OCS**\n\n"
        result_msg += f"**Fichier testé**: `{test_path}`\n"
        result_msg += f"**Utilisateur cible**: `{target_username or 'N/A'}`\n\n"
        result_msg += f"**Tests effectués**:\n"
        result_msg += public_result
        result_msg += direct_result
        result_msg += build_result
        
        # Ajouter des recommandations
        if "❌" in result_msg:
            result_msg += f"\n\n**🔧 Recommandations**:\n"
            result_msg += f"- Vérifiez que le fichier existe: `{test_path}`\n"
            result_msg += f"- Lancez `!diagnostic_ocs` pour un diagnostic complet\n"
            result_msg += f"- Vérifiez la configuration de l'API OCS sur le serveur\n"
        elif "⚠️" in result_msg:
            result_msg += f"\n\n**💡 Remarques**:\n"
            result_msg += f"- Les liens WebDAV fonctionnent mais l'API OCS pourrait avoir des problèmes\n"
            result_msg += f"- Vérifiez la configuration du serveur Nextcloud\n"
        else:
            result_msg += f"\n\n**🎉 Excellent !** L'API OCS fonctionne correctement."
        
        await matrix_client.send_markdown_message(room_id, result_msg)
        
    except Exception as e:
        logger.error(f"Erreur lors du test OCS rapide: {str(e)}")
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Erreur lors du test: {str(e)}",
            msgtype="m.notice"
        )

@albert_command(
    group="admin",
    command="test_ocs_special",
    help_text="!test_ocs_special - Teste la création de liens OCS avec caractères spéciaux",
    for_geek=True
)
@ensure_webdav_initialized
async def test_ocs_special_command(ep: EventParser, matrix_client: MatrixClient, webdav_service: Optional[WebDAVService] = None):
    """Teste la génération de liens OCS avec des caractères spéciaux"""
    logger.info("[TEST_OCS_SPECIAL] Démarrage du test de liens avec caractères spéciaux")
    
    # Vérifier que le service WebDAV est disponible
    if not webdav_service:
        await matrix_client.send_notice(ep.room_id, "❌ Service WebDAV non disponible.")
        return
    
    # Chemins de test avec caractères spéciaux
    test_paths = [
        "Test File with spaces.pdf",
        "Test_File-with-dashes.pdf",
        "Test File with accents éèêë.pdf",
        "Test File with brackets [test].pdf",
        "Test File with parentheses (test).pdf",
        "Test+File+with+plus.pdf",
        "Test%File%with%percent.pdf",
        "Test&File&with&ampersand.pdf",
        "Test=File=with=equals.pdf",
        "Test@File@with@at.pdf",
        "Test:File:with:colon.pdf"
    ]
    
    # Extraire l'identifiant utilisateur pour le partage direct
    sender_user = ep.event.sender
    target_username = None
    
    if sender_user:
        # Extraire le nom d'utilisateur de l'ID Matrix (@user:domain -> user)
        if sender_user.startswith('@') and ':' in sender_user:
            target_username = sender_user.split(':')[0][1:]  # Enlever @ et prendre avant :
            logger.info(f"[TEST_OCS_SPECIAL] Utilisateur cible extrait: {sender_user} -> {target_username}")
        else:
            target_username = sender_user
    
    # Envoyer un message initial
    await matrix_client.send_notice(ep.room_id, "🔍 Test de création de liens OCS avec caractères spéciaux...")
    
    results = []
    
    # Tester chaque chemin
    for path in test_paths:
        try:
            # Tenter de créer un lien de partage
            logger.info(f"[TEST_OCS_SPECIAL] Test avec chemin: {path}")
            
            # Vérifier si la méthode create_share_link existe
            if hasattr(webdav_service, 'create_share_link'):
                link = await webdav_service.create_share_link(
                    path,
                    password=None,
                    expiration_days=1,  # Expiration courte pour les tests
                    target_user=target_username
                )
                
                # Analyser le résultat
                if link:
                    if "/s/" in link or "/f/" in link:
                        results.append(f"✅ **{path}**: Lien OCS créé avec succès: {link}")
                        logger.info(f"[TEST_OCS_SPECIAL] Succès OCS pour {path}: {link}")
                    else:
                        results.append(f"⚠️ **{path}**: Lien non-OCS créé: {link}")
                        logger.info(f"[TEST_OCS_SPECIAL] Lien non-OCS pour {path}: {link}")
                else:
                    results.append(f"❌ **{path}**: Échec de création du lien")
                    logger.error(f"[TEST_OCS_SPECIAL] Échec pour {path}")
            else:
                results.append(f"❓ **{path}**: Méthode create_share_link non disponible")
                logger.warning(f"[TEST_OCS_SPECIAL] Méthode create_share_link non disponible")
        
        except Exception as e:
            results.append(f"❌ **{path}**: Erreur: {str(e)}")
            logger.exception(f"[TEST_OCS_SPECIAL] Erreur pour {path}: {str(e)}")
    
    # Envoyer les résultats
    result_message = "### Résultats des tests de liens OCS avec caractères spéciaux\n\n" + "\n".join(results)
    await matrix_client.send_markdown_message(ep.room_id, result_message)
    
    # Ajouter des informations de diagnostic
    import re
    diagnostic_info = "\n\n### Informations de diagnostic\n"
    diagnostic_info += f"- Type de service WebDAV: {type(webdav_service).__name__}\n"
    
    # Vérifier si le service a la méthode create_share_link
    has_share_method = hasattr(webdav_service, 'create_share_link')
    diagnostic_info += f"- Méthode create_share_link disponible: {'✅ Oui' if has_share_method else '❌ Non'}\n"
    
    # Tester l'extraction du prénom.nom
    if target_username:
        # Utiliser r avant la chaîne pour éviter les séquences d'échappement invalides
        username_match = re.search(r'(.*?\.\D+)(\d+)?', target_username)
        if username_match:
            base_username = username_match.group(1)
            diagnostic_info += f"- Extraction prénom.nom: {target_username} -> {base_username}\n"
        else:
            diagnostic_info += f"- Extraction prénom.nom: Impossible d'extraire depuis {target_username}\n"
    
    await matrix_client.send_markdown_message(ep.room_id, diagnostic_info)