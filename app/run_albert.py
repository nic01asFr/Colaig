"""
Script de démarrage d'Albert Tchap.

Ce script démarre le bot Albert Tchap en utilisant l'approche modularisée
pour éviter les problèmes de duplication des commandes.
"""

import asyncio
import logging
import sys

from app.matrix_bot.config import setup_logging, logger

async def main():
    """Point d'entrée principal"""
    logger.info("Starting Albert Tchap...")
    
    # Initialisation de Playwright pour les extractions web
    logger.info("Initialisation de Playwright pour les extractions web...")
    from app.services.browser_extraction import ensure_playwright_installed
    await ensure_playwright_installed()
    logger.info("Initialisation de Playwright terminée")
    
    # Exécution du bot
    from app.bot import main as bot_main
    await bot_main()

if __name__ == "__main__":
    # Configuration du logging
    setup_logging()
    logger.info("starting the bot")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Bot stopped with error: {str(e)}")
        sys.exit(1) 