"""Instance globale du gestionnaire de contexte"""
from typing import Any, Optional
import asyncio
from app.matrix_bot.config import logger
from app.config import Config
from .manager import ContextManager

# Instance globale du gestionnaire de contexte
context_manager: Optional[ContextManager] = ContextManager(Config())
_init_lock = asyncio.Lock()

# Flag pour suivre l'état d'initialisation
_initialized = False
# Flag pour le mode dégradé
_degraded_mode = False

async def ensure_initialized():
    """S'assure que le gestionnaire de contexte est initialisé"""
    global context_manager, _initialized, _init_lock, _degraded_mode
    
    import traceback
    try:
        logger.info(f"[CTX INIT DEBUG] ensure_initialized: Démarrage, état actuel: _initialized={_initialized}, mode_dégradé={_degraded_mode}")
        
        # Si déjà en mode dégradé ou initialisé, retourner immédiatement le gestionnaire
        if _initialized:
            logger.info(f"[CTX INIT DEBUG] ensure_initialized: Gestionnaire déjà initialisé, retour immédiat")
            return context_manager
        
        logger.info(f"[CTX INIT DEBUG] ensure_initialized: Tentative d'acquisition du verrou _init_lock")
        async with _init_lock:
            logger.info(f"[CTX INIT DEBUG] ensure_initialized: Verrou _init_lock acquis")
            
            # Revérifier après avoir acquis le verrou (peut avoir changé pendant l'attente)
            if _initialized:
                logger.info(f"[CTX INIT DEBUG] ensure_initialized: Le gestionnaire a été initialisé pendant l'attente du verrou")
                return context_manager
                
            # Initialiser le gestionnaire
            logger.info(f"[CTX INIT DEBUG] ensure_initialized: Le gestionnaire n'est pas initialisé, initialisation en cours...")
            try:
                logger.info(f"[CTX INIT DEBUG] ensure_initialized: Appel à context_manager.initialize()")
                # Augmenter le timeout à 60 secondes (au lieu de 10)
                await asyncio.wait_for(context_manager.initialize(), timeout=60.0)
                _initialized = True
                _degraded_mode = False  # Réinitialiser le mode dégradé en cas de succès
                logger.info("Gestionnaire de contexte global initialisé avec succès")
                logger.info(f"[CTX INIT DEBUG] ensure_initialized: Initialisation réussie, _initialized={_initialized}")
            except asyncio.TimeoutError:
                logger.error("[CTX INIT DEBUG] ensure_initialized: TIMEOUT lors de l'initialisation du gestionnaire de contexte!")
                # Passer en mode dégradé mais marquer comme initialisé pour éviter les blocages
                _initialized = True
                _degraded_mode = True
                # Vérifier si le gestionnaire est en mode dégradé
                if not hasattr(context_manager, '_is_degraded_mode'):
                    # Ajouter l'attribut si nécessaire (compatibilité avec ancienne version)
                    context_manager._is_degraded_mode = True
                else:
                    context_manager._is_degraded_mode = True
                logger.warning("Gestionnaire de contexte initialisé en mode dégradé suite à un timeout")
            except Exception as e:
                logger.error(f"Erreur initialisation gestionnaire de contexte: {str(e)}")
                logger.error(f"[CTX INIT DEBUG] ensure_initialized: Traceback: {traceback.format_exc()}")
                # Passer en mode dégradé mais marquer comme initialisé pour éviter les blocages
                _initialized = True
                _degraded_mode = True
                # Vérifier si le gestionnaire est en mode dégradé
                if not hasattr(context_manager, '_is_degraded_mode'):
                    # Ajouter l'attribut si nécessaire (compatibilité avec ancienne version)
                    context_manager._is_degraded_mode = True
                else:
                    context_manager._is_degraded_mode = True
                logger.warning("Gestionnaire de contexte initialisé en mode dégradé suite à une erreur")
                
        logger.info(f"[CTX INIT DEBUG] ensure_initialized: Verrou _init_lock libéré")
        logger.info(f"[CTX INIT DEBUG] ensure_initialized: Fin réussie, retournant le gestionnaire (mode dégradé: {_degraded_mode})")
        
        return context_manager
    
    except Exception as e:
        logger.error(f"[CTX INIT DEBUG] ensure_initialized: ERREUR NON GÉRÉE: {str(e)}")
        logger.error(f"[CTX INIT DEBUG] ensure_initialized: Traceback: {traceback.format_exc()}")
        # En cas d'erreur critique, passer en mode dégradé pour ne pas bloquer l'application
        _initialized = True  # Marquer comme initialisé malgré l'erreur
        _degraded_mode = True  # Passer en mode dégradé
        # Continuer malgré l'erreur pour ne pas bloquer l'application
        return context_manager

__all__ = ['context_manager', 'ensure_initialized']