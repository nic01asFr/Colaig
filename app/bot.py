# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

import time
import asyncio

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.auth import AuthLogin, Credentials
from matrix_bot.callbacks import Callbacks
from matrix_bot.config import bot_lib_config

from commands import command_registry
from config import env_config
from services.context.instance import context_manager

# TODO/IMPROVE:
# - if albert-bot is invited in a salon, make it answer only when if it is tagged.
# - !models: show available models.
# - show sources of a mesage for some given reactions of an answer.
# - !info: show the chat setting (model, with_history).

class MatrixBot:
    def __init__(self, homeserver: str, username: str, password: str):
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
    tchap_bot = MatrixBot(
        env_config.matrix_home_server,
        env_config.matrix_bot_username,
        env_config.matrix_bot_password,
    )

    for feature in [
        feature
        for feature_group in env_config.groups_used
        for feature in command_registry.activate_and_retrieve_group(feature_group)
    ]:
        callback = feature["func"]
        onEvent = feature["onEvent"]
        tchap_bot.callbacks.register_on_custom_event(callback, onEvent, feature)
        logger.info("loaded feature", feature=feature["name"])

    # To send message if Albert is updated for example...
    # async def startup_action(room_id):
    #    await tchap_bot.matrix_client.send_markdown_message(room_id, command_registry.get_help())
    # tchap_bot.callbacks.register_on_startup(startup_action)

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
