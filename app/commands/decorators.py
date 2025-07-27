"""
Module contenant les décorateurs unifiés pour les commandes Albert.

Ce module fournit trois décorateurs principaux:
- albert_command: Pour les commandes simples (une seule interaction)
- albert_thread_command: Pour initialiser une commande avec thread
- albert_thread_response: Pour gérer les réponses à une commande avec thread

Ces décorateurs unifient la gestion des erreurs, les timeouts et la préservation du contexte.
"""

import asyncio
import functools
import inspect
import time
import traceback
from typing import Callable, Optional, List, Dict, Any, Union

from app.matrix_bot.config import logger
from app.matrix_bot.client import MatrixClient
from app.matrix_bot.eventparser import EventParser

# Utiliser les décorateurs existants de notre registre
from app.commands.registry import (
    register_feature, 
    threaded_command as thread_command, 
    thread_response,
    CommandThread
)
from app.commands.registry import RoomMessageText  # Pour les types d'événements

# Types pour les fonctions de validation
ValidateFunc = Callable[[str], bool]

# Import du service d'initialisation
from app.services.initialization import get_service_instance, is_service_initialized

# Implémentation locale des fonctions d'historique
def add_to_history(history: List[Dict[str, str]], role: str, content: str) -> List[Dict[str, str]]:
    """
    Ajoute un message à l'historique de conversation
    
    Args:
        history: Historique existant
        role: Rôle du message ('user' ou 'assistant')
        content: Contenu du message
        
    Returns:
        Historique mis à jour
    """
    if not history:
        history = []
    
    # Éviter d'ajouter des messages vides
    if not content or not content.strip():
        return history
    
    # Ajouter le nouveau message
    history.append({
        "role": role,
        "content": content.strip()
    })
    
    return history

# Ajouter la fonction manquante with_initialized_services
async def inject_services(func, *args, **kwargs):
    """
    S'assure que les services nécessaires sont initialisés avant d'appeler la fonction.
    Cette fonction est différente de with_initialized_services dans initialization.py:
    - Cette version (inject_services) gère un appel avec des arguments et services spécifiques
    - La version dans initialization.py est un décorateur qui initialise tous les services
    
    Args:
        func: Fonction à exécuter ou coroutine
        *args: Arguments positionnels à passer à la fonction
        **kwargs: Arguments nommés à passer à la fonction contenant les instances de service
        
    Returns:
        Le résultat de la fonction
    """
    logger.info("Les services sont déjà initialisés")
    
    # Si c'est déjà un coroutine, l'attendre
    if inspect.iscoroutine(func):
        logger.debug(f"inject_services: func est déjà un coroutine, attente...")
        func = await func
        logger.debug(f"inject_services: coroutine func terminé, retournant le résultat")
        return func

    # Si c'est une fonction
    if callable(func):
        # Pour chaque service demandé, essayer de l'initialiser s'il est None
        for service_name, service in kwargs.items():
            if service is None:
                # Tenter de récupérer le service depuis le registre
                from app.services.initialization import is_service_initialized, get_service_instance
                if is_service_initialized(service_name):
                    kwargs[service_name] = get_service_instance(service_name)
                else:
                    logger.warning(f"Service '{service_name}' requis mais non initialisé")
                    # On continue même si le service n'est pas disponible
        
        try:
            # Cas spécial: handle_conversation nécessite ep et matrix_client
            if func.__name__ == "handle_conversation" and len(args) == 0 and "ep" not in kwargs and "matrix_client" not in kwargs:
                logger.debug("inject_services: fonction handle_conversation détectée sans arguments, impossible d'appeler")
                # Dans ce cas, on ne peut pas appeler la fonction car elle a besoin d'arguments spécifiques
                raise TypeError(f"{func.__name__}() missing 2 required positional arguments: 'ep' and 'matrix_client'")
                
            # Appeler la fonction avec les services injectés
            result = func(*args, **kwargs)
            
            # Si la fonction retourne un coroutine, l'attendre
            if inspect.iscoroutine(result):
                logger.debug(f"inject_services: le résultat est un coroutine, attente...")
                result = await result
                logger.debug(f"inject_services: coroutine résultat terminé")
            
            return result
        except Exception as e:
            # Capturer et logger l'erreur
            logger.error(f"ERREUR CRITIQUE lors de l'exécution de with_initialized_services: {str(e)}")
            # Propager l'erreur
            raise
    
    # Si func n'est ni un coroutine ni une fonction mais une valeur directe, la retourner
    return func

