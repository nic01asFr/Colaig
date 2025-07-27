"""
Module de gestion des commandes d'Albert.
Ce module contient toutes les fonctionnalités relatives à l'enregistrement et 
à l'exécution des commandes dans Albert.

Il implémente également un système de thread de commandes qui permet aux commandes
d'avoir une conversation continue avec l'utilisateur, avec:
- Maintien du contexte entre les messages
- Suivi de l'état de la conversation
- Système de heartbeat pour prolonger les conversations tant que l'utilisateur est actif
- Nettoyage automatique des états orphelins

Le système de heartbeat met à jour un timestamp d'activité (last_activity_time) à chaque
interaction avec l'utilisateur, permettant aux conversations de rester actives aussi
longtemps que nécessaire sans être interrompues par le nettoyage automatique.
"""

from typing import List, Dict, Any, Optional, Set, Tuple
from dataclasses import dataclass, field
from functools import wraps
from enum import Enum
import time
import re
import asyncio
import traceback
import inspect
import logging

from app.matrix_bot.client import MatrixClient
from app.matrix_bot.config import logger
from app.matrix_bot.eventparser import EventNotConcerned, EventParser
from nio import Event, RoomEncryptedFile, RoomMemberEvent, RoomMessageText

from app.bot_msg import AlbertMsg
from app.config import COMMAND_PREFIX, Config
from app.iam import TchapIam
from app.services.context.types import ContextType

# Structure de registre des commandes
@dataclass
class CommandRegistry:
    function_register: dict = field(default_factory=dict)
    activated_functions: set[str] = field(default_factory=set)

    def add_command(
        self,
        name: str,
        group: str,
        onEvent: Event,
        command: str | None,
        aliases: list[str] | None,
        prefix: str | None,
        help_message: str | None,
        for_geek: bool,
        func,
    ):
        # Initialiser commands comme une liste vide si command est None
        commands = [command] if command else []
        if aliases:
            commands.extend(aliases)
        
        # Si après tout cela, commands est vide, mettre à None
        commands = commands if commands else None

        self.function_register[name] = {
            "name": name,
            "group": group,
            "onEvent": onEvent,
            "commands": commands,
            "prefix": prefix,
            "help": help_message,
            "for_geek": for_geek,
            "func": func,
        }
        
        # Logs supplémentaires pour suivre l'enregistrement des commandes
        if group == "thread_response":
            logger.info(f"[REGISTRY] Enregistrement du handler thread_response: {name}")
            if hasattr(func, "__wrapped__"):
                logger.info(f"[REGISTRY] Le handler {name} a un décorateur __wrapped__")
            # Vérifier si la fonction a des attributs spécifiques
            if hasattr(func, "thread_name"):
                logger.info(f"[REGISTRY] Le handler {name} répond au thread '{func.thread_name}'")
            if hasattr(func, "command_name"):
                logger.info(f"[REGISTRY] Le handler {name} répond à la commande '{func.command_name}'")
        
        # Activer automatiquement la fonction
        self.activated_functions |= {name}
        
        # Log pour confirmer l'activation
        logger.info(f"[REGISTRY] Commande {name} enregistrée et activée (groupe: {group})")

    def activate_and_retrieve_group(self, group_name: str) -> list:
        features = []
        for name, feature in self.function_register.items():
            if feature["group"] == group_name:
                self.activated_functions |= {name}
                features.append(feature)
        return features

    def is_valid_command(self, command) -> bool:
        valid_commands = []
        for name, feature in self.function_register.items():
            if name in self.activated_functions:
                if feature.get("commands"):
                    valid_commands += feature["commands"]
        return command in valid_commands

    def get_help(self, config: Config, verbose: bool = False) -> str:
        cmds = self._get_cmds(config, verbose)
        
        # Améliorons la façon dont nous obtenons le nom du modèle
        model_name = "meta-llama/Llama-3.1-8B-Instruct"  # Valeur par défaut sécurisée
        model_url = "https://huggingface.co/meta-llama/Llama-3.1-8B-Instruct"
        model_short_name = "Llama-3.1-8B-Instruct"
        
        try:
            # Essayer d'abord de récupérer depuis albert_config si disponible
            if hasattr(config, "albert_config") and config.albert_config:
                if hasattr(config.albert_config, "albert_model") and config.albert_config.albert_model:
                    model_name = config.albert_config.albert_model
                    logger.info(f"Using model from albert_config: {model_name}")
            # Sinon, essayer directement sur l'objet config
            elif hasattr(config, "albert_model") and config.albert_model:
                model_name = config.albert_model
                logger.info(f"Using model directly from config: {model_name}")
                
            # Traiter le nom du modèle
            if model_name:
                model_short_name = model_name.split("/")[-1]
                model_url = f"https://huggingface.co/{model_name}"
        except Exception as e:
            logger.error(f"Error getting model name: {str(e)}")
            # Conserver les valeurs par défaut en cas d'erreur
        
        logger.info(f"Help using model: {model_short_name} ({model_url})")
        
        # Utiliser une version Markdown au lieu de HTML
        # Pour corriger le problème avec send_html
        try:
            # Créer un message au format Markdown
            help_markdown = f"""# 🤖 Albert - Votre assistant IA

Propulsé par **[{model_short_name}]({model_url})**

## Commandes disponibles:
"""
            
            # Ajouter chaque commande à la liste
            for cmd in cmds:
                help_markdown += f"- {cmd}\n"
                
            return help_markdown
            
        except Exception as e:
            logger.error(f"Erreur lors de la génération du message d'aide: {str(e)}")
            return "Erreur lors de la génération du message d'aide. Veuillez réessayer."

    def show_commands(self, config: Config) -> str:
        cmds = self._get_cmds(config)
        return AlbertMsg.commands(cmds)

    def _get_cmds(self, config: Config, verbose: bool = False) -> list[str]:
        cmds = set(
            feature["help"]
            for name, feature in self.function_register.items()
            if name in self.activated_functions
            and feature["help"]
            and (not feature["for_geek"] or verbose)
            and not ("sources" in (feature.get("commands") or []) and config.albert_mode == "norag")
        )
        return sorted(list(cmds))


# Créer l'instance du registre
command_registry = CommandRegistry()

