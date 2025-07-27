"""
Instance globale du gestionnaire de contexte.

Ce module fournit des fonctions pour accéder à l'instance globale du gestionnaire
de contexte et initialiser correctement les services.
"""

import asyncio
import os
from typing import Optional, Dict, Any

from app.matrix_bot.config import logger
from app.config import Config
from app.services.context.manager import ContextManager

# Instance globale (privée)
_context_manager = None
_context_manager_lock = asyncio.Lock()

async def get_context_manager(config: Optional[Config] = None) -> ContextManager:
    """
    Récupère l'instance globale du gestionnaire de contexte.
    L'initialise si nécessaire.
    
    Args:
        config: Configuration optionnelle
        
    Returns:
        L'instance du gestionnaire de contexte
    """
    global _context_manager
    
    if _context_manager is None:
        async with _context_manager_lock:
            if _context_manager is None:
                logger.info("Initialisation du gestionnaire de contexte depuis get_context_manager")
                
                # Si config est None, créer un objet Config avec des valeurs par défaut
                if config is None:
                    from app.config import Config
                    logger.warning("Configuration nulle fournie à get_context_manager, utilisation des valeurs par défaut")
                    config = Config(
                        context_cache_size=1000,
                        context_cache_ttl=3600,
                        context_cleanup_interval=3600,
                        context_save_interval=300,
                        context_max_age_days=30,
                        context_auto_cleanup=True
                    )
                
                _context_manager = ContextManager(config)
                await _context_manager.initialize()
    
    return _context_manager

async def ensure_initialized() -> None:
    """S'assure que le gestionnaire de contexte est initialisé"""
    await get_context_manager()

async def close_context_manager() -> None:
    """Ferme proprement le gestionnaire de contexte"""
    global _context_manager
    
    if _context_manager is not None:
        async with _context_manager_lock:
            if _context_manager is not None:
                await _context_manager.close()
                _context_manager = None
                logger.info("Gestionnaire de contexte fermé")

async def get_or_create_session_context(
    config: Config, 
    room_id: str, 
    user_id: str
) -> Dict[str, Any]:
    """Récupère ou crée un contexte de session.
    
    Args:
        config: Configuration
        room_id: ID du salon
        user_id: ID de l'utilisateur
        
    Returns:
        Le contexte de session
    """
    # Récupérer le gestionnaire de contexte
    context_manager = await get_context_manager(config)
    
    # Créer l'ID de session
    session_id = f"{room_id}_{user_id}"
    
    # Récupérer le contexte s'il existe
    from app.services.context.types import ContextType
    session_context = await context_manager.get_context(session_id, ContextType.SESSION)
    
    if session_context is None:
        # Créer un nouveau contexte
        session_context = await context_manager.create_context(
            session_id,
            ContextType.SESSION,
            {
                "session_id": session_id,
                "room_id": room_id,
                "user_id": user_id,
                "history": [],
                "conversation_state": {}
            }
        )
    
    return session_context