# Garder la compatibilité avec le code existant
async def with_initialized_services(func, *args, **kwargs):
    """
    Version de compatibilité qui délègue à inject_services.
    Conservée pour maintenir les imports existants fonctionnels.
    
    Cette fonction va être dépréciée dans les versions futures.
    Utilisez inject_services à la place.
    """
    return await inject_services(func, *args, **kwargs)

def albert_command(
    group: str,
    command: str,
    aliases: Optional[List[str]] = None,
    help_text: Optional[str] = None,
    for_geek: bool = False,
    preserve_context: bool = True,
    timeout: Optional[float] = None,
    required_services: Optional[List[str]] = None
) -> Callable:
    """
    Décorateur unifié pour les commandes Albert.
    
    Args:
        group: Groupe de la commande (ex: "utils", "document", etc.)
        command: Nom de la commande
        aliases: Liste des alias de la commande
        help_text: Texte d'aide affiché pour la commande
        for_geek: Si la commande est pour les utilisateurs avancés uniquement
        preserve_context: Si le contexte de conversation doit être préservé
        timeout: Timeout en secondes pour la commande (None = pas de timeout)
        required_services: Liste des services requis (webdav_service, index_service, context_manager, behavior_manager)
    
    Returns:
        La fonction décorée
    
    Example:
        @albert_command(
            group="utils",
            command="echo",
            help_text="!echo <texte> - Répète le texte"
        )
        async def echo_command(ep, matrix_client):
            return f"Echo: {ep.args_str}"
    """
    def decorator(func):
        @register_feature(
            group=group,
            onEvent=RoomMessageText,
            command=command,
            aliases=aliases,
            help=help_text,
            for_geek=for_geek
        )
        @functools.wraps(func)
        async def wrapper(ep: EventParser, matrix_client: MatrixClient):
            # Configuration de base
            config = getattr(matrix_client, "albert_config", matrix_client.config)
            room_id = ep.room.room_id
            sender = ep.sender
            
            # Journalisation détaillée du démarrage
            logger.info(f"[ALBERT CMD] Démarrage commande '{command}' - Sender: {sender}, Room: {room_id}")
            logger.info(f"[ALBERT CMD] Event Type: {type(ep.event).__name__}, Event ID: {ep.event.event_id}")
            
            # Extraire le texte du message
            message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
            
            # Envoyer l'indicateur "en train d'écrire" si la méthode existe
            try:
                if hasattr(matrix_client, 'room_typing'):
                    await matrix_client.room_typing(room_id, typing_state=True)
            except Exception as e:
                logger.warning(f"Impossible d'activer l'indicateur de frappe: {str(e)}")
            
            try:
                # Si le message est uniquement la commande elle-même, fournir l'aide
                parts = message_text.split(maxsplit=1)
                if len(parts) == 1:
                    return f"""❓ **Comment utiliser !{command}**
                    
Pour utiliser cette commande, ajoutez votre question après la commande:
```
!{command} Votre question sur les documents
```

Exemple: `!{command} Quelles sont les procédures à suivre pour un appel d'offres?`
"""
                    
                # Récupérer le texte de la requête
                query = parts[1].strip()
                
                # Log pour débogage
                logger.info(f"[DOCQUERY] Requête: '{query}'")
                
                # Initialiser le contexte si demandé
                if preserve_context:
                    try:
                        # Récupérer le contexte de session
                        from app.commands import get_unified_session_context
                        session_context = await get_unified_session_context(config, room_id, sender)
                        
                        # Ajouter la requête à l'historique
                        session_context.add_message("user", message_text)
                        
                        logger.debug(f"Historique de conversation mis à jour, {len(session_context.history)} messages")
                    except Exception as e:
                        logger.warning(f"Erreur lors de la gestion du contexte: {str(e)}")
                        # Continuer malgré l'erreur
                
                # Vérifier si nous avons un timeout
                if timeout is not None:
                    # Exécuter avec timeout
                    task = asyncio.create_task(func(ep, matrix_client))
                    try:
                        # Attendre avec timeout
                        logger.info(f"[ALBERT CMD] Exécution de '{command}' avec timeout de {timeout} secondes")
                        result = await asyncio.wait_for(task, timeout=timeout)
                    except asyncio.TimeoutError:
                        # En cas de timeout, annuler la tâche et informer l'utilisateur
                        logger.warning(f"[ALBERT CMD] Timeout pour la commande '{command}' après {timeout} secondes")
                        task.cancel()
                        return f"⚠️ La commande a pris trop de temps à s'exécuter et a été annulée (> {timeout} secondes)."
                else:
                    # Exécution normale sans timeout
                    logger.info(f"[ALBERT CMD] Exécution de '{command}' sans timeout")
                    result = await func(ep, matrix_client)
                
                # Si le résultat est None, convertir en chaîne vide pour éviter des erreurs
                if result is None:
                    result = ""
                
                # Si le résultat est un coroutine, attendre son exécution
                if inspect.iscoroutine(result):
                    result = await result
                
                # Si le résultat est une chaîne, l'ajouter à l'historique
                if preserve_context and isinstance(result, str) and result.strip():
                    try:
                        from app.commands import get_unified_session_context
                        session_context = await get_unified_session_context(config, room_id, sender)
                        session_context.add_message("assistant", result)
                        logger.debug(f"Réponse ajoutée à l'historique, total: {len(session_context.history)} messages")
                    except Exception as e:
                        logger.warning(f"Erreur lors de la mise à jour de l'historique: {str(e)}")
                
                # Désactiver l'indicateur "en train d'écrire" si la méthode existe
                try:
                    if hasattr(matrix_client, 'room_typing'):
                        await matrix_client.room_typing(room_id, typing_state=False)
                except Exception as e:
                    # Ignorer silencieusement, c'est juste une amélioration de l'UX
                    pass
                    
                # Ajouter des logs de diagnostic pour l'envoi de message
                logger.info(f"[ALBERT CMD DEBUG] Préparation à retourner le résultat pour '{command}', type: {type(result)}")
                if isinstance(result, str):
                    logger.info(f"[ALBERT CMD DEBUG] Longueur du résultat: {len(result)}")
                    if len(result) > 100:
                        logger.info(f"[ALBERT CMD DEBUG] Début du résultat: {result[:100]}...")
                    else:
                        logger.info(f"[ALBERT CMD DEBUG] Résultat complet: {result}")
                else:
                    logger.info(f"[ALBERT CMD DEBUG] Résultat non textuel: {repr(result)}")
                
                # Injecter les services si nécessaires
                if required_services:
                    try:
                        # La fonction with_initialized_services gère automatiquement tous les cas
                        # (que result soit une fonction, une coroutine ou une valeur)
                        result = await inject_services(result, **{
                            service_name: getattr(matrix_client, service_name) 
                            for service_name in required_services
                        })
                    except Exception as e:
                        logger.error(f"Erreur lors de l'injection des services: {str(e)}")
                        # Continuer sans bloquer en cas d'erreur
                
                return result
                
            except asyncio.TimeoutError:
                logger.error(f"[ALBERT CMD] Timeout: La commande '{command}' a dépassé le délai de {timeout}s")
                return f"""⏱️ **Délai dépassé**

Je n'ai pas pu terminer le traitement de votre demande dans le délai imparti.
Veuillez réessayer avec une requête plus simple ou contacter l'administrateur si le problème persiste."""
                
            except Exception as e:
                logger.error(f"[ALBERT CMD] Erreur lors de l'exécution de '{command}': {str(e)}")
                logger.error(f"[ALBERT CMD] Détails: {traceback.format_exc()}")
                return f"""❌ **Erreur lors du traitement de la commande**

Une erreur est survenue lors du traitement de votre demande:
```
{str(e)}
```

Veuillez réessayer ou contacter l'administrateur si le problème persiste."""
                
            finally:
                # S'assurer que l'indicateur de frappe est désactivé, même en cas d'erreur
                try:
                    if hasattr(matrix_client, 'room_typing'):
                        await matrix_client.room_typing(room_id, typing_state=False)
                except:
                    pass
        
        # Ajouter des métadonnées à la fonction
        wrapper.is_albert_command = True
        wrapper.command_name = command
        wrapper.command_group = group
        wrapper.command_timeout = timeout
        
        return wrapper
    
    return decorator

