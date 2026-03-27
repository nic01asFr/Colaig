# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

import time
import asyncio
import traceback
import os
import sys
from typing import List, Dict, Any, Optional, Callable, Awaitable

from app.matrix_bot.client import MatrixClient
from app.matrix_bot.config import logger
from app.matrix_bot.auth import AuthLogin, Credentials
from app.matrix_bot.callbacks import Callbacks
from app.matrix_bot.bot import MatrixBot as BaseMatrixBot
from app.matrix_bot.config import bot_lib_config
from app.commands.registry import command_registry
from app.config import Config, env_config
from app.commands.exclusions import EXCLUDED_MODULES, INCLUDED_COMMANDS

# Configuration Albert
from app.services.context.instance import get_context_manager, ensure_initialized

# TODO/IMPROVE:
# - if albert-bot is invited in a salon, make it answer only when if it is tagged.
# - !models: show available models.
# - show sources of a mesage for some given reactions of an answer.
# - !info: show the chat setting (model, with_history).

class TchapBot:
    def __init__(self, homeserver: str, username: str, password: str, config: Config = None):
        # Préparer les configurations avant de créer le client Matrix
        # Ajouter le registre de commandes en tant qu'attribut direct
        self.command_registry = command_registry
        self.config = config or env_config

        self.matrix_client = MatrixClient(
            AuthLogin(Credentials(homeserver=homeserver, username=username, password=password))
        )
        # Inject config into matrix_client for downstream access
        self.matrix_client.albert_config = self.config
        
        self.callbacks = Callbacks(self.matrix_client)
        self._maintenance_task = None
        self._full_functionality_enabled = False
        self._welcome_sent = set()  # Ensemble de salons où le message de bienvenue a été envoyé
        self._commands_loaded = False  # Flag pour éviter de charger les commandes plusieurs fois
        
    async def load_commands(self):
        """Charge les commandes et groupes de fonctionnalités"""
        # Éviter le double chargement
        if self._commands_loaded:
            logger.info("Commandes déjà chargées, ignorer.")
            return
            
        logger.info("INITIALISATION DU SYSTÈME DE COMMANDES")
        
        # 1. Vider complètement les registres existants pour éviter les doublons
        self.command_registry.function_register.clear()
        self.command_registry.activated_functions.clear()
        
        # Importer le module d'exclusions
        try:
            from app.commands.exclusions import (
                EXCLUDED_MODULES, 
                EXCLUDED_COMMANDS, 
                INCLUDED_COMMANDS,
                USE_STRICT_INCLUSION
            )
            using_exclusions = True
            logger.info(f"Exclusions chargées: {len(EXCLUDED_COMMANDS)} commandes, {len(EXCLUDED_MODULES)} modules exclus")
            logger.info(f"Mode inclusion stricte: {USE_STRICT_INCLUSION}")
        except ImportError:
            logger.info("Module d'exclusions non trouvé, toutes les commandes seront importées")
            using_exclusions = False
            EXCLUDED_MODULES = []
            EXCLUDED_COMMANDS = []
            INCLUDED_COMMANDS = []
            USE_STRICT_INCLUSION = False
        
        # 2. Importation directe des modules essentiels
        logger.info("Importation directe des commandes essentielles...")
        
        # Commandes de base
        logger.info("Chargement des commandes de base")
        from app.commands.basic_commands import help
        
        # Conversation générale (nécessaire pour le fonctionnement de base)
        logger.info("Chargement du module conversation")
        import app.commands.conversation
        
        # Commandes documentaires
        logger.info("Chargement des commandes documentaires")
        # Utiliser les versions adaptées des commandes documentaires
        from app.commands.document_commands.docquery_adapted import doc_query_adapted_command
        from app.commands.document_commands.index import faiss_index_command
        from app.commands.document_commands.attachment_adapted import handle_attachments_adapted_command
        from app.commands.document_commands.synthesis import handle_synthesis_command
        
        # Commandes Web (nouvelles)
        logger.info("Chargement des commandes de recherche web")
        from app.commands.web_commands.web_search import web_search_command, add_link_command, list_links_command, explore_link_command
        
        # Commandes Webhook (nouvelles)
        logger.info("Chargement des commandes webhook")
        from app.commands.web_commands.n8n_webhook import webhook_command, webhook_response
        logger.info(f"Commandes webhook chargées : webhook_command et webhook_response")

        # Si nous ne sommes pas en mode inclusion stricte, charger d'autres modules
        if not USE_STRICT_INCLUSION:
            logger.info("Importation des modules supplémentaires...")
            # Importation normale des autres modules
            from app.commands import register_all_commands
            # Ne pas importer les modules exclus
            modules = []
            for module_name in register_all_commands():
                if module_name not in EXCLUDED_MODULES:
                    modules.append(module_name)
                    try:
                        __import__(module_name)
                        logger.info(f"Module {module_name} importé")
                    except Exception as e:
                        logger.error(f"Erreur lors de l'importation du module {module_name}: {e}")
        
        # En mode inclusion stricte, activer uniquement les commandes listées
        if USE_STRICT_INCLUSION:
            # Sauvegarder toutes les commandes enregistrées
            all_commands = set(self.command_registry.function_register.keys())
            
            # Réinitialiser les commandes activées
            self.command_registry.activated_functions = set()
            
            # Activer uniquement les commandes incluses
            for cmd_name in INCLUDED_COMMANDS:
                if cmd_name in all_commands:
                    self.command_registry.activated_functions.add(cmd_name)
                    logger.info(f"Activation spécifique de la commande: {cmd_name}")
                else:
                    logger.warning(f"Commande {cmd_name} non trouvée dans le registre")
            
            logger.info(f"Mode inclusion stricte: {len(self.command_registry.activated_functions)}/{len(all_commands)} commandes activées")
        
        # 3. Afficher les commandes enregistrées pour diagnostic
        logger.info("=== COMMANDES ENREGISTRÉES ===")
        for name, feature in self.command_registry.function_register.items():
            logger.info(f"Command registered: {name}, Group: {feature['group']}")
        logger.info("=== FIN COMMANDES ENREGISTRÉES ===")
        
        # 4. Message au démarrage pour informer sur le mode de fonctionnement
        async def startup_message(room_id):
            await self.matrix_client.room_typing(room_id, typing_state=True)
            try:
                await self.matrix_client.send_markdown_message(
                    room_id,
                    """✨ **Colaig Albert** - Niveau 1
                    
Je suis en ligne avec les fonctionnalités suivantes :

- Conversation générale : je réponds à tous les messages de manière suivie
- Commande `!chercher` : interrogation des documents
- Commande `!synthese` : génération de synthèses sur un sujet
- Commande `!classer` : classement des pièces jointes
- Commande `!index` : gestion de l'espace documentaire

**Nouvelles fonctionnalités web** :
- Commande `!recherche_web` : recherche d'informations sur Internet
- Commande `!ajouter_lien` : ajout de liens dans la base de données
- Commande `!liste_liens` : affichage des liens enregistrés
- Commande `!explorer_lien` : exploration et résumé de pages web

Pour plus d'informations sur ces commandes, utilisez `!aide`.
""",
                    msgtype="m.notice"
                )
            finally:
                await self.matrix_client.room_typing(room_id, typing_state=False)
        
        # Enregistrer l'action de démarrage
        self.callbacks.register_on_startup(startup_message)
        
        # Charger les groupes configurés dans l'environnement
        groups_to_activate = []
        if hasattr(self.config, 'groups_used') and self.config.groups_used:
            groups_to_activate = [g.strip() for g in self.config.groups_used.split(",")]
        
        # S'assurer que le groupe 'conversation' est toujours activé pour permettre
        # au bot de répondre aux messages qui ne sont pas des commandes
        if 'conversation' not in groups_to_activate:
            groups_to_activate.append('conversation')
        
        # S'assurer que le groupe 'web' est activé pour les fonctionnalités web
        if 'web' not in groups_to_activate:
            groups_to_activate.append('web')
        
        logger.info(f"Groups to activate: {groups_to_activate}")
        
        # Activer les groupes de commandes sans duplication
        activated_features = set()  # Ensemble pour suivre les fonctions déjà activées
        
        for group_name in groups_to_activate:
            for feature in self.command_registry.activate_and_retrieve_group(group_name):
                feature_name = feature["name"]
                
                # Vérification que la fonction n'a pas déjà été activée
                if feature_name not in activated_features:
                    activated_features.add(feature_name)
                    callback = feature["func"]
                    onEvent = feature["onEvent"]
                    self.callbacks.register_on_custom_event(callback, onEvent, feature)
                    logger.info(f"Loaded feature: {feature_name} from group {feature['group']}")
        
        # Afficher les métriques d'activation
        logger.info(f"Total registered commands: {len(self.command_registry.function_register)}")
        logger.info(f"Total activated commands: {len(self.command_registry.activated_functions)}")
        logger.info(f"Activated command list: {sorted(list(self.command_registry.activated_functions))}")
        
        # Enregistrer les handlers de réactions emoji
        logger.info("Enregistrement des handlers de réactions emoji")
        from app.commands.reactions import handle_reaction as _handle_reaction
        _mc = self.matrix_client  # référence locale pour la closure

        async def _reaction_dispatch(room, event, emoji):
            await _handle_reaction(room, event, emoji, _mc)

        self.callbacks.register_on_reaction_event(_reaction_dispatch)
        logger.info("Handlers de réactions emoji enregistrés (👍 👎 🔄 ➕)")

        # Marquer les commandes comme chargées pour éviter les doublons
        self._commands_loaded = True
        
    async def _maintenance_loop(self):
        """Tâche de maintenance périodique"""
        while True:
            try:
                # Obtenir le gestionnaire de contexte
                context_manager = await get_context_manager()
                
                # Sauvegarde des contextes en attente
                await context_manager.flush_pending_saves()
                
                # Nettoyage des vieux contextes si activé
                if self.config.context_auto_cleanup:
                    await context_manager.cleanup_old_contexts(
                        max_age_days=self.config.context_max_age_days
                    )

                # Attente avant la prochaine exécution
                await asyncio.sleep(self.config.context_save_interval)
                
            except Exception as e:
                logger.error(f"Erreur maintenance contextes: {str(e)}")
                await asyncio.sleep(60)  # Attente plus courte en cas d'erreur
                
    async def setup_basic_callbacks(self):
        """Configure uniquement les callbacks essentiels ne dépendant pas de WebDAV"""
        # Importer les types d'événements nécessaires
        from nio import InviteMemberEvent, RoomMessageText, MegolmEvent

        # Ajouter le callback pour les invitations qui ne dépend pas de WebDAV
        if bot_lib_config.join_on_invite:
            self.matrix_client.add_event_callback(self.callbacks.invite_callback, InviteMemberEvent)

        # Toujours enregistrer le callback pour les messages non déchiffrés
        self.matrix_client.add_event_callback(self.callbacks.decryption_failure, MegolmEvent)
            
        # Ajouter un gestionnaire de base pour les messages
        async def basic_message_handler(room, event):
            if event.sender == self.matrix_client.user_id:
                return  # Ignorer nos propres messages
                
            try:
                # Réponse minimale en attendant l'initialisation complète
                if not self._full_functionality_enabled:
                    # Ne répondre qu'aux commandes d'aide explicites
                    if event.body.strip().lower() in ["!aide", "!help"]:
                        await self.matrix_client.send_markdown_message(
                            room.room_id,
                            "⌛ Initialisation en cours... Je serai bientôt pleinement opérationnel.\n"
                            "Certaines fonctionnalités peuvent être temporairement indisponibles.",
                            msgtype="m.notice"
                        )
            except Exception as e:
                logger.error(f"Erreur dans le gestionnaire de message basique: {str(e)}")
        
        # Enregistrer ce gestionnaire basique
        self.matrix_client.add_event_callback(basic_message_handler, RoomMessageText)
        logger.info("Callbacks de base configurés")
        
    async def enable_full_functionality(self):
        """Active toutes les fonctionnalités une fois les services initialisés"""
        if self._full_functionality_enabled:
            logger.info("Fonctionnalités complètes déjà activées")
            return
            
        try:
            # Charger les commandes si ce n'est pas déjà fait
            await self.load_commands()
            
            # Configurer tous les callbacks avancés
            await self.callbacks.setup_callbacks()
            logger.info("Callbacks avancés configurés avec succès")
            
            # Définir le flag indiquant que toutes les fonctionnalités sont actives
            self._full_functionality_enabled = True
            
            # Informer les utilisateurs dans tous les salons que le bot est complètement fonctionnel
            for room_id in self.matrix_client.rooms:
                if room_id not in self._welcome_sent:
                    for action in self.callbacks.startup:
                        await action(room_id)
                    self._welcome_sent.add(room_id)
                    
            # Démarrer la tâche de maintenance
            if self._maintenance_task is None or self._maintenance_task.done():
                self._maintenance_task = asyncio.create_task(self._maintenance_loop())
                logger.info("Tâche de maintenance démarrée")
                
            logger.info("Fonctionnalités complètes activées")
        except Exception as e:
            logger.error(f"Erreur lors de l'activation des fonctionnalités complètes: {str(e)}")
            logger.error(traceback.format_exc())

    async def main(self):
        """Point d'entrée principal du bot"""
        # Créer un événement pour signaler que l'initialisation de base est terminée
        initialization_done = asyncio.Event()
        
        try:
            # Connexion Matrix (étape rapide)
            logger.info("Tentative de connexion au serveur Matrix...")
            await self.matrix_client.automatic_login()
            logger.info("Connexion Matrix réussie")
            
            # Configuration des callbacks basiques (rapide, sans dépendances)
            logger.info("Configuration des callbacks basiques...")
            await self.setup_basic_callbacks()
            
            # Signal que l'initialisation de base est terminée
            initialization_done.set()
            
            # Lancer l'initialisation des services en arrière-plan
            logger.info("Initialisation des services avancés en arrière-plan...")
            services_task = asyncio.create_task(self._initialize_services())
            
            # Démarrer le bot en mode limité
            logger.info("Démarrage du bot en mode limité...")
            
            # Attendre les services puis activer les fonctionnalités (sans timeout bloquant)
            async def _enable_when_ready():
                try:
                    await services_task
                    await self.enable_full_functionality()
                except Exception as e:
                    logger.error(f"Erreur initialisation services: {str(e)}")
            asyncio.create_task(_enable_when_ready())
            
            # Sync initial pour peupler next_batch avant sync_forever
            _SYNC_FILTER = {
                "room": {
                    "state": {"lazy_load_members": True},
                    "timeline": {"limit": 50},
                },
                "presence": {"not_types": ["*"]},
            }
            try:
                from nio import SyncError as NioSyncError, KeysQueryResponse
                logger.info("Sync initial pour peupler next_batch...")
                sync_resp = await self.matrix_client.sync(timeout=30000, full_state=True, sync_filter=_SYNC_FILTER)
                if isinstance(sync_resp, NioSyncError):
                    logger.warning(f"Sync initial échoué (ignoré): {sync_resp}")
                else:
                    logger.info(f"Sync initial réussi, next_batch={sync_resp.next_batch}")
                    # Interroger les clés des membres + établir sessions Olm
                    if self.matrix_client.matrix_config.encryption_enabled:
                        import logging as _logging
                        from nio import KeysClaimResponse
                        _log = _logging.getLogger("colaig.bot")
                        _log.info("Requête des clés des membres (keys_query)...")
                        try:
                            kq_resp = await self.matrix_client.keys_query()
                            if isinstance(kq_resp, KeysQueryResponse):
                                _log.info(f"keys_query réussi: {len(kq_resp.device_keys)} users")
                            else:
                                _log.warning(f"keys_query réponse inattendue: {kq_resp}")
                        except Exception as kq_err:
                            _log.warning(f"keys_query exception: {kq_err}")

                        # Claim one-time keys pour établir des sessions Olm avec tous les membres
                        try:
                            users_devices = {}
                            device_store = self.matrix_client.device_store
                            _log.info(f"device_store users: {list(device_store.users)}")
                            for user_id in device_store.users:
                                if user_id == self.matrix_client.user_id:
                                    continue
                                devices = list(device_store[user_id].keys())
                                if devices:
                                    users_devices[user_id] = devices
                            _log.info(f"keys_claim: {len(users_devices)} users à claim")
                            if users_devices:
                                claim_resp = await self.matrix_client.keys_claim(users_devices)
                                if isinstance(claim_resp, KeysClaimResponse):
                                    _log.info(f"keys_claim réussi: {len(claim_resp.one_time_keys)} users")
                                else:
                                    _log.warning(f"keys_claim: {claim_resp}")
                        except Exception as claim_err:
                            _log.warning(f"keys_claim exception: {claim_err}")
            except Exception as e:
                logger.warning(f"Sync initial exception (ignorée): {e}")

            try:
                logger.info("Démarrage de sync_forever...")
                while True:
                    try:
                        await self.matrix_client.sync_forever(timeout=3000, full_state=False, sync_filter=_SYNC_FILTER)
                        logger.info("sync_forever terminé normalement")
                        break
                    except asyncio.TimeoutError:
                        logger.warning("sync_forever: timeout aiohttp, relance immédiate...")
                    except Exception as e:
                        logger.error(f"sync_forever: erreur inattendue: {e}, relance dans 5s...")
                        await asyncio.sleep(5)
            finally:
                # Nettoyage à la fermeture
                logger.info("Nettoyage des ressources...")
                if self._maintenance_task:
                    logger.info("Annulation de la tâche de maintenance...")
                    self._maintenance_task.cancel()
                    try:
                        await self._maintenance_task
                    except asyncio.CancelledError:
                        pass
                
                # Fermeture propre du service d'index global
                try:
                    from app.services.index_service import close_index_service
                    logger.info("Fermeture du service d'index global...")
                    await close_index_service()
                    logger.info("Service d'index global fermé proprement")
                except Exception as e:
                    logger.error(f"Erreur lors de la fermeture du service d'index global: {str(e)}")
                        
                # Fermeture propre du behavior manager
                try:
                    from app.services.behavior_manager import close_behavior_manager
                    await close_behavior_manager()
                    logger.info("Behavior manager fermé")
                except Exception as e:
                    logger.error(f"Erreur lors de la fermeture du behavior manager: {str(e)}")

                # Fermeture propre du registre MCP
                try:
                    from app.services.mcp.registry import close_mcp_registry
                    await close_mcp_registry()
                    logger.info("Registre MCP fermé")
                except Exception as e:
                    logger.error(f"Erreur lors de la fermeture du registre MCP: {str(e)}")

                # Fermeture propre du gestionnaire de contexte
                logger.info("Fermeture du gestionnaire de contexte...")
                from app.services.context.instance import close_context_manager
                await close_context_manager()
                logger.info("Gestionnaire de contexte fermé")
        except Exception as e:
            logger.error(f"Erreur dans TchapBot.main(): {str(e)}")
            logger.error(traceback.format_exc())
            raise
        
        return initialization_done
        
    async def _initialize_services(self):
        """Initialise les services en arrière-plan"""
        try:
            # Initialiser le gestionnaire de contexte (qui dépend de WebDAV)
            logger.info("Tentative d'initialisation du gestionnaire de contexte...")
            await ensure_initialized()
            logger.info("Gestionnaire de contexte initialisé avec succès")

            # Initialiser les autres services
            from app.services.initialization import initialize_services, _initialized
            if not _initialized:
                logger.info("Initialisation des services...")
                await initialize_services(self.config)
                logger.info("Services initialisés avec succès")
            
            # Retourner True pour indiquer le succès
            return True
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation des services: {str(e)}")
            logger.error(traceback.format_exc())
            return False

