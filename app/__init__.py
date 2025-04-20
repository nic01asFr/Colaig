"""
Module principal de l'application Albert Tchap.
"""

import os
import sys
import logging

# Configurer un logger de base
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)-8s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
)
logger = logging.getLogger("albert-tchap")

# Importer et appliquer le patch pour browser-use
try:
    from app.patches import browser_use_headless_patch
    logger.info("Patch browser-use importé avec succès")
except ImportError as e:
    logger.warning(f"Impossible d'importer le patch browser-use: {str(e)}")
except Exception as e:
    logger.error(f"Erreur lors de l'import du patch browser-use: {str(e)}")

# Informations sur l'environnement
logger.info(f"DISPLAY: {os.environ.get('DISPLAY', 'non défini')}")
logger.info(f"PLAYWRIGHT_HEADLESS: {os.environ.get('PLAYWRIGHT_HEADLESS', 'non défini')}")
logger.info(f"PLAYWRIGHT_BROWSERS_PATH: {os.environ.get('PLAYWRIGHT_BROWSERS_PATH', 'non défini')}")
logger.info(f"Python version: {sys.version}")
logger.info(f"PID: {os.getpid()}")
logger.info(f"Répertoire courant: {os.getcwd()}")

# Démarrage de l'application
logger.info("Initialisation de l'application Albert Tchap")

from ._version import __version__

__all__ = ['__version__']