def albert_thread_command(
    thread_name: str,
    group: str,
    command: str,
    aliases: Optional[List[str]] = None,
    help_text: Optional[str] = None,
    for_geek: bool = False,
    preserve_context: bool = True,
    timeout: Optional[float] = None
) -> Callable:
    """
    Décorateur pour les commandes qui initient un thread de conversation.
    
    Args:
        thread_name: Nom du thread (pour le suivi des réponses)
        group: Groupe de la commande
        command: Nom de la commande
        aliases: Liste des alias de la commande
        help_text: Texte d'aide affiché pour la commande
        for_geek: Si la commande est pour les utilisateurs avancés uniquement
        preserve_context: Si le contexte de conversation doit être préservé
        timeout: Timeout en secondes pour la commande (None = pas de timeout)
    
    Returns:
        La fonction décorée
    
    Example:
        @albert_thread_command(
            thread_name="calc",
            group="utils",
            command="calculatrice"
        )
        async def calculatrice_command(ep, matrix_client):
            # Initialiser un thread
            return "Entrez une expression"
    """
    def decorator(func):
        @register_feature(
            group=group,
            onEvent=RoomMessageText,
            command=command,
            aliases=aliases,
            help=help_text,
            for_geek=for_geek
        )
        @thread_command(thread_name)
        @functools.wraps(func)
        async def wrapper(ep: EventParser, matrix_client: MatrixClient):
            # Configuration de base
            config = getattr(matrix_client, "albert_config", matrix_client.config)
            room_id = ep.room.room_id
            sender = ep.sender
            
            # Informations pour le logging
            event_type = type(ep.event).__name__
            event_id = getattr(ep.event, 'event_id', 'Inconnu')
            
            # Logs au démarrage
            logger.info(f"[ALBERT THREAD] Démarrage thread '{thread_name}' via commande '{command}' - Sender: {sender}, Room: {room_id}")
            logger.info(f"[ALBERT THREAD] Event Type: {event_type}, Event ID: {event_id}")
            
            # Capturer le temps de démarrage
            start_time = time.time()
            
            # Activer l'indicateur de frappe
            await matrix_client.room_typing(room_id, typing_state=True)
            
            try:
                # Vérifier si nous avons un timeout
                if timeout is not None:
                    # Exécuter avec timeout
                    task = asyncio.create_task(func(ep, matrix_client))
                    try:
                        # Attendre avec timeout
                        logger.info(f"[ALBERT THREAD] Exécution de '{command}' avec timeout de {timeout} secondes")
                        result = await asyncio.wait_for(task, timeout=timeout)
                    except asyncio.TimeoutError:
                        # En cas de timeout, annuler la tâche et informer l'utilisateur
                        logger.warning(f"[ALBERT THREAD] Timeout pour la commande '{command}' après {timeout} secondes")
                        
                        # Essayer d'annuler la tâche (peut ne pas fonctionner si bloquée en IO)
                        task.cancel()
                        
                        # Terminer le thread
                        await CommandThread.end(
                            room_id=room_id, 
                            sender=sender, 
                            thread_name=thread_name,
                            user_id=sender,
                            command_name=command,
                            config=config,
                            status="timeout",
                            error=f"Timeout après {timeout} secondes"
                        )
                        
                        # Envoyer un message d'erreur
                        return f"⚠️ La commande a pris trop de temps à s'exécuter et a été annulée (> {timeout} secondes)."
                else:
                    # Exécution normale sans timeout
                    logger.info(f"[ALBERT THREAD] Exécution de '{command}' sans timeout")
                    result = await func(ep, matrix_client)
                
                # Calculer le temps d'exécution
                execution_time = time.time() - start_time
                logger.info(f"[ALBERT THREAD] Commande '{command}' terminée en {execution_time:.2f} secondes")
                
                # Si preserve_context est activé, mettre à jour l'historique
                if preserve_context and result:
                    # Construction du contexte historique
                    from app.commands import get_unified_session_context
                    session_context = await get_unified_session_context(config, room_id, sender)
                    
                    # Créer l'historique si la commande a réussi
                    if inspect.iscoroutine(result):
                        result = await result
                    
                    # Ajouter à l'historique qu'en cas de message réponse (pas None)
                    if result:
                        logger.info(f"[ALBERT THREAD] Mise à jour du contexte pour '{command}'")
                        
                        # Récupérer l'historique existant
                        history = session_context.history or []
                        
                        # Ajouter le message de l'utilisateur
                        user_message = f"!{command}"
                        if hasattr(ep, 'args_str'):
                            user_message = f"{user_message} {ep.args_str}".strip()
                        history = add_to_history(history, "user", user_message)
                        
                        # Ajouter la réponse d'Albert
                        history = add_to_history(history, "assistant", result)
                        
                        # Mettre à jour le contexte
                        session_context.history = history
                        session_context.last_command = command
                        session_context.thread_command = thread_name
                        session_context.last_command_time = time.time()
                        
                        # Sauvegarder
                        from app.services.context.instance import get_context_manager
                        from app.services.context.types import ContextType
                        context_manager = await get_context_manager()
                        await context_manager.update_context(
                            session_context.session_id, 
                            ContextType.SESSION, 
                            session_context.to_dict()
                        )
                
                # Retourner le résultat
                return result
            
            except Exception as e:
                # Gérer les exceptions
                error_traceback = traceback.format_exc()
                logger.error(f"[ALBERT THREAD] Erreur dans la commande '{command}':\n{error_traceback}")
                
                # Terminer le thread en cas d'erreur
                try:
                    await CommandThread.end(
                        room_id=room_id, 
                        sender=sender, 
                        thread_name=thread_name,
                        user_id=sender,
                        command_name=command,
                        config=config,
                        status="error",
                        error=str(e)
                    )
                except Exception as end_err:
                    logger.error(f"[ALBERT THREAD] Erreur lors de la terminaison du thread: {str(end_err)}")
                
                # Construire un message d'erreur pour l'utilisateur
                error_message = f"⚠️ Une erreur est survenue lors de l'exécution de la commande: {str(e)}"
                
                # Retourner l'erreur
                return error_message
            
            finally:
                # Désactiver l'indicateur de frappe
                await matrix_client.room_typing(room_id, typing_state=False)
        
        # Ajouter des métadonnées à la fonction
        wrapper.is_albert_command = True
        wrapper.is_thread_command = True
        wrapper.command_name = command
        wrapper.thread_name = thread_name
        wrapper.command_group = group
        wrapper.command_timeout = timeout
        
        return wrapper
    
    return decorator