# Ajouter un dictionnaire global pour suivre les messages déjà traités
# Clé: ID de l'événement, Valeur: timestamp + handler qui l'a traité
_processed_events = {}
# Durée de conservation des entrées (en secondes)
_PROCESSED_EVENT_TTL = 60

# Fonction pour nettoyer périodiquement les événements anciens
def _cleanup_processed_events():
    """Nettoie les événements traités qui sont plus anciens que TTL"""
    current_time = time.time()
    to_delete = []
    for event_id, (timestamp, handler, _completed) in _processed_events.items():
        if current_time - timestamp > _PROCESSED_EVENT_TTL:
            to_delete.append(event_id)
    
    for event_id in to_delete:
        _processed_events.pop(event_id, None)

# Fonction pour vérifier si un événement a déjà été traité
def is_event_processed(event_id, handler_name=None):
    """
    Vérifie si un événement a déjà été traité.
    Renvoie (déjà_traité, nom_du_handler, est_terminé)
    """
    _cleanup_processed_events()  # Nettoyage des anciens événements
    
    # Ajout de logs détaillés pour le diagnostic
    logger.debug(f"Vérification si l'événement {event_id} a été traité, demandeur: {handler_name or 'non spécifié'}")
    
    if event_id in _processed_events:
        timestamp, handler, completed = _processed_events[event_id]
        logger.debug(f"Événement {event_id} déjà traité par {handler} (completed={completed}), vérifié par: {handler_name or 'non spécifié'}")
        return True, handler, completed
        
    logger.debug(f"Événement {event_id} NON traité, vérifié par: {handler_name or 'non spécifié'}")
    return False, None, False

# Fonction pour marquer un événement comme traité
def mark_event_processed(event_id, handler_name, command_completed=False):
    """
    Marque un événement comme traité par un handler spécifique
    
    Args:
        event_id: ID de l'événement
        handler_name: Nom du handler qui a traité l'événement
        command_completed: Si True, indique que la commande est terminée
                           et que le message peut être repris par handle_conversation
    """
    # Ajout de logs détaillés pour le diagnostic
    logger.debug(f"Demande de marquage de l'événement {event_id} par {handler_name}, completed={command_completed}")
    
    # Vérifier d'abord si l'événement est déjà marqué
    if event_id in _processed_events:
        timestamp, existing_handler, existing_completed = _processed_events[event_id]
        logger.warning(f"Événement {event_id} déjà marqué par {existing_handler} (completed={existing_completed}), demande de re-marquage par {handler_name} (completed={command_completed})")
        
        # Si c'est le même handler qui essaie de mettre à jour l'état, autoriser
        if existing_handler == handler_name:
            _processed_events[event_id] = (time.time(), handler_name, command_completed)
            logger.debug(f"Mise à jour de l'état de l'événement {event_id} par {handler_name}, completed={command_completed}")
            return
    
    # SOLUTION GÉNÉRIQUE: Pour handle_conversation, ne pas bloquer d'autres traitements
    # mais toujours enregistrer pour le logging
    if handler_name == "handle_conversation" and not command_completed:
        logger.debug(f"Événement {event_id} traité par {handler_name} (sans verrouillage)")
        return
    
    # Enregistrer l'événement comme traité avec le statut approprié
    _processed_events[event_id] = (time.time(), handler_name, command_completed)
    logger.debug(f"Événement {event_id} marqué comme traité par {handler_name}, completed={command_completed}")

# Décorateur pour capturer l'historique des conversations
def capture_conversation_history(func):
    """
    Décorateur pour capturer l'historique des conversations.
    Enregistre les messages de l'utilisateur et les réponses du bot dans l'historique.
    """
    from . import update_conversation_history
    
    @wraps(func)
    async def wrapper(ep: EventParser, matrix_client: MatrixClient):
        # Extraire le message de l'utilisateur
        user_message = None
        try:
            # Essayer d'extraire le texte brut du message
            if isinstance(ep.event, RoomMessageText):
                user_message = ep.event.body
        except Exception as e:
            logger.warning(f"Erreur lors de l'extraction du message utilisateur: {str(e)}")
            
        # Mettre à jour l'historique avec le message de l'utilisateur
        if user_message:
            try:
                await update_conversation_history(
                    matrix_client.config, 
                    ep.room.room_id, 
                    ep.sender, 
                    role="user",
                    user_message=user_message
                )
            except Exception as e:
                logger.warning(f"Erreur lors de la mise à jour de l'historique (message utilisateur): {str(e)}")
                
        # Exécuter la fonction originale pour obtenir la réponse
        response = await func(ep, matrix_client)
        
        # Si la fonction a renvoyé une réponse, l'ajouter à l'historique
        if isinstance(response, str) and response.strip():
            try:
                await update_conversation_history(
                    matrix_client.config, 
                    ep.room.room_id, 
                    ep.sender, 
                    role="assistant",
                    user_message=response
                )
            except Exception as e:
                logger.warning(f"Erreur lors de la mise à jour de l'historique (réponse bot): {str(e)}")
                
        return response
        
    return wrapper


