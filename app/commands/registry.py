from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, field
from functools import wraps
from enum import Enum
import time
import re

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventNotConcerned, EventParser
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
    
    if event_id in _processed_events:
        timestamp, handler, completed = _processed_events[event_id]
        return True, handler, completed
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
    # AMÉLIORATION: Ne pas marquer comme traité les messages simples dans handle_conversation
    # pour éviter qu'ils ne soient ignorés par d'autres gestionnaires
    if handler_name == "handle_conversation" and not command_completed:
        # Enregistrer uniquement pour le logging mais sans bloquer d'autres traitements
        logger.debug(f"Événement {event_id} traité par {handler_name} (sans verrouillage)")
        return
        
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
                    bot_response=response
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
            event_id = getattr(ep.event, "event_id", None)
            
            # Pour handle_conversation, vérifier si l'utilisateur est dans un thread de commande
            if event_id and func.__name__ == "handle_conversation":
                processed, handler, completed = is_event_processed(event_id)
                
                # Si l'événement a déjà été traité
                if processed:
                    # Mais s'il est marqué comme terminé, permettre la reprise de la conversation
                    if completed:
                        logger.info(f"Événement {event_id} traité par {handler} et marqué comme terminé, reprise par handle_conversation")
                        # On continue l'exécution pour handle_conversation
                    # Pour les messages simples, autoriser handle_conversation
                    elif hasattr(ep.event, 'body') and ep.event.body.strip() and not ep.event.body.strip().startswith(COMMAND_PREFIX):
                        # Si c'est un message simple (pas une commande), permettre le traitement
                        message_text = ep.event.body.strip()
                        logger.info(f"Message simple détecté: '{message_text}', autorisation du traitement par handle_conversation")
                    else:
                        logger.info(f"Événement {event_id} déjà traité par {handler}, ignoré par handle_conversation")
                        return
            
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
                # Déterminer si notre handler est un handler qui termine une commande
                completes_command = False
                if func.__name__ in ["handle_attachments_response", "handle_docquery_response"]:
                    # Vérifier si le message contient une indication de complétion
                    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
                    if re.match(r"^\s*[1-3]\s*$", message_text):
                        completes_command = True
                
                mark_event_processed(event_id, func.__name__, completes_command)
            
            # Si nous démarrons une nouvelle commande, marquer le début du thread
            try:
                if command and hasattr(ep.event, 'body'):
                    message_text = ep.event.body.strip()
                    command_parts = message_text.split()
                    if command_parts and command_parts[0] == f"{prefix}{command}":
                        logger.info(f"Démarrage d'un thread de commande '{command}'")
                        await mark_command_thread_start(
                            ep.room.room_id, 
                            ep.sender, 
                            command, 
                            config,
                            command_type=command,
                            command_context="command"
                        )
            except Exception as e:
                logger.warning(f"Erreur lors du marquage du début de thread: {str(e)}")

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
async def mark_command_thread_start(room_id, user_id, command_name, config, **thread_data):
    """
    Marque explicitement le début d'un thread de commande.
    
    Args:
        room_id: ID du salon
        user_id: ID de l'utilisateur
        command_name: Nom de la commande (ex: 'pj', 'docquery')
        config: Configuration (pour accéder au gestionnaire de contexte)
        **thread_data: Données supplémentaires du thread (action, fichier, etc.)
    """
    from app.commands import get_context_manager, get_unified_session_context
    
    try:
        # Récupérer le contexte de session
        session_context = await get_unified_session_context(config, room_id, user_id)
        
        # Mettre à jour l'état de conversation
        if not hasattr(session_context, "conversation_state"):
            session_context.conversation_state = {}
            
        # Marquer le début du thread
        session_context.conversation_state.update({
            "in_command_thread": True,
            "thread_command": command_name,
            "thread_start_time": time.time(),
            "command_completed": False,
            "last_command": command_name,
            # Ajouter les données supplémentaires
            **thread_data
        })
        
        # Mise à jour du contexte
        session_id = f"{room_id}_{user_id}"
        ctx_manager = get_context_manager(config)
        await ctx_manager.update_context(
            session_id, 
            ContextType.SESSION, 
            session_context.to_dict()
        )
        
        logger.info(f"Début du thread de commande '{command_name}' marqué pour {user_id} dans {room_id}")
        
    except Exception as e:
        logger.warning(f"Erreur lors du marquage du début de thread: {str(e)}")

