"""
Gestionnaires du cycle de vie des commandes.

Ce module fournit des outils pour gérer de manière centralisée le cycle de vie
des commandes, indépendamment de leur logique métier spécifique.
"""

import time
import asyncio
from functools import wraps
from typing import Dict, Any, Optional
import traceback

from app.matrix_bot.client import MatrixClient
from app.matrix_bot.config import logger
from app.matrix_bot.eventparser import EventParser

from app.services.context.types import ContextType
from app.services.context.instance import get_context_manager, ensure_initialized
from app.commands import get_unified_session_context
from app.commands.registry import mark_event_processed


class CommandLifecycleManager:
    """Gère le cycle de vie des commandes indépendamment de leur logique"""
    
    @staticmethod
    async def begin_command(room_id, user_id, command_name, config):
        """Marque le début d'une commande et prépare le contexte.
        
        Args:
            room_id: ID du salon
            user_id: ID de l'utilisateur
            command_name: Nom de la commande
            config: Configuration
            
        Returns:
            Le contexte de session nettoyé
        """
        # LOGS DE DIAGNOSTIC DÉTAILLÉS
        logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Démarrage pour {command_name}, user={user_id}, room={room_id}")
        
        try:
            # S'assurer que le gestionnaire de contexte est initialisé
            logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Initialisation du gestionnaire de contexte")
            await ensure_initialized()
            logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Gestionnaire de contexte initialisé")
            
            logger.info(f"[COMMAND] Début de la commande {command_name} pour {user_id} dans {room_id}")
            
            # Récupérer le contexte de session actuel
            logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Récupération du contexte de session")
            session_context = await get_unified_session_context(config, room_id, user_id)
            logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Contexte de session récupéré")
            
            # Vérifier si le contexte contient des drapeaux bloquants
            if hasattr(session_context, "conversation_state"):
                logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: État de conversation trouvé, vérification des drapeaux")
                # Nettoyage préventif au début de la commande
                if "in_command_thread" in session_context.conversation_state:
                    logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Drapeau in_command_thread détecté, vérification...")
                    # Si déjà dans un thread, vérifier s'il est actif depuis longtemps
                    thread_start = session_context.conversation_state.get("thread_start_time", 0)
                    thread_age = time.time() - thread_start
                    logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Âge du thread: {thread_age:.2f}s")
                    
                    if thread_age > 300:  # 5 minutes
                        logger.warning(f"[COMMAND] Thread actif depuis plus de 5 minutes, nettoyage forcé")
                        session_context.conversation_state.pop("in_command_thread", None)
                        session_context.conversation_state.pop("thread_command", None)
                        logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Thread ancien nettoyé")
                    else:
                        logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Thread récent ({thread_age:.2f}s), conservé")
                
                # Mise à jour des données de commande
                logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Mise à jour des données de commande")
                session_context.conversation_state["current_command"] = command_name
                session_context.conversation_state["command_start_time"] = time.time()
                session_context.conversation_state["command_completed"] = False
                
                # Mise à jour du contexte
                logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Mise à jour du contexte dans le gestionnaire")
                session_id = f"{room_id}_{user_id}"
                context_manager = await get_context_manager()
                await context_manager.update_context(session_id, ContextType.SESSION, session_context.to_dict())
                logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Contexte mis à jour avec succès")
            else:
                logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Pas d'état de conversation trouvé")
            
            logger.info(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Terminé avec succès pour {command_name}")
            return session_context
        except Exception as e:
            logger.error(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: ERREUR pour {command_name}: {str(e)}")
            logger.error(f"[LIFECYCLE DEBUG] BEGIN_COMMAND: Traceback: {traceback.format_exc()}")
            raise
    
    @staticmethod
    async def end_command(room_id, user_id, command_name, config, success=True, **result_data):
        """Marque la fin d'une commande et nettoie le contexte.
        
        Args:
            room_id: ID du salon
            user_id: ID de l'utilisateur
            command_name: Nom de la commande
            config: Configuration
            success: Si la commande s'est terminée avec succès
            **result_data: Données de résultat à stocker dans le contexte
            
        Returns:
            Le contexte de session mis à jour
        """
        # S'assurer que le gestionnaire de contexte est initialisé
        await ensure_initialized()
        
        logger.info(f"[COMMAND] Fin de la commande {command_name} pour {user_id} dans {room_id} (succès: {success})")
        
        # Récupérer le contexte de session actuel
        session_context = await get_unified_session_context(config, room_id, user_id)
        
        # Nettoyage et mise à jour du contexte
        if hasattr(session_context, "conversation_state"):
            # Si la commande est simple (non thread), nettoyer les drapeaux
            if session_context.conversation_state.get("current_command") == command_name:
                session_context.conversation_state.pop("in_command_thread", None)
            
            # Marquer la commande comme terminée
            session_context.conversation_state["command_completed"] = True
            session_context.conversation_state["last_command"] = command_name
            
            # Statistiques d'exécution
            start_time = session_context.conversation_state.get("command_start_time", time.time())
            execution_time = time.time() - start_time
            session_context.conversation_state["last_execution_time"] = execution_time
            
            # Stockage du statut
            session_context.conversation_state["final_status"] = "success" if success else "error"
            
            # Ajouter les données de résultat au contexte
            for key, value in result_data.items():
                session_context.conversation_state[key] = value
            
            # Mise à jour du contexte
            session_id = f"{room_id}_{user_id}"
            context_manager = await get_context_manager()
            await context_manager.update_context(session_id, ContextType.SESSION, session_context.to_dict())
        
        return session_context


class ThreadedCommandManager:
    """Gère les commandes qui nécessitent plusieurs interactions"""
    
    @staticmethod
    async def start_thread(room_id, user_id, thread_name, config, **initial_data):
        """Démarre un thread de commande.
        
        Args:
            room_id: ID du salon
            user_id: ID de l'utilisateur
            thread_name: Nom du thread
            config: Configuration
            **initial_data: Données initiales du thread
            
        Returns:
            Le contexte de session mis à jour
        """
        # S'assurer que le gestionnaire de contexte est initialisé
        await ensure_initialized()
        
        logger.info(f"[THREAD] Début du thread {thread_name} pour {user_id} dans {room_id}")
        
        # Récupérer le contexte de session actuel
        session_context = await get_unified_session_context(config, room_id, user_id)
        
        # Configuration du thread
        if hasattr(session_context, "conversation_state"):
            # Définir les paramètres du thread
            session_context.conversation_state["in_command_thread"] = True
            session_context.conversation_state["thread_command"] = thread_name
            session_context.conversation_state["thread_start_time"] = time.time()
            session_context.conversation_state["command_completed"] = False
            
            # Ajouter les données initiales
            for key, value in initial_data.items():
                session_context.conversation_state[key] = value
            
            # Mise à jour du contexte
            session_id = f"{room_id}_{user_id}"
            context_manager = await get_context_manager()
            await context_manager.update_context(session_id, ContextType.SESSION, session_context.to_dict())
        
        return session_context
    
    @staticmethod
    async def end_thread(room_id, user_id, thread_name, config, **result_data):
        """Termine un thread de commande.
        
        Args:
            room_id: ID du salon
            user_id: ID de l'utilisateur
            thread_name: Nom du thread
            config: Configuration
            **result_data: Données de résultat du thread
            
        Returns:
            Le contexte de session mis à jour
        """
        # S'assurer que le gestionnaire de contexte est initialisé
        await ensure_initialized()
        
        logger.info(f"[THREAD] Fin du thread {thread_name} pour {user_id} dans {room_id}")
        
        # Récupérer le contexte de session actuel
        session_context = await get_unified_session_context(config, room_id, user_id)
        
        # Nettoyage du thread
        if hasattr(session_context, "conversation_state"):
            # Nettoyer les drapeaux du thread
            session_context.conversation_state.pop("in_command_thread", None)
            session_context.conversation_state.pop("thread_command", None)
            
            # Marquer la commande comme terminée
            session_context.conversation_state["command_completed"] = True
            session_context.conversation_state["last_thread_command"] = thread_name
            
            # Statistiques d'exécution
            start_time = session_context.conversation_state.get("thread_start_time", time.time())
            execution_time = time.time() - start_time
            session_context.conversation_state["last_thread_duration"] = execution_time
            
            # Ajouter le statut final
            session_context.conversation_state["final_status"] = "success"
            
            # Ajouter les données de résultat
            for key, value in result_data.items():
                session_context.conversation_state[key] = value
            
            # Mise à jour du contexte
            session_id = f"{room_id}_{user_id}"
            context_manager = await get_context_manager()
            await context_manager.update_context(session_id, ContextType.SESSION, session_context.to_dict())
        
        return session_context


# Décorateurs pour simplifier l'utilisation

def command_lifecycle(command_name=None, timeout=60.0):
    """Décorateur qui gère automatiquement le cycle de vie d'une commande"""
    def decorator(func):
        @wraps(func)
        async def wrapper(ep: EventParser, matrix_client: MatrixClient):
            # Configuration et extraction des données
            config = getattr(matrix_client, "albert_config", matrix_client.config)
            room_id = ep.room.room_id
            sender = ep.sender
            event_id = getattr(ep.event, "event_id", None)
            cmd_name = command_name or func.__name__
            
            # Vérifier si la fonction a un timeout spécifique défini
            fn_timeout = getattr(func, "command_timeout", timeout)
            
            # LOGS DE DIAGNOSTIC DÉTAILLÉS
            if fn_timeout is None:
                logger.info(f"[LIFECYCLE DEBUG] DÉMARRAGE du wrapper pour {cmd_name}, event_id={event_id} (sans timeout)")
            else:
                logger.info(f"[TIMEOUT DEBUG] Démarrage de la commande {cmd_name} avec timeout de {fn_timeout}s")
            
            try:
                # Marquer le début de la commande
                logger.info(f"[LIFECYCLE DEBUG] Appel à begin_command pour {cmd_name}")
                await CommandLifecycleManager.begin_command(room_id, sender, cmd_name, config)
                logger.info(f"[LIFECYCLE DEBUG] begin_command terminé pour {cmd_name}")
                
                # LOGS: Avant exécution de la fonction
                logger.info(f"[LIFECYCLE DEBUG] Avant exécution de {func.__name__}")
                
                # Exécuter la commande avec ou sans timeout selon la configuration
                if fn_timeout is not None:
                    task = asyncio.create_task(func(ep, matrix_client))
                    try:
                        # Attendre avec timeout
                        result = await asyncio.wait_for(task, timeout=fn_timeout)
                    except asyncio.TimeoutError:
                        logger.error(f"[TIMEOUT DEBUG] La commande {cmd_name} a dépassé le délai de {fn_timeout}s")
                        error_msg = f"⚠️ La commande a pris trop de temps (> {fn_timeout}s). Veuillez réessayer ou simplifier votre demande."
                        await CommandLifecycleManager.end_command(
                            room_id, sender, cmd_name, config, 
                            success=False,
                            error="timeout",
                            error_status="timeout"
                        )
                        return error_msg
                else:
                    # Exécution normale sans timeout
                    result = await func(ep, matrix_client)
                
                # LOGS: Après exécution de la fonction
                logger.info(f"[LIFECYCLE DEBUG] Après exécution de {func.__name__}, résultat: {type(result)}")
                
                # Marquer la fin de la commande
                logger.info(f"[LIFECYCLE DEBUG] Appel à end_command pour {cmd_name}")
                await CommandLifecycleManager.end_command(
                    room_id, sender, cmd_name, config, 
                    success=True,
                    result=result if result else {}
                )
                logger.info(f"[LIFECYCLE DEBUG] end_command terminé pour {cmd_name}")
                
                # Marquer l'événement comme traité
                if event_id:
                    logger.info(f"[LIFECYCLE DEBUG] Marquage de l'événement {event_id} comme traité pour {cmd_name}")
                    mark_event_processed(event_id, cmd_name, command_completed=True)
                
                if fn_timeout is None:
                    logger.info(f"[LIFECYCLE DEBUG] FIN du wrapper pour {cmd_name}")
                else:
                    logger.info(f"[TIMEOUT DEBUG] Commande {cmd_name} terminée avec succès dans le délai imparti")
                return result
                
            except Exception as e:
                # Gestion des erreurs
                logger.error(f"[LIFECYCLE DEBUG] ERREUR dans {cmd_name}: {str(e)}")
                logger.error(f"[LIFECYCLE DEBUG] Traceback complet: {traceback.format_exc()}")
                
                # Marquer la fin de la commande, mais avec une erreur
                try:
                    logger.info(f"[LIFECYCLE DEBUG] Tentative de marquage de fin avec erreur pour {cmd_name}")
                    await CommandLifecycleManager.end_command(
                        room_id, sender, cmd_name, config, 
                        success=False,
                        error=str(e),
                        error_status="exception"
                    )
                    logger.info(f"[LIFECYCLE DEBUG] Fin marquée avec erreur pour {cmd_name}")
                except Exception as cleanup_err:
                    logger.error(f"[LIFECYCLE DEBUG] ERREUR lors du marquage de fin: {str(cleanup_err)}")
                
                # Marquer l'événement comme traité même en cas d'erreur
                if event_id:
                    try:
                        logger.info(f"[LIFECYCLE DEBUG] Tentative de marquage de l'événement {event_id} avec erreur")
                        mark_event_processed(event_id, cmd_name, command_completed=True)
                        logger.info(f"[LIFECYCLE DEBUG] Événement marqué avec erreur pour {cmd_name}")
                    except Exception as mark_err:
                        logger.error(f"[LIFECYCLE DEBUG] ERREUR lors du marquage de l'événement: {str(mark_err)}")
                
                logger.info(f"[LIFECYCLE DEBUG] FIN du wrapper (avec erreur) pour {cmd_name}")
                # Propager l'erreur
                raise
        
        return wrapper
    return decorator


def threaded_command_lifecycle(thread_name=None):
    """Décorateur qui gère le cycle de vie d'une commande avec thread"""
    def decorator(func):
        @wraps(func)
        async def wrapper(ep: EventParser, matrix_client: MatrixClient):
            # Configuration et extraction des données
            config = getattr(matrix_client, "albert_config", matrix_client.config)
            room_id = ep.room.room_id
            sender = ep.sender
            event_id = getattr(ep.event, "event_id", None)
            thread_cmd = thread_name or func.__name__
            
            # Marquer le début de la commande 
            # (le thread sera démarré dans la fonction elle-même)
            await CommandLifecycleManager.begin_command(room_id, sender, thread_cmd, config)
            
            try:
                # Exécuter la commande
                result = await func(ep, matrix_client)
                
                # Marquer l'événement comme traité
                if event_id:
                    mark_event_processed(event_id, thread_cmd, command_completed=False)
                
                return result
                
            except Exception as e:
                # Gestion des erreurs
                logger.error(f"Erreur au démarrage du thread {thread_cmd}: {str(e)}")
                
                # Terminer le thread en cas d'erreur
                await ThreadedCommandManager.end_thread(
                    room_id, sender, thread_cmd, config,
                    error=str(e),
                    error_status="exception_startup"
                )
                
                # Marquer l'événement comme traité même en cas d'erreur
                if event_id:
                    mark_event_processed(event_id, thread_cmd, command_completed=True)
                
                # Propager l'erreur
                raise
        
        return wrapper
    return decorator


# Exportation des fonctions et classes principales
__all__ = [
    'CommandLifecycleManager',
    'ThreadedCommandManager',
    'command_lifecycle',
    'threaded_command_lifecycle'
] 