# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

import time
import asyncio
import traceback

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.auth import AuthLogin, Credentials
from matrix_bot.callbacks import Callbacks
from matrix_bot.config import bot_lib_config

# Référence au registre de commandes unique
from app.commands.registry import command_registry

# Configuration Albert
from app.config import env_config
from app.services.context.instance import context_manager

# TODO/IMPROVE:
# - if albert-bot is invited in a salon, make it answer only when if it is tagged.
# - !models: show available models.
# - show sources of a mesage for some given reactions of an answer.
# - !info: show the chat setting (model, with_history).

class MatrixBot:
    def __init__(self, homeserver: str, username: str, password: str):
        # Préparer les configurations avant de créer le client Matrix
        # Ajouter le registre de commandes en tant qu'attribut direct
        self.command_registry = command_registry
        
        self.matrix_client = MatrixClient(
            AuthLogin(Credentials(homeserver=homeserver, username=username, password=password))
        )
        
        self.callbacks = Callbacks(self.matrix_client)
        self._maintenance_task = None
        self._context_cleanup_task = None
        
    async def _maintenance_loop(self):
        """Tâche de maintenance périodique"""
        while True:
            try:
                # Sauvegarde des contextes en attente
                await context_manager.flush_pending_saves()
                
                # Nettoyage des vieux contextes si activé
                if env_config.context_auto_cleanup:
                    await context_manager.cleanup_old_contexts(
                        max_age_days=env_config.context_max_age_days
                    )
                
                # Attente avant la prochaine exécution
                await asyncio.sleep(env_config.context_save_interval)
                
            except Exception as e:
                logger.error(f"Erreur maintenance contextes: {str(e)}")
                await asyncio.sleep(60)  # Attente plus courte en cas d'erreur

    async def main(self):
        # Initialisation du gestionnaire de contexte
        await context_manager.initialize()
        
        await self.matrix_client.automatic_login()
        sync = await self.matrix_client.sync(timeout=bot_lib_config.timeout, full_state=True)
        logger.info("Synchronisation initiale réussie", 
            next_batch=sync.next_batch,
            rooms_joined=len(sync.rooms.join) if sync.rooms else 0
        )
        await self.callbacks.setup_callbacks()
        
        for action in self.callbacks.startup:
            for room_id in self.matrix_client.rooms:
                await action(room_id)
                
        # Démarrage des tâches de maintenance
        self._maintenance_task = asyncio.create_task(self._maintenance_loop())
        
        try:
            await self.matrix_client.sync_forever(timeout=3000, full_state=True)
        finally:
            # Nettoyage à la fermeture
            if self._maintenance_task:
                self._maintenance_task.cancel()
                try:
                    await self._maintenance_task
                except asyncio.CancelledError:
                    pass
                    
            # Fermeture propre du gestionnaire de contexte
            await context_manager.close()

async def main():
    # Initialiser le bot avec la configuration d'environnement
    tchap_bot = MatrixBot(
        env_config.matrix_home_server,
        env_config.matrix_bot_username,
        env_config.matrix_bot_password,
    )
    
    # Attacher la configuration d'Albert directement à l'objet MatrixClient
    # pour qu'elle soit accessible dans les commandes
    tchap_bot.matrix_client.albert_config = env_config
    
    # Afficher les infos de configuration
    logger.info(f"Albert config assigned: {hasattr(tchap_bot.matrix_client, 'albert_config')}")
    if hasattr(tchap_bot.matrix_client, 'albert_config'):
        logger.info(f"Albert model in config: {tchap_bot.matrix_client.albert_config.albert_model}")
    
    # ===================================================================
    # CHARGEMENT DES COMMANDES MODULARISÉES
    # ===================================================================
    logger.info("INITIALISATION DU SYSTÈME DE COMMANDES")
    
    # 1. Vider complètement les registres existants pour éviter les doublons
    tchap_bot.command_registry.function_register.clear()
    tchap_bot.command_registry.activated_functions.clear()
    
    # 2. Importation sélective des modules avec enregistrement automatique des commandes
    #    Les décorateurs @register_feature de chaque commande s'exécuteront
    #    lors de l'importation, enregistrant les commandes dans le registre.
    logger.info("Chargement des commandes de base")
    from app.commands.basic_commands import help
    
    logger.info("Chargement des commandes documentaires")
    from app.commands.document_commands.docquery import doc_query_command
    from app.commands.document_commands.index import faiss_index_command
    from app.commands.document_commands.attachment import handle_attachments_command
    
    # 3. Afficher les commandes enregistrées pour diagnostic
    logger.info("=== COMMANDES ENREGISTRÉES ===")
    for name, feature in tchap_bot.command_registry.function_register.items():
        logger.info(f"Command registered: {name}, Group: {feature['group']}")
    logger.info("=== FIN COMMANDES ENREGISTRÉES ===")
    
    # 4. Message au démarrage pour informer sur le mode de fonctionnement
    async def startup_message(room_id):
        await tchap_bot.matrix_client.send_markdown_message(
            room_id,
            """🤖 **Albert Tchap - Mode Optimisé**
            
Je suis en ligne avec les fonctionnalités suivantes activées:
- Commande `!aide` (affiche l'aide)
- Commande `!docquery` (interroge les documents)
- Commande `!index` (gère l'index FAISS)
- Commande `!pj` (traite les pièces jointes)

Pour obtenir plus d'informations, utilisez la commande `!aide`.""",
            msgtype="m.notice"
        )
    
    # Enregistrer l'action de démarrage
    tchap_bot.callbacks.register_on_startup(startup_message)
    # ===================================================================
    
    # Charger les groupes configurés dans l'environnement
    groups_to_activate = []
    if env_config.groups_used:
        groups_to_activate = [g.strip() for g in env_config.groups_used.split(",")]
    
    logger.info(f"Groups to activate: {groups_to_activate}")
    
    # Activer les groupes de commandes sans duplication
    activated_features = set()  # Ensemble pour suivre les fonctions déjà activées
    
    for group_name in groups_to_activate:
        for feature in tchap_bot.command_registry.activate_and_retrieve_group(group_name):
            feature_name = feature["name"]
            
            # Vérification que la fonction n'a pas déjà été activée
            if feature_name not in activated_features:
                activated_features.add(feature_name)
                callback = feature["func"]
                onEvent = feature["onEvent"]
                tchap_bot.callbacks.register_on_custom_event(callback, onEvent, feature)
                logger.info(f"Loaded feature: {feature_name} from group {feature['group']}")
    
    # Afficher les métriques d'activation
    logger.info(f"Total registered commands: {len(tchap_bot.command_registry.function_register)}")
    logger.info(f"Total activated commands: {len(tchap_bot.command_registry.activated_functions)}")
    logger.info(f"Activated command list: {sorted(list(tchap_bot.command_registry.activated_functions))}")

    # Démarrer le bot avec gestion d'erreur et retry
    n_tries = 4
    err = None
    for i in range(n_tries):
        try:
            await tchap_bot.main()
        except Exception as e:
            err = e
            logger.error(f"Bot startup failed with error: {e}")
            time.sleep(3)

    if err:
        raise err

if __name__ == "__main__":
    asyncio.run(main())
