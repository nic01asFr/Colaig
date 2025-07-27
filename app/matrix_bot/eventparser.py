# SPDX-FileCopyrightText: 2021 - 2022 Isaac Beverly <https://github.com/imbev>
# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT
from dataclasses import dataclass
from typing import Optional

from nio import Event, MatrixRoom, RoomMessageText

from .client import MatrixClient
from .config import logger
from .room_utils import room_is_direct_message


class EventNotConcerned(Exception):
    """Exception to say that the current event is not concerned by this parser"""


@dataclass
class EventParser:
    """
    Parse the current event for the callbacks.
    Many useful methods that raises a EventNotConcerned when the action do not concern the current event
    """

    room: MatrixRoom
    event: Event
    matrix_client: MatrixClient
    log_usage: bool = False

    @property
    def sender(self) -> str:
        return self.event.sender

    def is_from_userid(self, userid: str) -> bool:
        return self.sender_id() == userid

    def is_from_this_bot(self) -> bool:
        return self.is_from_userid(self.matrix_client.user_id)

    def room_is_direct_message(self) -> bool:
        return room_is_direct_message(self.room)

    def sender_id(self) -> str:
        return self.event.sender

    def sender_username(self) -> str:
        return self.room.users[self.event.sender].name

    def do_not_accept_own_message(self) -> None:
        """
        :raise EventNotConcerned: if the message is written by the bot.
        """
        if self.is_from_this_bot():
            raise EventNotConcerned

    def only_on_direct_message(self) -> None:
        """
        :raise EventNotConcerned: if the room is a not a direct message room.
        """
        if not self.room_is_direct_message():
            raise EventNotConcerned

    def only_on_salons(self) -> None:
        """
        :raise EventNotConcerned: if the room is a direct message room.
        """
        if self.room_is_direct_message():
            raise EventNotConcerned

    def only_on_join(self) -> None:
        """
        :raise EventNotConcerned: if the event is not a join event (the bot has been invited)
        """
        if not self.event.source.get("content", {}).get("membership") == "invite":
            raise EventNotConcerned

    def is_command(self, *args) -> bool:
        return False

    async def get_tchap_context(self) -> 'TchapContext':
        """Obtient le contexte Tchap pour cet événement"""
        from app.services.tchap_context_resolver import TchapContextResolver
        resolver = TchapContextResolver(self.matrix_client)
        return await resolver.resolve_context(self)

    async def should_respond_in_context(self) -> bool:
        """Détermine si le bot devrait répondre dans ce contexte"""
        context = await self.get_tchap_context()
        return context.should_respond

    async def get_response_thread_id(self) -> Optional[str]:
        """Détermine l'ID du thread pour la réponse"""
        context = await self.get_tchap_context()
        return context.response_thread_id


class MessageEventParser(EventParser):
    event: RoomMessageText
    command: list[str] | None = None

    def parse_command(self, commands: str | list[str], prefix: str, command_name: str = ""):
        """
        if the event is concerned by the command, returns the command line as a list.
        Raise EventNotConcerned otherwise.

        :param command: the command that is to be recognized.
        :param prefix: the prefix for this command (default is !).
        :param command_name: name(s) of the command, for logging purposes.
        :return: the text after the command
        :raise EventNotConcerned: if the current event is not concerned by the command.
        """
        commands = [commands] if isinstance(commands, str) else commands
        body = self.event.body.strip()
        user_command = body.split()
        
        if not user_command:
            raise EventNotConcerned
            
        # Vérification stricte de la commande
        command_found = False
        matching_command = None
        
        for c in commands:
            cmd_with_prefix = f"{prefix}{c}"
            if cmd_with_prefix == user_command[0]:
                command_found = True
                matching_command = c
                break
                
        if not command_found:
            logger.debug(f"Commande non reconnue dans parse_command: {user_command[0]}")
            raise EventNotConcerned

        # Construire la liste de commande avec la commande exacte qui a été trouvée
        command = [matching_command] + user_command[1:]

        if self.log_usage:
            logger.info(
                f"Handling command: {command_name or matching_command}, payload: {command[1:] if len(command) > 1 else 'none'}"
            )

        self.command = command

    def is_command(self, prefix: str) -> bool:
        text = self.event.body.strip()
        return text.startswith(prefix) and len(text) > 1

    def get_command(self) -> list[str] | None:
        return self.command

