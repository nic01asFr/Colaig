"""
Script de démarrage d'Albert Tchap.

Ce script démarre le bot Albert Tchap en utilisant l'approche modularisée
pour éviter les problèmes de duplication des commandes.
"""

import asyncio
import logging
import sys

from matrix_bot.config import setup_logging, logger

from app.bot import main

if __name__ == "__main__":
    # Configuration du logging
    setup_logging()
    logger.info("starting the bot")
    logger.info("Starting Albert Tchap...")
    
    # Exécution du bot
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Bot stopped with error: {str(e)}")
        sys.exit(1) 