async def main(config: Config = None):
    """
    Fonction principale qui initialise et démarre le bot.
    Cette fonction est le point d'entrée centralisé pour éviter les instances multiples.

    Args:
        config: Optional Config instance. If None, uses the global env_config singleton.
                Pass a custom Config for multi-instance support.
    """
    config = config or env_config
    logger.info("=== DÉMARRAGE APPLICATION ALBERT ===")

    # Créer le bot Matrix qui ne dépendra pas immédiatement de WebDAV
    logger.info("=== CRÉATION CLIENT MATRIX ===")
    tchap_bot = TchapBot(
        config.matrix_home_server,
        config.matrix_bot_username,
        config.matrix_bot_password,
        config=config,
    )
    
    # Démarrer le bot avec un timeout global
    try:
        # Le bot démarre rapidement en mode limité et pourra ensuite
        # activer ses fonctionnalités complètes quand les services seront prêts
        initialization_event = await tchap_bot.main()
        
        # Attendre jusqu'à ce que l'application soit interrompue
        # Cette attente est nécessaire pour maintenir l'application en vie
        while True:
            await asyncio.sleep(3600)  # 1 heure
            
    except Exception as e:
        logger.error(f"Erreur dans la fonction main(): {str(e)}")
        logger.error(traceback.format_exc())
        
        # En cas d'erreur grave, attendre un moment avant de sortir
        # pour éviter les redémarrages en boucle
        await asyncio.sleep(5)
        raise  # Propager l'erreur pour un redémarrage propre

if __name__ == "__main__":
    asyncio.run(main())
