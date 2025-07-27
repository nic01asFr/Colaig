# SPDX-FileCopyrightText: 2021 - 2022 Isaac Beverly <https://github.com/imbev>
# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
#
# SPDX-License-Identifier: MIT

import asyncio

from nio import SyncResponse

from .auth import AuthLogin, Credentials
from .callbacks import Callbacks
from .client import MatrixClient
from .config import bot_lib_config, logger


class MatrixBot:
    """
    A class for the bot library user to interact with.
    """

    def __init__(self, homeserver: str, username: str, password: str):
        self.matrix_client = MatrixClient(
            AuthLogin(Credentials(homeserver=homeserver, username=username, password=password))
        )
        self.callbacks = Callbacks(self.matrix_client)

    async def main(self):
        # Initialisation du gestionnaire de contexte
        logger.info("MatrixBot.main() appelée")
        
        try:
            logger.info("Tentative d'initialisation du gestionnaire de contexte...")
            await ensure_initialized()
            logger.info("Gestionnaire de contexte initialisé avec succès")
            
            logger.info("Tentative de connexion au serveur Matrix...")
            # Ajouter un timeout spécifique pour l'authentification
            try:
                logger.info("Début du processus d'authentification Matrix (avec timeout 30s)...")
                await asyncio.wait_for(
                    self.matrix_client.automatic_login(),
                    timeout=30
                )
                logger.info("Authentification Matrix réussie")
            except asyncio.TimeoutError:
                logger.error("TIMEOUT: L'authentification Matrix a pris trop de temps (>30s)")
                logger.warning("Continuation malgré l'échec d'authentification pour éviter le blocage complet")
                # Ne pas lever d'exception pour permettre au bot de continuer sans être authentifié
            
            try:
                logger.info("Lancement de la synchronisation initiale...")
                # Ajouter un timeout pour la synchro initiale
                sync = await asyncio.wait_for(
                    self.matrix_client.sync(timeout=bot_lib_config.timeout, full_state=True),
                    timeout=30  # 30 secondes max
                )
                logger.info("Synchronisation initiale réussie", 
                    next_batch=sync.next_batch,
                    rooms_joined=len(sync.rooms.join) if sync.rooms else 0
                )
            except asyncio.TimeoutError:
                logger.error("TIMEOUT: La synchronisation initiale a pris trop de temps (>30s)")
                logger.warning("Continuation malgré l'échec de synchronisation")
            except Exception as e:
                logger.error(f"Erreur lors de la synchronisation initiale: {str(e)}")
                logger.warning("Continuation malgré l'erreur de synchronisation")
            
            logger.info("Configuration des callbacks...")
            await self.callbacks.setup_callbacks()
            logger.info("Callbacks configurés")
            
            # Déclencher les actions de démarrage avec gestion d'erreur
            logger.info("Exécution des actions de démarrage...")
            for action in self.callbacks.startup:
                for room_id in self.matrix_client.rooms:
                    try:
                        await action(room_id)
                    except Exception as e:
                        logger.error(f"Erreur lors de l'action de démarrage pour {room_id}: {str(e)}")
                        # Continuer avec les autres salons malgré l'erreur
            logger.info("Actions de démarrage terminées")    
                    
            # Démarrage des tâches de maintenance
            logger.info("Démarrage de la tâche de maintenance...")
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())
            logger.info("Tâche de maintenance démarrée")
            
            try:
                logger.info("Démarrage de sync_forever...")
                # Envelopper sync_forever dans un timeout plus long
                await asyncio.wait_for(
                    self.matrix_client.sync_forever(timeout=3000, full_state=True),
                    timeout=120  # 2 minutes max pour lancer le sync_forever
                )
                logger.info("sync_forever terminé")
            except asyncio.TimeoutError:
                logger.error("TIMEOUT: sync_forever a pris trop de temps à démarrer (>120s)")
                # Ne pas déclencher d'erreur fatale pour permettre la continuation
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
                        
                # Fermeture propre du gestionnaire de contexte
                logger.info("Fermeture du gestionnaire de contexte...")
                from app.services.context.instance import close_context_manager
                await close_context_manager()
                logger.info("Gestionnaire de contexte fermé")
        except Exception as e:
            logger.error(f"Erreur dans MatrixBot.main(): {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            raise

    def print_sync_response(self, sync):
        if not isinstance(sync, SyncResponse):
            logger.warning("La réponse de synchronisation n'est pas du type attendu")
            return
            
        # Afficher les informations de connexion
        logger.info(
            "Connected",
            server=self.matrix_client.homeserver,
            user_id=self.matrix_client.user_id,
            device_id=self.matrix_client.device_id,
        )
        
        # Afficher les informations de chiffrement
        if bot_lib_config.encryption_enabled:
            if not self.matrix_client.olm:
                logger.warning("Le chiffrement est activé mais self.matrix_client.olm est None")
                return
                
            try:
                key = self.matrix_client.olm.account.identity_keys["ed25519"]
                logger.info(
                    f'This bot\'s public fingerprint ("Session key") for one-sided verification is: '
                    f"{' '.join([key[i:i + 4] for i in range(0, len(key), 4)])}"
                )
            except Exception as e:
                logger.error(f"Erreur lors de l'affichage de la clé de session: {str(e)}")

    def run(self):
        """Runs the bot."""
        asyncio.run(self.main())