# Décorateur pour enregistrer les commandes
def register_feature(
    group: str,
    onEvent: Event,
    command: str | None = None,
    aliases: list[str] | None = None,
    prefix: str = COMMAND_PREFIX,
    help: str | None = None,
    for_geek: bool = False,
):
    def decorator(func):
        @wraps(func)
        async def wrapper(ep: EventParser, matrix_client: MatrixClient):
            # Utiliser albert_config si disponible, sinon utiliser la config standard
            config = getattr(matrix_client, "albert_config", matrix_client.config)
            room_id = ep.room.room_id
            sender = ep.sender
            event_id = getattr(ep.event, "event_id", None)
            
            # SOLUTION IMMÉDIATE: Nettoyage proactif du contexte pour éviter les blocages
            try:
                # Récupérer le contexte de session actuel pour vérification
                from app.commands import get_unified_session_context, get_context_manager
                session_context = await get_unified_session_context(config, room_id, sender)
                
                # Log de l'état initial du contexte pour diagnostic
                current_state = session_context.conversation_state.copy() if hasattr(session_context, "conversation_state") else {}
                logger.info(f"[SAFETY DEBUG] État du contexte avant {func.__name__}: {current_state}")
                
                # Détection d'un état potentiellement bloquant
                needs_cleanup = False
                if hasattr(session_context, "conversation_state"):
                    # Vérifier les drapeaux potentiellement problématiques
                    if session_context.conversation_state.get("in_command_thread", False):
                        last_command = session_context.conversation_state.get("last_command", "unknown")
                        thread_command = session_context.conversation_state.get("thread_command", "unknown")
                        
                        # Utiliser le timestamp d'activité s'il existe, sinon utiliser le timestamp de démarrage
                        last_activity = session_context.conversation_state.get(
                            "last_activity_time", 
                            session_context.conversation_state.get("thread_start_time", 0)
                        )
                        current_time = time.time()
                        idle_time = current_time - last_activity  # Temps d'inactivité depuis la dernière action
                        
                        # Déterminer le timeout approprié en fonction de la commande
                        timeout_threshold = 300  # 5 minutes par défaut
                        
                        # Si c'est une commande conversationnelle, utiliser son timeout spécifique
                        if thread_command in CONVERSATIONAL_COMMANDS:
                            timeout_threshold = CONVERSATIONAL_COMMANDS.get(thread_command, {}).get("timeout", 300)
                        
                        # Si on tente d'exécuter une commande et qu'un thread est actif, 
                        # ou si le thread est inactif depuis plus longtemps que le timeout, le nettoyer
                        if command or idle_time > timeout_threshold:
                            logger.warning(f"[SAFETY] État potentiellement bloquant détecté: in_command_thread=True, last_command={last_command}, thread_command={thread_command}, inactivité={idle_time:.0f}s")
                            needs_cleanup = True
                
                # Effectuer le nettoyage si nécessaire
                if needs_cleanup:
                    logger.warning(f"[SAFETY] Nettoyage proactif du contexte pour {func.__name__}")
                    
                    # Supprimer les drapeaux liés aux threads
                    if "in_command_thread" in session_context.conversation_state:
                        session_context.conversation_state.pop("in_command_thread")
                    
                    # Marquer explicitement comme terminé
                    session_context.conversation_state["command_completed"] = True
                    
                    # Mise à jour du contexte
                    session_id = f"{room_id}_{sender}"
                    ctx_manager = await get_context_manager(config)
                    await ctx_manager.update_context(
                        session_id, 
                        ContextType.SESSION, 
                        session_context.to_dict()
                    )
                    
                    # Log après nettoyage
                    logger.info(f"[SAFETY] Contexte nettoyé avec succès pour {func.__name__}")
            except Exception as e:
                # Ne pas bloquer l'exécution de la commande si le nettoyage échoue
                logger.error(f"[SAFETY] Erreur lors du nettoyage proactif: {str(e)}")
            
            # Pour handle_conversation, vérifier si l'événement a déjà été traité par un autre handler
            if event_id and func.__name__ == "handle_conversation":
                processed, handler, completed = is_event_processed(event_id)
                
                # Si l'événement a déjà été traité par un autre handler
                if processed and handler != "handle_conversation":
                    # Si ce message a déjà été traité par un autre handler dans le même tour de synchronisation,
                    # handle_conversation ne doit pas le traiter à nouveau, même s'il est marqué comme completed
                    # Cela évite le double traitement d'un même message.
                    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
                    logger.info(f"Message '{message_text}' déjà traité par {handler}, pas de traitement par handle_conversation dans le même tour")
                    return
                    
                    # IMPORTANT: Ne jamais arriver ici - nous ne voulons jamais qu'un message soit traité 
                    # à la fois par un handler de commande ET par handle_conversation dans le même tour
            
            # Pour les handlers de commandes spécifiques, vérifier si nous devons traiter ce message
            elif command:
                # Vérifier si le message est une commande qui nous concerne
                is_our_command = False
                if hasattr(ep.event, 'body'):
                    message_text = ep.event.body.strip()
                    command_parts = message_text.split()
                    if command_parts and command_parts[0] == f"{prefix}{command}":
                        is_our_command = True
                
                # Si ce n'est pas notre commande, vérifier si c'est une réponse à notre thread
                if not is_our_command:
                    # Vérifier si l'utilisateur est dans un thread qui nous concerne
                    is_in_thread, thread_command = await is_in_active_command_thread(
                        ep.room.room_id, ep.sender, config
                    )
                    
                    # Si l'utilisateur est dans un thread mais pas le nôtre, ignorer
                    if is_in_thread and thread_command != command:
                        logger.info(f"Message ignoré par {func.__name__}: utilisateur dans un autre thread '{thread_command}'")
                        return
                        
                    # Si l'utilisateur n'est pas dans un thread et ce n'est pas notre commande, ignorer
                    if not is_in_thread and not is_our_command:
                        return
            
            # Marquer cet événement comme traité par nous
            if event_id:
                # SOLUTION GÉNÉRIQUE: Utiliser le nom de la fonction pour déterminer s'il s'agit
                # d'un handler de réponse (qui peut potentiellement terminer une commande)
                is_response_handler = func.__name__.endswith("_response")
                
                # Pour les handlers de réponse, le marquage est fait par le décorateur thread_response
                # seulement si le thread est effectivement actif
                if not is_response_handler:
                    # Marquer le message comme traité avec le bon statut
                    mark_event_processed(event_id, func.__name__, is_response_handler)
                    logger.debug(f"Message marqué comme traité par {func.__name__} dans register_feature")
            
            # Exécuter la fonction originale
            return await func(ep, matrix_client)

        # Appliquer le décorateur d'historique de conversation à la fonction
        wrapped_func = capture_conversation_history(wrapper)
        
        # Enregistrer la commande dans le registre
        command_registry.add_command(
            name=func.__name__,
            group=group,
            onEvent=onEvent,
            command=command,
            aliases=aliases,
            prefix=prefix,
            help_message=help,
            for_geek=for_geek,
            func=wrapped_func,
        )
        return wrapper

    return decorator


