# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

"""Point d'entrée principal de l'application"""
import asyncio
import logging
import sys

# Configuration du logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)-8s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger("albert-tchap")

def setup_logging():
    """Configure le système de logging"""
    # Déjà configuré plus haut
    pass

async def init_app():
    """Initialise l'application"""
    try:
        # Initialisation du gestionnaire de contexte
        from app.services.context.instance import context_manager, ensure_initialized
        await ensure_initialized()
        logger.info("Context manager initialized successfully")
        
        # Initialisation de Playwright pour les extractions web
        logger.info("Initialisation de Playwright pour les extractions web...")
        from app.services.browser_extraction import ensure_playwright_installed
        await ensure_playwright_installed()
        logger.info("Initialisation de Playwright terminée")
        
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
        from app.bot import main
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