def albert_thread_response(
    thread_name: str,
    validate_format: Optional[ValidateFunc] = None,
    preserve_context: bool = True,
    timeout: Optional[float] = None
) -> Callable:
    """
    Décorateur pour les fonctions qui gèrent les réponses aux threads.
    
    Args:
        thread_name: Nom du thread (doit correspondre à celui de la commande initiale)
        validate_format: Fonction optionnelle pour valider le format de la réponse
        preserve_context: Si le contexte de conversation doit être préservé
        timeout: Timeout en secondes pour le traitement de la réponse (None = pas de timeout)
    
    Returns:
        La fonction décorée
    
    Example:
        @albert_thread_response(
            thread_name="calc",
            validate_format=lambda text: re.match(r'^[\d\+\-\*\/\(\)\.]+$', text) is not None
        )
        async def calculatrice_response(ep, matrix_client):
            # Traiter la réponse
            return f"Résultat: {eval(ep.event.body)}"
    """
    def decorator(func):
        @register_feature(
            group="thread_response",
            onEvent=RoomMessageText,
            command="",  # Pas de commande spécifique pour les réponses
            help=""  # Pas d'aide pour les réponses
        )
        @thread_response(command_name=thread_name)
        @functools.wraps(func)
        async def wrapper(ep: EventParser, matrix_client: MatrixClient):
            # Configuration de base
            config = getattr(matrix_client, "albert_config", matrix_client.config)
            room_id = ep.room.room_id
            sender = ep.sender
            
            # Journalisation
            logger.info(f"[ALBERT RESPONSE] Réponse reçue pour le thread '{thread_name}' - Sender: {sender}, Room: {room_id}")
            
            # Récupérer les données du thread s'il est actif
            is_active, thread_info = await CommandThread.is_active(room_id, sender, config)
            if not is_active or thread_info != thread_name:
                logger.info(f"[ALBERT RESPONSE] Thread '{thread_name}' non actif, ignoré")
                raise EventNotConcerned
            
            # Si validation requise, vérifier le format
            if validate_format:
                # Récupérer le texte du message
                message_text = ep.event.body if hasattr(ep.event, 'body') else ""
                
                # Valider le format
                if not validate_format(message_text):
                    logger.info(f"[ALBERT RESPONSE] Format invalide pour '{thread_name}': '{message_text}'")
                    return "⚠️ Format de réponse invalide. Veuillez réessayer."
            
            # Activer l'indicateur de frappe
            await matrix_client.room_typing(room_id, typing_state=True)
            
            try:
                # Exécuter avec ou sans timeout selon la configuration
                if timeout is not None:
                    task = asyncio.create_task(func(ep, matrix_client))
                    try:
                        logger.info(f"[ALBERT RESPONSE] Traitement avec timeout de {timeout} secondes")
                        result = await asyncio.wait_for(task, timeout=timeout)
                    except asyncio.TimeoutError:
                        # Timeout dépassé
                        logger.warning(f"[ALBERT RESPONSE] Timeout pour la réponse du thread '{thread_name}' après {timeout} secondes")
                        
                        # Annuler la tâche
                        task.cancel()
                        
                        # Message d'erreur
                        return f"⚠️ Le traitement de votre réponse a pris trop de temps (> {timeout} secondes). Veuillez réessayer ou tenter une autre approche."
                else:
                    # Pas de timeout
                    logger.info(f"[ALBERT RESPONSE] Traitement sans timeout")
                    result = await func(ep, matrix_client)
                
                # Si preserve_context est activé, mettre à jour l'historique
                if preserve_context and result:
                    # Construction du contexte historique
                    from app.commands import get_unified_session_context
                    session_context = await get_unified_session_context(config, room_id, sender)
                    
                    # Créer l'historique si la commande a réussi
                    if inspect.iscoroutine(result):
                        result = await result
                    
                    # Ajouter à l'historique qu'en cas de message réponse (pas None)
                    if result:
                        logger.info(f"[ALBERT RESPONSE] Mise à jour du contexte pour la réponse")
                        
                        # Récupérer l'historique existant
                        history = session_context.history or []
                        
                        # Ajouter le message de l'utilisateur
                        user_message = ep.event.body if hasattr(ep.event, 'body') else ""
                        history = add_to_history(history, "user", user_message)
                        
                        # Ajouter la réponse d'Albert
                        history = add_to_history(history, "assistant", result)
                        
                        # Mettre à jour le contexte
                        session_context.history = history
                        session_context.last_response_time = time.time()
                        
                        # Sauvegarder
                        from app.services.context.instance import get_context_manager
                        from app.services.context.types import ContextType
                        context_manager = await get_context_manager()
                        await context_manager.update_context(
                            session_context.session_id, 
                            ContextType.SESSION, 
                            session_context.to_dict()
                        )
                
                # Retourner le résultat
                return result
            
            except Exception as e:
                # Gérer les exceptions
                error_traceback = traceback.format_exc()
                logger.error(f"[ALBERT RESPONSE] Erreur dans le traitement de la réponse:\n{error_traceback}")
                
                # Construire un message d'erreur pour l'utilisateur
                error_message = f"⚠️ Une erreur est survenue lors du traitement de votre réponse: {str(e)}"
                
                # Retourner l'erreur
                return error_message
            
            finally:
                # Désactiver l'indicateur de frappe
                await matrix_client.room_typing(room_id, typing_state=False)
        
        # Ajouter des métadonnées à la fonction
        wrapper.is_albert_response = True
        wrapper.thread_name = thread_name
        wrapper.has_validation = validate_format is not None
        wrapper.response_timeout = timeout
        
        return wrapper
    
    return decorator

