# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

"""
Point d'entrée principal de l'application ColaIG.

Ce module initialise les services nécessaires et démarre le bot Matrix.
Ce point d'entrée utilise la fonction principale de bot.py pour éviter les doublons d'instance.
"""

import asyncio
import logging
import sys
import traceback  # Import au niveau global

from app.matrix_bot.config import logger
from app.services.initialization import initialize_services, shutdown_services, setup_signal_handlers
from app.config import env_config

async def main():
    """
    Fonction principale asynchrone qui initialise les services et démarre le bot.
    """
    try:
        # Configurer le logging
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        logger.info("Démarrage de ColaIG...")
        
        # Initialiser tous les services enregistrés
        await initialize_services(env_config)
        
        # Importer et utiliser la fonction main de bot.py pour éviter les doublons d'instance
        from app.bot import main as bot_main
        await bot_main()
        
    except KeyboardInterrupt:
        logger.info("Interruption clavier détectée, fermeture en cours...")
        
    except Exception as e:
        logger.error(f"Erreur fatale: {str(e)}")
        logger.error(traceback.format_exc())
        
    finally:
        # Fermer proprement tous les services
        await shutdown_services()
        logger.info("Application terminée")

if __name__ == "__main__":
    # Configurer les gestionnaires de signaux
    setup_signal_handlers()
    
    # Exécuter la fonction principale asynchrone
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interruption du programme")
        sys.exit(0)
    except Exception as e:
        logger.critical(f"Erreur non gérée: {e}")
        sys.exit(1)