# Décorateur pour vérifier l'autorisation utilisateur
def only_allowed_user(func):
    @wraps(func)
    async def wrapper(ep: EventParser, matrix_client: MatrixClient):
        room_id = ep.room.room_id
        sender = ep.sender
        config = matrix_client.config

        # Vérifier si l'utilisateur est autorisé
        tchap_iam = TchapIam(config)
        is_allowed, message = await tchap_iam.is_user_allowed(config, sender)
        
        if not is_allowed:
            await log_not_allowed(
                message or f"L'utilisateur {sender} n'est pas autorisé",
                ep,
                matrix_client
            )
            return
            
        # L'utilisateur est autorisé, exécuter la fonction
        # Vérifier si func est déjà un coroutine ou une fonction
        if inspect.iscoroutine(func):
            # Si func est un coroutine, on l'attend directement
            logger.debug(f"only_allowed_user: func est un coroutine, attente directe...")
            return await func
        else:
            # Si func est une fonction, on l'appelle
            return await func(ep, matrix_client)
        
    return wrapper


async def log_not_allowed(msg: str, ep: EventParser, matrix_client: MatrixClient):
    """Log un message d'erreur et informe l'utilisateur"""
    logger.warning(msg)
    
    try:
        # Informer l'utilisateur de façon courtoise
        await matrix_client.send_markdown_message(
            ep.room.room_id,
            "🔒 Je suis désolé, mais vous n'avez pas l'autorisation requise pour effectuer cette action.",
            msgtype="m.notice"
        )
    except Exception as e:
        logger.error(f"Erreur lors de l'envoi du message d'erreur: {str(e)}")

# Fonctions utilitaires pour la gestion des threads de commande
# Ces fonctions sont conservées pour compatibilité avec les commandes existantes
# mais devraient progressivement être remplacées par les méthodes de CommandThread

async def is_in_active_command_thread(room_id, user_id, config):
    """
    Vérifie si l'utilisateur est actuellement dans un thread de commande actif.
    
    Args:
        room_id: ID de la salle
        user_id: ID de l'utilisateur
        config: Configuration
        
    Returns:
        tuple: (is_in_thread: bool, thread_command: str)
    """
    from app.commands import get_unified_session_context
    
    try:
        # Récupérer le contexte de session
        session_context = await get_unified_session_context(config, room_id, user_id)
        
        # Vérifier si le contexte contient un état de conversation
        if not hasattr(session_context, "conversation_state"):
            return False, ""
            
        conversation_state = session_context.conversation_state
        
        # Vérifier si le flag in_command_thread est présent et True
        if conversation_state.get("in_command_thread", False):
            thread_command = conversation_state.get("thread_command", "")
            
            # Normaliser certains noms de commandes pour compatibilité
            if thread_command == "handle_attachments_command":
                thread_command = "pj"
            
            # IMPORTANT: Vérifier que la commande est bien dans la liste des commandes avec thread
            if thread_command not in THREAD_COMMANDS:
                logger.info(f"Thread command '{thread_command}' non reconnu comme commande de thread valide (doit être dans {THREAD_COMMANDS})")
                return False, ""
                
            return True, thread_command
        
        # Si on arrive ici, ce n'est pas un thread actif
        return False, ""
        
    except Exception as e:
        logger.warning(f"Erreur lors de la vérification du thread: {str(e)}")
        return False, ""

# Commandes conversationnelles nécessitant des timeouts plus longs
# Ces commandes sont conçues pour avoir des interactions étendues avec l'utilisateur
# Elles bénéficient du système de heartbeat qui étend leur durée de vie tant que l'utilisateur reste actif
CONVERSATIONAL_COMMANDS = {
    "webhook": {
        "timeout": 600,  # 10 minutes en secondes
        "description": "Commandes webhook pour n8n"
    }
    # Ajouter d'autres commandes conversationnelles si nécessaire
}

# Liste des commandes par nom qui utilisent le thread de commande
# Attention: cette liste doit être maintenue manuellement
THREAD_COMMANDS = [
    "pj",
    "add",
    "rmv",
    "sondage",  # Exemple de commande avec thread
    "classer",
    "webhook"
]