# Décorateur pour garantir l'initialisation asynchrone d'un service
def ensure_async_initialized(service_getter_func: Callable) -> Callable:
    """
    Décorateur qui garantit qu'un service asynchrone est correctement initialisé avant utilisation.
    
    Args:
        service_getter_func: Fonction asynchrone qui retourne le service initialisé
        
    Returns:
        Décorateur qui garantit l'initialisation
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Récupérer le service initialisé
            service = await service_getter_func(*args, **kwargs)
            
            # Passer le service comme argument supplémentaire
            if 'initialized_service' in inspect.signature(func).parameters:
                kwargs['initialized_service'] = service
                return await func(*args, **kwargs)
            else:
                # Si la fonction ne prend pas le service en paramètre, 
                # on l'ajoute simplement au contexte d'exécution
                return await func(*args, **kwargs)
        return wrapper
    return decorator

# Décorateur pour éviter les imports circulaires
def with_dynamic_import(module_path: str, attribute_name: str) -> Callable:
    """
    Décorateur qui importe dynamiquement un module/attribut au moment de l'exécution
    pour éviter les imports circulaires.
    
    Args:
        module_path: Chemin du module à importer
        attribute_name: Nom de l'attribut à extraire du module
        
    Returns:
        Décorateur qui injecte l'attribut importé
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            # Import dynamique au moment de l'exécution
            module = __import__(module_path, fromlist=[attribute_name])
            attribute = getattr(module, attribute_name)
            
            # Passer l'attribut comme argument supplémentaire
            if attribute_name in inspect.signature(func).parameters:
                kwargs[attribute_name] = attribute
                return await func(*args, **kwargs)
            else:
                # Sinon, on exécute simplement la fonction
                return await func(*args, **kwargs)
        return wrapper
    return decorator