async def mark_command_thread_end(room_id, user_id, command_name, config, **final_data):
    """
    Marque explicitement la fin d'un thread de commande.
    
    Args:
        room_id: ID du salon
        user_id: ID de l'utilisateur
        command_name: Nom de la commande (ex: 'pj', 'docquery')
        config: Configuration (pour accéder au gestionnaire de contexte)
        **final_data: Données finales à conserver (résultat, fichier traité, etc.)
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
        
        # Créer un nouvel état avec uniquement les données pertinentes
        session_context.conversation_state = {
            "command_completed": True,
            "last_command": thread_command,
            "last_command_end_time": time.time(),
            "previous_thread_command": thread_command,
            # Conserver certaines données du thread
            "last_file_processed": current_state.get("file_name") or current_state.get("last_file_processed", ""),
            "last_action": current_state.get("action") or current_state.get("current_action", ""),
            "last_target_path": current_state.get("target_path") or current_state.get("last_target_path", ""),
            # Ajouter les données finales
            **final_data
        }
        
        # Mise à jour du contexte
        session_id = f"{room_id}_{user_id}"
        ctx_manager = get_context_manager(config)
        await ctx_manager.update_context(
            session_id, 
            ContextType.SESSION, 
            session_context.to_dict()
        )
        
        logger.info(f"Fin du thread de commande '{thread_command}' marquée pour {user_id} dans {room_id}")
        
    except Exception as e:
        logger.warning(f"Erreur lors du marquage de la fin de thread: {str(e)}")

async def is_in_active_command_thread(room_id, user_id, config):
    """
    Vérifie si l'utilisateur est actuellement dans un thread de commande actif.
    
    Args:
        room_id: ID du salon
        user_id: ID de l'utilisateur
        config: Configuration (pour accéder au gestionnaire de contexte)
        
    Returns:
        (bool, str): Tuple contenant (est_dans_thread, nom_commande)
    """
    from app.commands import get_context_manager, get_unified_session_context
    
    try:
        # Récupérer le contexte de session
        session_context = await get_unified_session_context(config, room_id, user_id)
        
        # Vérifier l'état de conversation
        if not hasattr(session_context, "conversation_state"):
            return False, ""
            
        conversation_state = session_context.conversation_state
        
        # Si la commande est explicitement marquée comme terminée, nous ne sommes plus dans un thread actif
        if conversation_state.get("command_completed", False):
            return False, conversation_state.get("last_command", "")
            
        # Vérifier les indicateurs explicites de thread
        if conversation_state.get("in_command_thread", False):
            return True, conversation_state.get("thread_command", "")
            
        # Vérification des états connus qui indiquent un thread actif
        if any([
            conversation_state.get("waiting_for_alternative", False),
            conversation_state.get("waiting_for_path", False),
            conversation_state.get("command_context") == "document",
            conversation_state.get("command_type") in ["pj", "docquery", "sources"],
            conversation_state.get("current_action") in ["classify", "query", "upload", "browse"],
            conversation_state.get("action") in ["classify", "query", "upload", "browse"]
        ]):
            command = (conversation_state.get("thread_command") or 
                     conversation_state.get("last_command") or 
                     "unknown")
            return True, command
            
        return False, ""
        
    except Exception as e:
        logger.warning(f"Erreur lors de la vérification du thread: {str(e)}")
        return False, "" 