# Ajout d'une classe CommandThread qui encapsule le comportement d'un thread de commande
class CommandThread:
    """
    Classe qui encapsule le concept de thread de commande.
    Cette classe fournit une interface simple pour gérer le cycle de vie d'un thread de commande.
    """
    
    @staticmethod
    async def start(room_id: str, user_id: str, command_name: str, config, **thread_data):
        """
        Démarre un thread de commande.
        
        Args:
            room_id: ID du salon
            user_id: ID de l'utilisateur
            command_name: Nom de la commande
            config: Configuration
            **thread_data: Données supplémentaires à stocker dans le thread
        """
        from app.commands import get_context_manager, get_unified_session_context
        
        # Log détaillé pour le débogage
        logger.info(f"[THREAD DEBUG] Démarrage thread commande '{command_name}' avec données: {thread_data}")
        
        try:
            # Récupérer le contexte de session
            session_context = await get_unified_session_context(config, room_id, user_id)
            
            # Mettre à jour l'état de conversation
            if not hasattr(session_context, "conversation_state"):
                session_context.conversation_state = {}
            
            # Log de l'état avant mise à jour
            if hasattr(logger, 'isEnabledFor') and logger.isEnabledFor(10):  # DEBUG = 10
                logger.debug(f"[THREAD DEBUG] État avant mise à jour: {session_context.conversation_state}")
            else:
                logger.info(f"[THREAD DEBUG] État avant mise à jour: {session_context.conversation_state}")
                
            # Marquer le début du thread
            current_time = time.time()
            session_context.conversation_state.update({
                "in_command_thread": True,
                "thread_command": command_name,
                "thread_start_time": current_time,
                "last_activity_time": current_time,  # Ajout du timestamp d'activité initial pour le heartbeat
                "command_completed": False,
                "last_command": command_name,
                # Ajouter les données supplémentaires
                **thread_data
            })
            
            # Log de l'état après mise à jour
            if hasattr(logger, 'isEnabledFor') and logger.isEnabledFor(10):
                logger.debug(f"[THREAD DEBUG] État après mise à jour: {session_context.conversation_state}")
            else:
                logger.info(f"[THREAD DEBUG] État après mise à jour: {session_context.conversation_state}")
            
            # Mise à jour du contexte
            session_id = f"{room_id}_{user_id}"
            ctx_manager = await get_context_manager(config)
            await ctx_manager.update_context(
                session_id, 
                ContextType.SESSION, 
                session_context.to_dict()
            )
            
            logger.info(f"Début du thread de commande '{command_name}' marqué pour {user_id} dans {room_id}")
            
        except Exception as e:
            logger.warning(f"Erreur lors du démarrage du thread: {str(e)}")
    
    @staticmethod
    async def end(room_id: str, user_id: str, command_name: str, config, **final_data):
        """
        Termine un thread de commande.
        
        Cette méthode s'assure que:
        1. Le thread est bien marqué comme terminé
        2. Le flag in_command_thread est correctement supprimé
        3. Le contexte conversationnel est préservé
        
        Args:
            room_id: ID du salon
            user_id: ID de l'utilisateur
            command_name: Nom de la commande
            config: Configuration
            **final_data: Données finales à stocker dans le contexte
        """
        from app.commands import get_context_manager, get_unified_session_context
        
        try:
            # Récupérer le contexte de session
            session_context = await get_unified_session_context(config, room_id, user_id)
            
            # Vérifier si nous sommes bien dans un thread
            if not hasattr(session_context, "conversation_state"):
                logger.warning(f"Tentative de terminer un thread inexistant pour {user_id}")
                return
                
            # Marquer la fin du thread tout en conservant des informations importantes
            current_state = session_context.conversation_state
            thread_command = current_state.get("thread_command", command_name)
            
            # Récupération générique des données importantes
            context_to_preserve = {}
            
            # 1. Préserver le contexte concernant le fichier
            file_info = current_state.get("file_info", {})
            filename = None
            if isinstance(file_info, dict):
                # Essayer les noms de champ courants pour les fichiers
                for field in ["filename", "name", "file_name"]:
                    if field in file_info and file_info[field]:
                        filename = file_info[field]
                        break
            
            # Si on n'a pas trouvé dans file_info, essayer directement dans le state ou final_data
            if not filename:
                for source in [current_state, final_data]:
                    for field in ["file_name", "filename", "name"]:
                        if field in source and source[field]:
                            filename = source[field]
                            break
                    if filename:
                        break
            
            if filename:
                context_to_preserve["last_file_processed"] = filename
            
            # 2. Préserver le contexte concernant les chemins et actions
            target_path = None
            for field in ["target_path", "path", "destination"]:
                if field in final_data and final_data[field]:
                    target_path = final_data[field]
                    break
                if field in current_state and current_state[field]:
                    target_path = current_state[field]
                    break
            
            if target_path:
                context_to_preserve["last_target_path"] = target_path
            
            # 3. Préserver le contexte concernant l'action
            action = None
            for field in ["action", "étape_finale", "status", "operation"]:
                if field in final_data and final_data[field]:
                    action = final_data[field]
                    break
                if field in current_state and current_state[field]:
                    action = current_state[field]
                    break
            
            if action:
                context_to_preserve["last_action"] = action
            
            # 4. Préserver tous les statuts d'erreur ou de succès
            error = None
            for field in ["error", "error_status", "erreur"]:
                if field in final_data and final_data[field]:
                    error = final_data[field]
                    break
                if field in current_state and current_state[field]:
                    error = current_state[field]
                    break
            
            if error:
                context_to_preserve["error_status"] = error
            
            # 5. Déterminer le statut final
            final_status = final_data.get("final_status", "")
            if not final_status:
                if error:
                    final_status = "error"
                elif action in ["classé", "terminé", "success", "succès"]:
                    final_status = "success"
                else:
                    final_status = "completed"
            
            context_to_preserve["final_status"] = final_status
            
            # Log détaillé du contexte préservé
            logger.debug(f"[THREAD END] Contexte préservé : {context_to_preserve}")
            
            # Créer un nouvel état avec les données pertinentes
            new_state = {
                # IMPORTANT: Marquer explicitement comme terminé
                "command_completed": True,
                # Supprimer in_command_thread pour permettre aux futurs messages d'être traités par handle_conversation
                # "in_command_thread": False,  # Ce flag est supprimé, pas juste mis à False
                # Information sur la dernière commande
                "last_command": thread_command,
                "last_command_end_time": time.time(),
                "previous_thread_command": thread_command,
                # Ajouter toutes les données contextuelles préservées
                **context_to_preserve,
                # Ajouter les données finales complètes
                **final_data
            }
            
            # S'assurer que le flag in_command_thread est explicitement supprimé
            if "in_command_thread" in session_context.conversation_state:
                session_context.conversation_state.pop("in_command_thread")
            
            # Mettre à jour l'état
            session_context.conversation_state = new_state
            
            # Log pour confirmer que le thread a bien été terminé
            logger.info(f"[THREAD DEBUG] État après fin de thread: {session_context.conversation_state}")
            
            # Mise à jour du contexte
            session_id = f"{room_id}_{user_id}"
            ctx_manager = await get_context_manager(config)
            await ctx_manager.update_context(
                session_id, 
                ContextType.SESSION, 
                session_context.to_dict()
            )
            
            logger.info(f"Fin du thread de commande '{thread_command}' marquée pour {user_id} dans {room_id}")
            
        except Exception as e:
            logger.warning(f"Erreur lors du marquage de la fin de thread: {str(e)}")
            # Remonter l'exception pour que les appelants puissent la gérer
            raise
    
    @staticmethod
    async def is_active(room_id: str, user_id: str, config) -> Tuple[bool, str]:
        """
        Vérifie si un thread est actif pour un salon/utilisateur.
        
        Args:
            room_id: ID du salon
            user_id: ID de l'utilisateur
            config: Configuration
            
        Returns:
            (bool, str): Tuple indiquant si un thread est actif et son nom
        """
        from app.commands import get_context_manager, get_unified_session_context
        
        try:
            # Récupérer le contexte de session
            session_context = await get_unified_session_context(config, room_id, user_id)
            
            # Logs détaillés pour le diagnostic
            logger.info(f"[THREAD ACTIVE CHECK] Vérification pour room={room_id}, user={user_id}")
            
            # Vérifier si nous sommes dans un thread
            if not hasattr(session_context, "conversation_state"):
                logger.info(f"[THREAD ACTIVE CHECK] Pas de conversation_state")
                return False, ""
                
            # Logs détaillés sur l'état de la conversation
            if hasattr(logger, 'isEnabledFor') and logger.isEnabledFor(logging.INFO):
                state_info = {
                    "in_thread": session_context.conversation_state.get("in_command_thread", False),
                    "thread_cmd": session_context.conversation_state.get("thread_command", ""),
                    "completed": session_context.conversation_state.get("command_completed", False),
                    "action": session_context.conversation_state.get("action", ""),
                    "stage": session_context.conversation_state.get("stage", "")
                }
                logger.info(f"[THREAD ACTIVE CHECK] État: {state_info}")
                
            # Vérifier si le thread est actif
            is_active = session_context.conversation_state.get("in_command_thread", False)
            thread_command = session_context.conversation_state.get("thread_command", "")
            
            # Si le thread est marqué comme terminé, il n'est plus actif
            if session_context.conversation_state.get("command_completed", False):
                logger.info(f"[THREAD ACTIVE CHECK] Thread '{thread_command}' marqué comme terminé")
                return False, ""
                
            logger.info(f"[THREAD ACTIVE CHECK] Résultat: is_active={is_active}, thread_command='{thread_command}'")
            return is_active, thread_command
            
        except Exception as e:
            logger.error(f"[THREAD ACTIVE CHECK] Erreur lors de la vérification du thread: {str(e)}")
            logger.error(traceback.format_exc())
            return False, ""
    
    @staticmethod
    async def update_state(room_id: str, user_id: str, config, **state_data):
        """Met à jour l'état du thread de commande."""
        from app.commands import get_context_manager, get_unified_session_context
        
        try:
            # Récupérer le contexte de session
            session_context = await get_unified_session_context(config, room_id, user_id)
            
            # Vérifier si nous sommes bien dans un thread
            if not hasattr(session_context, "conversation_state"):
                logger.warning(f"Tentative de mettre à jour un thread inexistant pour {user_id}")
                return False
            
            # HEARTBEAT: Mettre à jour le timestamp d'activité
            if session_context.conversation_state.get("in_command_thread", False):
                state_data["last_activity_time"] = time.time()
            
            # Mettre à jour l'état de conversation avec les nouvelles données
            session_context.conversation_state.update(state_data)
            
            # Mise à jour du contexte
            session_id = f"{room_id}_{user_id}"
            ctx_manager = await get_context_manager(config)
            await ctx_manager.update_context(
                session_id, 
                ContextType.SESSION, 
                session_context.to_dict()
            )
            
            # Données mises à jour avec succès
            return True
        except Exception as e:
            logger.warning(f"Erreur lors de la mise à jour de l'état du thread: {str(e)}")
            return False

    @staticmethod
    async def get_data(room_id: str, user_id: str, config) -> Dict[str, Any]:
        """
        Récupère les données du thread actif pour un salon/utilisateur.
        
        Args:
            room_id: ID du salon
            user_id: ID de l'utilisateur
            config: Configuration
            
        Returns:
            Dict[str, Any]: Données du thread ou dictionnaire vide si pas de thread actif
        """
        from app.commands import get_unified_session_context
        
        try:
            # Récupérer le contexte de session
            session_context = await get_unified_session_context(config, room_id, user_id)
            
            # Vérifier si nous sommes dans un thread
            if not hasattr(session_context, "conversation_state"):
                logger.debug(f"[THREAD GET_DATA] Pas de conversation_state")
                return {}
                
            # Vérifier si le thread est actif
            is_active = session_context.conversation_state.get("in_command_thread", False)
            if not is_active:
                logger.debug(f"[THREAD GET_DATA] Aucun thread actif")
                return {}
                
            # Retourner une copie du dictionnaire d'état
            thread_data = dict(session_context.conversation_state)
            logger.debug(f"[THREAD GET_DATA] Données récupérées: {thread_data}")
            return thread_data
            
        except Exception as e:
            logger.error(f"[THREAD GET_DATA] Erreur lors de la récupération des données du thread: {str(e)}")
            return {}