# Décorateur pour injecter les services nécessaires dans les commandes
def with_services(*service_names):
    """
    Décorateur qui injecte automatiquement les services requis dans une fonction.
    
    Args:
        *service_names: Noms des services à injecter (webdav_service, index_service, context_manager, behavior_manager)
        
    Returns:
        Décorateur qui injecte les services
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        @ensure_async_initialized
        async def wrapper(ep: EventParser, matrix_client: MatrixClient, *args, **kwargs):
            # Récupérer la configuration
            config = getattr(matrix_client, "albert_config", matrix_client.config)
            
            # Injecter les services demandés
            for service_name in service_names:
                if service_name not in kwargs:
                    if service_name == 'webdav_service':
                        from app.services.webdav_service_factory import get_webdav_service
                        kwargs[service_name] = await get_webdav_service(config)
                    elif service_name == 'index_service':
                        from app.index.types import get_index_service
                        kwargs[service_name] = await get_index_service(config)
                    elif service_name == 'context_manager':
                        from app.services.context.instance import get_context_manager
                        kwargs[service_name] = await get_context_manager(config)
                    elif service_name == 'behavior_manager':
                        from app.services.initialization import get_service_instance
                        kwargs[service_name] = get_service_instance('behavior_manager')
            
            # Exécuter la fonction avec les services injectés
            return await func(ep, matrix_client, *args, **kwargs)
        
        return wrapper
    
    return decorator 