"""Instance globale du gestionnaire de contexte"""
from typing import Any, Optional
import asyncio
from matrix_bot.config import logger
from config import Config
from .manager import ContextManager

# Instance globale du gestionnaire de contexte
context_manager: Optional[ContextManager] = ContextManager(Config())
_init_lock = asyncio.Lock()

# Flag pour suivre l'état d'initialisation
_initialized = False

async def ensure_initialized():
    """S'assure que le gestionnaire de contexte est initialisé"""
    global context_manager, _initialized, _init_lock
    
    async with _init_lock:
        if not _initialized:
            try:
                await context_manager.initialize()
                _initialized = True
                logger.info("Gestionnaire de contexte global initialisé")
            except Exception as e:
                logger.error(f"Erreur initialisation gestionnaire de contexte: {str(e)}")
                # Continuer malgré l'erreur, l'initialisation sera retentée
    
    return context_manager

__all__ = ['context_manager', 'ensure_initialized'] 