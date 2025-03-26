from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, field
from functools import wraps
from enum import Enum

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventNotConcerned, EventParser
from nio import Event, RoomEncryptedFile, RoomMemberEvent, RoomMessageText

from app.bot_msg import AlbertMsg
from app.config import COMMAND_PREFIX, Config
from app.iam import TchapIam

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
        commands = [command] if command else None
        if aliases:
            commands += aliases

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
            and not ("sources" in feature.get("commands") and config.albert_mode == "norag")
        )
        return sorted(list(cmds))


# Créer l'instance du registre
command_registry = CommandRegistry()


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
            return await func(ep, matrix_client)

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
            func=wrapper,
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