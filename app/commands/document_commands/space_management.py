"""
Module de gestion des espaces documentaires.

Ce module contient les commandes pour gérer les espaces documentaires:
- list: Liste les espaces documentaires disponibles
- link: Associe un salon à un espace documentaire
- unlink: Supprime l'association entre un salon et un espace documentaire
- info: Affiche les informations sur l'espace documentaire associé au salon
- index: Force l'indexation d'un espace documentaire
"""

from typing import List, Dict, Any, Optional
import os
import asyncio
import traceback

from app.matrix_bot.client import MatrixClient
from app.matrix_bot.config import logger
from app.matrix_bot.eventparser import EventParser
from nio import Event, RoomMessageText

from app.bot_msg import AlbertMsg
from app.config import Config
from app.services.index_service import get_index_service
from app.services.context import ContextManager, ContextType
from app.commands.registry import register_feature, only_allowed_user
from app.commands import get_context_manager, get_room_context
from app.commands.lifecycle import command_lifecycle
from app.services.webdav_context_manager import get_webdav_context_manager

@register_feature(
    group="document",
    onEvent=RoomMessageText,
    command="space",
    help="!space [list|link|unlink|info|index] - Gérer les espaces documentaires"
)
@only_allowed_user
@command_lifecycle("space", timeout=None)
async def space_management_command(ep: EventParser, matrix_client: MatrixClient):
    """Commande pour gérer les espaces documentaires"""
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    event_id = ep.event.event_id
    sender = ep.sender
    
    # Extraire l'action demandée
    message_text = ep.event.body.strip()
    command_parts = message_text.split()
    action = "info"  # Action par défaut
    
    if len(command_parts) > 1:
        action = command_parts[1].lower()
    
    # Activer l'indicateur de frappe
    await matrix_client.room_typing(room_id, typing_state=True)
    
    try:
        # Récupérer le gestionnaire WebDAV
        webdav_manager = await get_webdav_context_manager(config)
        
        # Traiter les différentes actions
        if action == "list":
            await list_spaces(matrix_client, room_id, webdav_manager)
        elif action == "link":
            space_id = command_parts[2] if len(command_parts) > 2 else None
            await link_space(matrix_client, room_id, space_id, webdav_manager)
        elif action == "unlink":
            await unlink_space(matrix_client, room_id, webdav_manager)
        elif action == "info":
            await show_space_info(matrix_client, room_id, config)
        elif action == "index":
            space_id = command_parts[2] if len(command_parts) > 2 else None
            await index_space(matrix_client, room_id, space_id, webdav_manager, config)
        else:
            # Action inconnue, afficher l'aide
            help_text = """
## Gestion des espaces documentaires

**Usage:**
```
!space list                  # Liste tous les espaces documentaires
!space link <ID>             # Associe le salon à l'espace spécifié
!space unlink                # Supprime l'association du salon
!space info                  # Affiche les informations sur l'espace associé
!space index [ID]            # Force l'indexation d'un espace spécifique
```

Les espaces documentaires sont des dossiers WebDAV contenant un répertoire `.colaig`.
Chaque salon peut être associé à un espace documentaire spécifique.
"""
            await matrix_client.send_markdown_message(room_id, help_text)
    except Exception as e:
        logger.error(f"Erreur dans la commande space: {str(e)}")
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Erreur lors de l'exécution de la commande: {str(e)}",
            msgtype="m.notice",
            reply_to=event_id
        )
    finally:
        # Désactiver l'indicateur de frappe
        await matrix_client.room_typing(room_id, typing_state=False)

async def list_spaces(matrix_client: MatrixClient, room_id: str, webdav_manager):
    """Liste tous les espaces documentaires disponibles"""
    # Découvrir les espaces
    spaces = await webdav_manager.discover_documentation_spaces()
    
    if not spaces:
        await matrix_client.send_markdown_message(
            room_id, 
            "Aucun espace documentaire trouvé. Les espaces sont des dossiers contenant un répertoire `.colaig`.",
            msgtype="m.notice"
        )
        return
        
    # Formater la liste
    space_list = ["📁 **Espaces documentaires disponibles:**"]
    for space_id, space in spaces.items():
        space_list.append(f"- **{space['name']}** (`{space_id}`) : {space['path']}")
        
    await matrix_client.send_markdown_message(
        room_id,
        "\n".join(space_list),
        msgtype="m.notice"
    )

