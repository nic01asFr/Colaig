# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

from typing import Optional
from io import BytesIO

from app.matrix_bot.eventparser import EventParser
from nio import Event, MatrixRoom, MessageDirection
from nio.crypto.attachments import decrypt_attachment

from app.bot_msg import AlbertMsg
from app.config import Config


def has_keys_along(nested_dict: dict, keys: list[str]) -> bool:
    current_level = nested_dict
    for key in keys:
        if isinstance(current_level, dict) and key in current_level:
            current_level = current_level[key]
        else:
            return False
    return True


def isa_reply_to(event) -> bool:
    return has_keys_along(event.source, ["content", "m.relates_to", "m.in_reply_to", "event_id"])


#
# Message management
#


async def get_thread_messages(
    config: Config, ep: EventParser, max_rewind: int = 100
) -> list[Event]:
    matrix_client = ep.matrix_client
    event = ep.event

    # Build the conversation thread
    messages: list = []
    i = 0
    while isa_reply_to(event) and i < max_rewind:
        messages.insert(0, event)
        previous_event_id = event.source["content"]["m.relates_to"]["m.in_reply_to"]["event_id"]
        previous = await matrix_client.room_get_event(ep.room.room_id, previous_event_id)
        event = previous.event
        i += 1

    # Insert the last non original poster message
    if not isa_reply_to(event) and i < max_rewind:
        messages.insert(0, event)

    return messages


async def get_previous_messages(
    config: Config, ep: EventParser, history_lookup: int = 10, max_rewind: int = 100
) -> list[Event]:
    matrix_client = ep.matrix_client
    # Build the conversation history
    starttoken = matrix_client.next_batch
    roommessages = await matrix_client.room_messages(
        ep.room.room_id,
        starttoken,
        limit=min(config.albert_history_lookup, config.albert_max_rewind),
        direction=MessageDirection.back,
        message_filter={"types": ["m.room.message", "m.room.encrypted"]},
    )
    messages: list = []
    decr = 0
    for i, event in enumerate(roommessages.chunk):
        body = event.source["content"]["body"].strip()
        # Or only accept "mesgtype" == m.text ?
        if (
            isa_reply_to(event)
            or event.source["content"]["msgtype"] in ["m.notice"]
            or any(body.startswith(msg) for msg in AlbertMsg.common_msg_prefixes)
        ):
            decr += 1
            continue
        messages.insert(0, event)
        if i - decr >= min(history_lookup, max_rewind):
            break

    return messages


def get_cleanup_body(event: Event) -> str:
    body = event.source["content"]["body"].strip()

    # Remove quoted text in reply to avoid unnecesserilly text
    if body.startswith("> <@"):
        line_start = 0
        lines = body.split("\n")
        for line in lines:
            if line.startswith("> "):
                line_start += 1
            else:
                break
        body = "\n".join(lines[line_start:])

    return body.strip()


async def get_decrypted_file(ep: EventParser) -> BytesIO:
    response = await ep.matrix_client.download(ep.event.url)
    content = decrypt_attachment(
        response.body, 
        ep.event.key.get('k'), 
        ep.event.hashes['sha256'], 
        ep.event.iv
    )
    file = BytesIO(content)
    file.name = ep.event.source['content']['body']
    file.type = ep.event.source['content']['info']['mimetype']
    return file


async def get_decrypted_file_from_parser(ep: EventParser) -> BytesIO:
    """
    Récupère et déchiffre une pièce jointe à partir d'un EventParser.
    Version originale maintenue pour compatibilité.
    
    Args:
        ep: L'EventParser contenant l'événement avec le fichier
    
    Returns:
        Le contenu du fichier déchiffré sous forme de BytesIO
    """
    response = await ep.matrix_client.download(ep.event.url)
    content = decrypt_attachment(
        response.body, 
        ep.event.key.get('k'), 
        ep.event.hashes['sha256'], 
        ep.event.iv
    )
    file = BytesIO(content)
    file.name = ep.event.source['content']['body']
    file.type = ep.event.source['content']['info']['mimetype']
    return file