# Décorateurs pour faciliter l'implémentation des commandes avec thread
def threaded_command(command_name: str = None):
    """
    Décorateur qui indique qu'une commande utilise un thread.
    Le décorateur s'occupe automatiquement de créer le thread de commande.
    
    IMPORTANT: Seules les commandes listées dans THREAD_COMMANDS seront effectivement
    traitées comme des threads. Si vous ajoutez une nouvelle commande utilisant
    le système de thread, assurez-vous de l'ajouter à cette liste.
    
    Usage:
    @register_feature(...)
    @threaded_command('ma_commande')
    async def ma_commande(ep, matrix_client):
        # Votre code ici
        # Le thread est déjà créé, vous pouvez vous concentrer sur la logique métier
    
    Args:
        command_name: Nom optionnel de la commande. Si None, le nom est déduit
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(ep: EventParser, matrix_client: MatrixClient):
            # Utiliser albert_config si disponible, sinon utiliser la config standard
            config = getattr(matrix_client, "albert_config", matrix_client.config)
            room_id = ep.room.room_id
            sender = ep.sender
            
            # Déterminer le nom de la commande
            cmd_name = command_name
            if not cmd_name:
                # Rechercher dans le registre
                for name, feature in command_registry.function_register.items():
                    if feature["func"].__wrapped__.__wrapped__ == func:
                        if feature.get("commands") and feature["commands"][0]:
                            cmd_name = feature["commands"][0]
                            break
                        
                # Fallback sur le nom de la fonction
                if not cmd_name:
                    cmd_name = func.__name__
            
            # Vérifier si la commande est dans la liste des commandes avec thread
            if cmd_name not in THREAD_COMMANDS:
                logger.warning(f"ATTENTION: La commande '{cmd_name}' utilise le décorateur @threaded_command mais n'est pas dans THREAD_COMMANDS. Le système de thread peut ne pas fonctionner correctement.")
            
            # IMPORTANT: Vérifier si un thread existe déjà et le terminer
            # pour éviter les conflits entre commandes successives
            is_in_thread, thread_command = await CommandThread.is_active(room_id, sender, config)
            
            # Si on est déjà dans un thread, le terminer proprement
            if is_in_thread and thread_command:
                logger.info(f"Thread existant '{thread_command}' détecté pour {sender} dans {room_id}, terminaison automatique")
                try:
                    # Terminer le thread proprement
                    await CommandThread.end(
                        room_id, 
                        sender, 
                        thread_command, 
                        config,
                        ended_by="new_command",
                        new_command=cmd_name
                    )
                except Exception as e:
                    logger.warning(f"Erreur lors de la terminaison du thread existant: {str(e)}")
            
            # Démarrer le thread pour cette nouvelle commande
            try:
                await CommandThread.start(
                    room_id, 
                    sender, 
                    cmd_name, 
                    config,
                    command_type=cmd_name,
                    command_context="command"
                )
            except Exception as e:
                logger.warning(f"Erreur lors du démarrage du thread: {str(e)}")
            
            # Exécuter la fonction originale
            # Vérifier si func est déjà un coroutine ou une fonction
            if inspect.iscoroutine(func):
                # Si func est un coroutine, on l'attend directement
                logger.debug(f"threaded_command: func est un coroutine, attente directe...")
                return await func
            else:
                # Si func est une fonction, on l'appelle
                return await func(ep, matrix_client)
        
        return wrapper
    return decorator

def thread_response(command_name: str = None, validate_format=None, timeout: Optional[float] = 60.0, expected_format_description: str = None, required_services: Optional[List[str]] = None):
    """
    Décorateur qui indique qu'une fonction gère les réponses à une commande threadée.
    
    IMPORTANT: Seules les commandes listées dans THREAD_COMMANDS seront effectivement
    traitées comme des threads. Si vous ajoutez une nouvelle commande utilisant
    le système de thread, assurez-vous de l'ajouter à cette liste.
    
    Usage:
    @register_feature(...)
    @thread_response('ma_commande')
    async def handler_reponse(ep, matrix_client):
        # Votre code ici
        # Le décorateur vérifie automatiquement si l'utilisateur est dans un thread actif
    
    Args:
        command_name: Nom de la commande à laquelle cette fonction répond
        validate_format: Fonction de validation du format de la réponse (facultatif)
        timeout: Timeout en secondes pour l'exécution de la fonction (None = pas de timeout)
        expected_format_description: Description du format attendu (utilisé dans le message d'erreur)
        required_services: Liste des services requis (webdav_service, index_service, context_manager, behavior_manager)
    """
    logger.info(f"[THREAD RESPONSE DECORATOR] Déclaration d'un handler pour command_name='{command_name}'")
    
    def decorator(func):
        @wraps(func)
        async def wrapper(ep: EventParser, matrix_client: MatrixClient):
            config = getattr(matrix_client, "albert_config", matrix_client.config)
            room_id = ep.room.room_id
            sender = ep.sender
            event_id = getattr(ep.event, "event_id", None)
            
            # Vérifier si la fonction a un timeout spécifique défini
            fn_timeout = getattr(func, "response_timeout", timeout)
            
            # Log pour indiquer le démarrage avec ou sans timeout
            if fn_timeout is None:
                logger.info(f"[THREAD RESPONSE] Démarrage du handler {func.__name__} pour {command_name} (sans timeout)")
            else:
                logger.info(f"[TIMEOUT DEBUG] Démarrage de la commande {func.__name__} avec timeout de {fn_timeout}s")
            
            # Extraire le texte du message
            message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
            
            # Détecter si c'est un message simple comme "merci", "ok", etc.
            is_simple_message = False
            simple_message_patterns = [
                r"^(merci|ok|d'accord|très bien|bien reçu|parfait)[\s!.]*$",
                r"^(thanks|thank you|thx|got it|received)[\s!.]*$"
            ]
            for pattern in simple_message_patterns:
                if re.match(pattern, message_text.lower()):
                    is_simple_message = True
                    logger.debug(f"Message simple détecté dans thread_response: '{message_text}'")
                    break
            
            # Vérifier si l'utilisateur est dans un thread actif pour cette commande
            is_in_thread, thread_command = await CommandThread.is_active(room_id, sender, config)
            
            # Log détaillé pour le diagnostic du thread
            logger.info(f"[THREAD DIAGNOSTIC] Message '{message_text}' - is_in_thread={is_in_thread}, thread_command={thread_command}, command_name={command_name}, handler={func.__name__}")
            
            # Si c'est un message simple et que le thread n'est plus actif, 
            # ne pas le traiter ici mais laisser handle_conversation le gérer
            if is_simple_message and not is_in_thread:
                logger.info(f"Message simple '{message_text}' détecté après thread terminé, laissé à handle_conversation")
                return None
            
            # Si l'utilisateur n'est pas dans un thread actif, ignorer
            if not is_in_thread:
                logger.info(f"Message ignoré par {func.__name__}: utilisateur pas dans un thread actif")
                return None
            
            # Si c'est un thread pour une autre commande, ignorer
            if command_name and thread_command != command_name:
                logger.info(f"Message ignoré par {func.__name__}: thread pour '{thread_command}', pas pour '{command_name}'")
                return None
                
            # IMPORTANT: Débug supplémentaire
            logger.info(f"[THREAD RESPONSE MATCH] CORRESPONDANCE TROUVÉE pour le thread '{thread_command}' avec le handler '{func.__name__}' (command_name='{command_name}')")
            
            # HEARTBEAT: Mettre à jour le timestamp d'activité du thread
            try:
                # Récupérer le contexte de session
                from app.commands import get_context_manager, get_unified_session_context
                session_context = await get_unified_session_context(config, room_id, sender)
                
                if hasattr(session_context, "conversation_state") and session_context.conversation_state.get("in_command_thread"):
                    # Mettre à jour le timestamp d'activité
                    old_timestamp = session_context.conversation_state.get("last_activity_time", 0)
                    session_context.conversation_state["last_activity_time"] = time.time()
                    
                    # Enregistrer la mise à jour
                    session_id = f"{room_id}_{sender}"
                    ctx_manager = await get_context_manager(config)
                    await ctx_manager.update_context(
                        session_id, 
                        ContextType.SESSION, 
                        session_context.to_dict()
                    )
                    logger.info(f"[HEARTBEAT] Timestamp d'activité mis à jour pour thread '{thread_command}': {old_timestamp} -> {session_context.conversation_state['last_activity_time']}")
                else:
                    logger.info(f"[HEARTBEAT] Impossible de mettre à jour le timestamp: conversation_state manquant ou in_command_thread=False")
            except Exception as e:
                # Ne pas bloquer l'exécution en cas d'erreur du heartbeat
                logger.error(f"[HEARTBEAT] Erreur lors de la mise à jour du timestamp d'activité: {str(e)}")
            
            # Indiquer que nous traitons cette réponse
            logger.info(f"[THREAD RESPONSE] Traitement de la réponse '{message_text}' pour le thread '{thread_command}' par {func.__name__}")
            
            # Si validate_format est fourni, vérifier le format de la réponse
            if validate_format and not validate_format(message_text):
                logger.info(f"Format invalide pour la réponse: '{message_text}'")
                error_msg = "⚠️ Format de réponse invalide. Veuillez réessayer avec un format correct."
                
                # Si une description du format attendu est fournie, l'inclure dans le message d'erreur
                if expected_format_description:
                    error_msg += f"\n\n{expected_format_description}"
                
                return error_msg
            
            # Si nous arrivons ici, nous allons effectivement traiter le message
            if event_id:
                # Marquer le message comme traité par ce handler de réponse
                mark_event_processed(event_id, func.__name__, False)  # False pour permettre à handle_conversation de le reprendre
                logger.debug(f"Message marqué comme traité par {func.__name__} (thread actif pour {thread_command})")
            
            # Exécuter la fonction avec ou sans timeout
            try:
                if fn_timeout is not None:
                    # Exécuter avec timeout
                    # Vérifier si func est déjà un coroutine
                    if inspect.iscoroutine(func):
                        # Si func est un coroutine, l'attendre directement avec timeout
                        logger.debug(f"thread_response: func est un coroutine, attente directe avec timeout...")
                        try:
                            result = await asyncio.wait_for(func, timeout=fn_timeout)
                        except asyncio.TimeoutError:
                            logger.error(f"Timeout dépassé pour {func.__name__} après {fn_timeout}s")
                            return f"⚠️ Le traitement de votre réponse a pris trop de temps (> {fn_timeout}s). Veuillez réessayer."
                    else:
                        # Si func est une fonction, créer une tâche et l'attendre avec timeout
                        task = asyncio.create_task(func(ep, matrix_client))
                        try:
                            result = await asyncio.wait_for(task, timeout=fn_timeout)
                        except asyncio.TimeoutError:
                            logger.error(f"Timeout dépassé pour {func.__name__} après {fn_timeout}s")
                            return f"⚠️ Le traitement de votre réponse a pris trop de temps (> {fn_timeout}s). Veuillez réessayer."
                else:
                    # Exécuter sans timeout
                    # Vérifier si func est déjà un coroutine
                    if inspect.iscoroutine(func):
                        # Si func est un coroutine, l'attendre directement
                        logger.debug(f"thread_response: func est un coroutine, attente directe...")
                        result = await func
                    else:
                        # Si func est une fonction, l'appeler normalement
                        result = await func(ep, matrix_client)
                
                # Terminer avec succès
                if fn_timeout is None:
                    logger.info(f"Traitement terminé avec succès pour {func.__name__}")
                else:
                    logger.info(f"[TIMEOUT DEBUG] Commande {func.__name__} terminée avec succès dans le délai imparti")
            except Exception as e:
                logger.error(f"Erreur dans {func.__name__}: {str(e)}")
                logger.error(traceback.format_exc())
                # En cas d'erreur, on retourne quand même un résultat pour ne pas bloquer
                result = f"⚠️ Une erreur est survenue: {str(e)}"
            
            # Si la fonction renvoie None explicitement, c'est qu'elle ne veut pas bloquer
            # le traitement par d'autres handlers (comme handle_conversation)
            if result is None:
                logger.info(f"Handler {func.__name__} a retourné None, conversation pourra reprendre le message")
                if event_id:
                    # Corriger la marque du message pour permettre à handle_conversation de le reprendre
                    mark_event_processed(event_id, func.__name__, True)
                    
            # Injecter les services si nécessaires
            if required_services:
                try:
                    # Utiliser la version de inject_services de decorators.py
                    from app.commands.decorators import inject_services
                    
                    # Créer un dictionnaire des services disponibles
                    services_dict = {
                        service_name: getattr(matrix_client, service_name) 
                        for service_name in required_services
                        if hasattr(matrix_client, service_name)
                    }
                    
                    # Cas spécial pour handle_conversation
                    if isinstance(result, type(None)) and hasattr(func, "__name__") and func.__name__ == "handle_conversation":
                        logger.debug("Cas spécial détecté: handle_conversation avec result=None")
                        # Dans ce cas, on appelle directement handle_conversation avec les arguments ep et matrix_client
                        # au lieu d'essayer d'injecter des services dans None
                        from app.commands.conversation import handle_conversation
                        result = await handle_conversation(ep, matrix_client)
                    else:
                        # Passer explicitement les arguments ep et matrix_client
                        if inspect.iscoroutine(result):
                            # Cas spécial pour les coroutines: les attendre d'abord
                            logger.debug(f"Le résultat est une coroutine, attente...")
                            result = await result
                            logger.debug(f"Coroutine complétée, résultat obtenu")
                            
                        # Appliquer les services au résultat ou à la fonction
                        logger.debug(f"Injection des services: {', '.join(services_dict.keys())}")
                        result = await inject_services(result, ep=ep, matrix_client=matrix_client, **services_dict)
                except Exception as e:
                    # Log et propagation de l'erreur
                    logger.error(f"Erreur lors de l'injection des services: {str(e)}")
                    # Si on a une exception de type AttributeError ou TypeError, c'est probablement lié à la signature de la fonction
                    if isinstance(e, (AttributeError, TypeError)):
                        logger.error(f"Détails complets de l'erreur: {traceback.format_exc()}")
                    raise e
            
            return result
        
        # Ajouter des métadonnées pour permettre l'introspection
        wrapper.is_thread_response = True
        wrapper.thread_command = command_name
        wrapper.validate_format = validate_format is not None
        wrapper.response_timeout = timeout
        wrapper.expected_format_description = expected_format_description
        
        return wrapper
    
    return decorator 