async def link_space(matrix_client: MatrixClient, room_id: str, space_id: str, webdav_manager):
    """Associe un salon à un espace documentaire"""
    if not space_id:
        await matrix_client.send_markdown_message(
            room_id,
            "❌ Veuillez spécifier l'identifiant de l'espace à associer: `!space link <space_id>`",
            msgtype="m.notice"
        )
        return
        
    # Découvrir les espaces disponibles
    spaces = await webdav_manager.discover_documentation_spaces()
    
    if space_id not in spaces:
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Espace documentaire `{space_id}` non trouvé. Utilisez `!space list` pour voir les espaces disponibles.",
            msgtype="m.notice"
        )
        return
        
    # Associer le salon à l'espace
    result = await webdav_manager.set_room_webdav_context(room_id, spaces[space_id]['path'])
    
    if result:
        await matrix_client.send_markdown_message(
            room_id,
            f"✅ Salon associé à l'espace documentaire **{spaces[space_id]['name']}**",
            msgtype="m.notice"
        )
    else:
        await matrix_client.send_markdown_message(
            room_id,
            "❌ Erreur lors de l'association du salon à l'espace documentaire",
            msgtype="m.notice"
        )

async def unlink_space(matrix_client: MatrixClient, room_id: str, webdav_manager):
    """Supprime l'association entre un salon et un espace documentaire"""
    result = await webdav_manager.clear_room_webdav_context(room_id)
    
    if result:
        await matrix_client.send_markdown_message(
            room_id,
            "✅ Association avec l'espace documentaire supprimée",
            msgtype="m.notice"
        )
    else:
        await matrix_client.send_markdown_message(
            room_id,
            "❌ Erreur lors de la suppression de l'association ou aucune association existante",
            msgtype="m.notice"
        )

async def show_space_info(matrix_client: MatrixClient, room_id: str, config: Config):
    """Affiche les informations sur l'espace documentaire associé au salon"""
    # Récupérer le contexte du salon
    room_context = await get_room_context(config, room_id)
    
    if not room_context or not hasattr(room_context, 'webdav_context') or not room_context.webdav_context:
        await matrix_client.send_markdown_message(
            room_id,
            "ℹ️ Ce salon n'est associé à aucun espace documentaire.\nUtilisez `!space link <id>` pour l'associer à un espace.",
            msgtype="m.notice"
        )
        return
        
    # Afficher les informations sur l'espace
    space_path = room_context.webdav_context
    
    info_message = [
        "📁 **Espace documentaire associé à ce salon:**",
        f"- **Chemin:** `{space_path}`",
        "",
        "Pour changer d'espace, utilisez `!space link <id>`.",
        "Pour supprimer l'association, utilisez `!space unlink`."
    ]
    
    await matrix_client.send_markdown_message(
        room_id,
        "\n".join(info_message),
        msgtype="m.notice"
    )

async def index_space(matrix_client: MatrixClient, room_id: str, space_id: str, webdav_manager, config: Config):
    """Force l'indexation d'un espace documentaire"""
    # Si aucun espace n'est spécifié, utiliser l'espace associé au salon
    space_path = None
    
    if space_id:
        # Indexer un espace spécifique
        spaces = await webdav_manager.discover_documentation_spaces()
        
        if space_id not in spaces:
            await matrix_client.send_markdown_message(
                room_id,
                f"❌ Espace documentaire `{space_id}` non trouvé. Utilisez `!space list` pour voir les espaces disponibles.",
                msgtype="m.notice"
            )
            return
            
        space_path = spaces[space_id]['path']
    else:
        # Indexer l'espace associé au salon
        room_context = await get_room_context(config, room_id)
        
        if not room_context or not hasattr(room_context, 'webdav_context') or not room_context.webdav_context:
            await matrix_client.send_markdown_message(
                room_id,
                "❌ Ce salon n'est associé à aucun espace documentaire.\nUtilisez `!space link <id>` pour l'associer à un espace ou spécifiez un ID: `!space index <id>`",
                msgtype="m.notice"
            )
            return
            
        space_path = room_context.webdav_context
    
    # Informer l'utilisateur que l'indexation est en cours
    await matrix_client.send_markdown_message(
        room_id,
        f"🔄 Indexation de l'espace documentaire `{space_path}` en cours...",
        msgtype="m.notice"
    )
    
    # Lancer l'indexation en arrière-plan
    index_service = await get_index_service(config)
    
    # Créer une tâche asynchrone pour l'indexation
    indexing_task = asyncio.create_task(index_service.rebuild_index(space_path))
    
    try:
        # Attendre la fin de l'indexation avec un timeout
        await asyncio.wait_for(indexing_task, timeout=120.0)  # 2 minutes max
        
        # Indexation terminée
        await matrix_client.send_markdown_message(
            room_id,
            f"✅ Indexation de l'espace documentaire `{space_path}` terminée avec succès.",
            msgtype="m.notice"
        )
    except asyncio.TimeoutError:
        # L'indexation prend trop de temps
        await matrix_client.send_markdown_message(
            room_id,
            f"⏳ L'indexation de l'espace documentaire `{space_path}` prend plus de temps que prévu. Elle continue en arrière-plan.",
            msgtype="m.notice"
        )
    except Exception as e:
        # Erreur pendant l'indexation
        await matrix_client.send_markdown_message(
            room_id,
            f"❌ Erreur lors de l'indexation de l'espace: {str(e)}",
            msgtype="m.notice"
        ) 