async def get_decrypted_file(event, matrix_client) -> bytes:
    """
    Récupère et déchiffre une pièce jointe à partir d'un événement Matrix.
    
    Args:
        event: L'événement Matrix contenant le fichier (RoomMessageFile ou RoomEncryptedFile)
        matrix_client: Le client Matrix pour télécharger le fichier
    
    Returns:
        Le contenu du fichier déchiffré sous forme de bytes
    """
    # Vérifier si c'est un EventParser ou un Event directement
    if hasattr(event, 'matrix_client') and hasattr(event, 'event'):
        # C'est un EventParser
        ep = event
        event = ep.event
        matrix_client = ep.matrix_client
    
    # Ajouter des logs pour le débogage
    from app.matrix_bot.config import logger
    logger.info(f"[GET_DECRYPTED_FILE] Type de l'événement: {type(event).__name__}")
    
    # S'assurer que nous avons une URL pour télécharger le fichier
    url = None
    
    # Cas 1: URL directement sur l'objet event
    if hasattr(event, 'url') and event.url:
        url = event.url
        logger.info(f"[GET_DECRYPTED_FILE] URL trouvée sur l'objet event: {url}")
    
    # Cas 2: URL dans source.content
    elif hasattr(event, 'source') and isinstance(event.source, dict) and 'content' in event.source:
        content = event.source['content']
        
        # Cas 2.1: URL directe dans content
        if 'url' in content:
            url = content['url']
            logger.info(f"[GET_DECRYPTED_FILE] URL trouvée dans content: {url}")
        
        # Cas 2.2: URL dans file.url (fichier chiffré)
        elif 'file' in content and isinstance(content['file'], dict) and 'url' in content['file']:
            url = content['file']['url']
            logger.info(f"[GET_DECRYPTED_FILE] URL trouvée dans file.url: {url}")
        
        # Cas 2.3: Essayer de trouver l'URL dans des structures plus profondes
        elif 'info' in content and isinstance(content['info'], dict):
            if 'url' in content['info']:
                url = content['info']['url']
                logger.info(f"[GET_DECRYPTED_FILE] URL trouvée dans info.url: {url}")
    
    # Si aucune URL n'a été trouvée, lever une exception
    if not url:
        logger.error(f"[GET_DECRYPTED_FILE] Impossible de trouver l'URL du fichier dans l'événement")
        raise ValueError("Impossible de trouver l'URL du fichier dans l'événement. Structure de l'événement non reconnue.")
    
    try:
        # Télécharger le fichier
        logger.info(f"[GET_DECRYPTED_FILE] Téléchargement du fichier depuis: {url}")
        response = await matrix_client.download(url)
        
        if not response or not hasattr(response, 'body'):
            logger.error(f"[GET_DECRYPTED_FILE] Réponse de téléchargement invalide")
            raise ValueError("Erreur lors du téléchargement du fichier: réponse invalide")
        
        logger.info(f"[GET_DECRYPTED_FILE] Fichier téléchargé avec succès, taille: {len(response.body)} octets")
        
        # Vérifier si le fichier est chiffré et doit être déchiffré
        # Cas 1: Attributs de chiffrement directement sur l'objet event
        if hasattr(event, 'key') and hasattr(event, 'iv') and hasattr(event, 'hashes'):
            logger.info(f"[GET_DECRYPTED_FILE] Déchiffrement avec attributs de l'objet event")
            try:
                content = decrypt_attachment(
                    response.body, 
                    event.key.get('k'), 
                    event.hashes['sha256'], 
                    event.iv
                )
                logger.info(f"[GET_DECRYPTED_FILE] Fichier déchiffré avec succès, taille: {len(content)} octets")
            except Exception as e:
                logger.error(f"[GET_DECRYPTED_FILE] Erreur lors du déchiffrement (méthode 1): {str(e)}")
                # Retourner le contenu non déchiffré en cas d'échec
                content = response.body
        
        # Cas 2: Informations de chiffrement dans source.content.file
        elif (hasattr(event, 'source') and 'content' in event.source 
              and 'file' in event.source['content'] and isinstance(event.source['content']['file'], dict)):
            file_info = event.source['content']['file']
            if 'key' in file_info and 'iv' in file_info and 'hashes' in file_info:
                logger.info(f"[GET_DECRYPTED_FILE] Déchiffrement avec informations dans source.content.file")
                try:
                    content = decrypt_attachment(
                        response.body,
                        file_info['key'].get('k'),
                        file_info['hashes'].get('sha256'),
                        file_info['iv']
                    )
                    logger.info(f"[GET_DECRYPTED_FILE] Fichier déchiffré avec succès, taille: {len(content)} octets")
                except Exception as e:
                    logger.error(f"[GET_DECRYPTED_FILE] Erreur lors du déchiffrement (méthode 2): {str(e)}")
                    # Retourner le contenu non déchiffré en cas d'échec
                    content = response.body
            else:
                # Fichier non chiffré ou format non reconnu
                logger.info(f"[GET_DECRYPTED_FILE] Fichier considéré comme non chiffré (méthode 2)")
                content = response.body
        else:
            # Fichier non chiffré
            logger.info(f"[GET_DECRYPTED_FILE] Fichier considéré comme non chiffré")
            content = response.body
        
        return content
        
    except Exception as e:
        logger.error(f"[GET_DECRYPTED_FILE] Erreur lors du traitement du fichier: {str(e)}")
        raise ValueError(f"Erreur lors du téléchargement ou du déchiffrement du fichier: {str(e)}")

#
# User management
#

default_power_to_title = {
    0: "utilisateur",
    50: "modérateur",
    100: "administrateur",
}


def user_name_to_non_hl_user(complete_user_name: str) -> str:
    """get the string of the user"""
    return complete_user_name.split("[")[0].strip()


def get_user_to_power_level(salon: MatrixRoom) -> dict:
    users = {user_id: user.name for user_id, user in salon.users.items()}
    return {
        user_name_to_non_hl_user(user_name): salon.power_levels.users.get(user_id, 0)
        for user_id, user_name in users.items()
    }


def get_salon_moderators(
    salon: MatrixRoom, *, fomo_user_name=None, kick_user_name=None
) -> Optional[list[str]]:
    user_to_power_level = get_user_to_power_level(salon)
    if fomo_user_name and fomo_user_name in user_to_power_level.keys():
        return None
    if kick_user_name and kick_user_name not in user_to_power_level.keys():
        return None
    minimum_power_level = 50
    if kick_user_name:
        minimum_power_level = user_to_power_level[kick_user_name] + 1
    return [
        user_name
        for user_name, power_level in user_to_power_level.items()
        if power_level >= minimum_power_level
    ]


def room_is_direct_message(room: MatrixRoom) -> bool:
    """
    Détermine si un salon est un message direct (conversation privée).
    
    Args:
        room: L'objet MatrixRoom à vérifier
        
    Returns:
        bool: True si c'est un message direct, False sinon
    """
    # Un salon est considéré comme direct s'il n'a que 2 membres
    return len(room.users) == 2
