# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

"""Point d'entrée principal de l'application"""
import asyncio
import logging
import sys

from matrix_bot.config import logger

def setup_logging():
    """Configure le système de logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

async def init_app():
    """Initialisation de l'application"""
    setup_logging()
    logger.info("Starting Albert Tchap...")
    
    try:
        # Import après la configuration du logging
        from services.context.instance import context_manager
        
        # Initialisation du gestionnaire de contexte
        await context_manager.initialize()
        logger.info("Context manager initialized successfully")
        
        # Préchargement de l'index documentaire
        from app.config import env_config
        from app.services.index_service import get_index_service
        
        logger.info("Préchargement de l'index documentaire...")
        try:
            # Initialisation asynchrone avec un timeout généreux
            await asyncio.wait_for(
                get_index_service(env_config),
                timeout=120.0  # 2 minutes
            )
            logger.info("Index documentaire préchargé avec succès")
        except asyncio.TimeoutError:
            logger.warning("Timeout lors du préchargement de l'index documentaire - il sera chargé lors de la première utilisation")
        except Exception as e:
            logger.warning(f"Erreur lors du préchargement de l'index documentaire: {str(e)} - il sera chargé lors de la première utilisation")
        
        # Import et démarrage du bot après l'initialisation du contexte
        from bot import main
        await main()
        
    except Exception as e:
        logger.error(f"Failed to initialize application: {str(e)}")
        raise

def main():
    """Point d'entrée principal"""
    try:
        asyncio.run(init_app())
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Application crashed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()
