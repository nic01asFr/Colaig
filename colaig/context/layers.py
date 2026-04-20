"""
Colaig — Construction des 5 couches contextuelles

Assemble le WorkspaceContext à partir du workspace, du message et de l'historique.
Les 5 couches :
1. Comportement (system_prompt, ton)
2. Capacités (outils disponibles)
3. Conversation (historique récent)
4. Connaissances (rempli par RAG en aval, pas ici)
5. Profil (infos utilisateur extraites du user_id)
"""

from __future__ import annotations

import json
import logging
import re
from typing import Optional

from colaig.models import (
    ContextMode,
    IncomingMessage,
    WorkspaceConfig,
    WorkspaceContext,
)

logger = logging.getLogger(__name__)

# Nombre de messages d'historique à conserver
DEFAULT_HISTORY_LENGTH = 10


def build_context(
    workspace: Optional[WorkspaceConfig],
    message: IncomingMessage,
    mode: ContextMode,
    conversation_history: Optional[list[dict]] = None,
) -> WorkspaceContext:
    """Construit le WorkspaceContext à partir des éléments résolus.

    Args:
        workspace: Configuration du workspace (None en mode chatbot sans workspace).
        message: Message entrant.
        mode: Mode résolu (ASSISTANT, CHATBOT, PERSONAL).
        conversation_history: Historique de conversation chargé depuis le storage.

    Returns:
        WorkspaceContext complet.
    """
    # Couche 1 — Comportement
    system_prompt = _build_system_prompt(workspace, mode)

    # Couche 2 — Capacités
    available_tools = workspace.tools_enabled if workspace else []

    # Couche 3 — Conversation (tronquée aux N derniers messages)
    history = (conversation_history or [])[-DEFAULT_HISTORY_LENGTH:]

    # Couche 5 — Profil
    user_display_name = message.display_name
    user_domain = _extract_domain(message.user_id)

    return WorkspaceContext(
        workspace=workspace,
        mode=mode,
        system_prompt=system_prompt,
        available_tools=available_tools,
        conversation_history=history,
        user_id=message.user_id,
        user_display_name=user_display_name,
        user_domain=user_domain,
    )


def _build_system_prompt(
    workspace: Optional[WorkspaceConfig], mode: ContextMode
) -> str:
    """Construit le system prompt adapté au mode."""
    if workspace and workspace.system_prompt:
        from colaig.security.prompt_sanitizer import sanitize_system_prompt
        prompt = sanitize_system_prompt(workspace.system_prompt)
    elif mode == ContextMode.CHATBOT:
        prompt = (
            "Tu es Colaig, un assistant documentaire IA. "
            "Ce salon n'est pas encore lié à un espace de travail. "
            "Tu peux répondre aux questions générales sur ton fonctionnement. "
            "Si l'utilisateur souhaite accéder à des documents, oriente-le vers la création "
            "d'un espace de travail via la commande `colaig créer <nom>` dans ce salon, "
            "ou via l'outil `colaig_onboard` depuis un client MCP (Claude Desktop, etc.). "
            "Tes capacités : recherche documentaire (RAG) sur Nextcloud/Bigfolder/S3, "
            "mémoire conversationnelle, réponses sourcées, tâches planifiées autonomes."
        )
    elif mode == ContextMode.PERSONAL:
        prompt = (
            "Tu es Colaig, l'assistant documentaire personnel de l'utilisateur. "
            "Tu réponds en conversation directe (DM). "
            "Tu peux accéder aux documents de tous les espaces de travail auxquels "
            "l'utilisateur a accès via l'outil ask_workspace. "
            "Tu peux créer des tâches planifiées (Mode C) pour des travaux longs ou récurrents. "
            "Tu mémorises les informations importantes au fil de la conversation."
        )
    else:
        # Mode ASSISTANT sans system_prompt configuré — fallback générique
        prompt = "Tu es Colaig, un assistant documentaire IA."

    # Ajouter les instructions de ton si workspace configuré
    if workspace and workspace.tone != "professional":
        tone_map = {
            "casual": "Adopte un ton décontracté mais respectueux.",
            "formal": "Adopte un ton très formel et institutionnel.",
            "technical": "Adopte un ton technique et précis.",
        }
        tone_instruction = tone_map.get(workspace.tone, "")
        if tone_instruction:
            prompt = f"{prompt}\n{tone_instruction}"

    return prompt


def _extract_domain(user_id: str) -> str:
    """Extrait le domaine d'un identifiant utilisateur.

    Supporte plusieurs formats :
    - Matrix  : @user:domain            → domain
    - Tchap   : @user-org.gouv.fr:server → org.gouv.fr (domaine métier)
    - Email   : user@domain.com          → domain.com
    - Slack   : U12345 (pas de domaine)  → ""
    - Autre   : identifiant opaque       → ""

    Args:
        user_id: Identifiant utilisateur (format dépend du provider).

    Returns:
        Domaine extrait, chaîne vide si non extractible.
    """
    if not user_id:
        return ""

    # Format Matrix/XMPP (@user:domain)
    if ":" in user_id:
        server_domain = user_id.split(":", 1)[1]

        # Tchap encode le domaine métier dans le localpart (@user-org.gouv.fr:server)
        localpart = user_id.split(":")[0].lstrip("@")
        if "-" in localpart:
            potential_domain = localpart.rsplit("-", 1)[-1]
            if "." in potential_domain:
                return potential_domain

        return server_domain

    # Format email (user@domain)
    if "@" in user_id:
        return user_id.split("@", 1)[1]

    return ""


def _sanitize_id(identifier: str) -> str:
    """Transforme un identifiant en nom de fichier sûr.

    Remplace tout caractère non alphanumérique (sauf _ et -) par un underscore.
    Fonctionne avec tous les formats d'identifiant (Matrix, Slack, UUID, etc.).

    Args:
        identifier: Identifiant brut.

    Returns:
        Chaîne sûre pour un nom de fichier.
    """
    result = re.sub(r"[^a-zA-Z0-9_\-]", "_", identifier)
    return result[:128]  # Borne de sécurité


async def load_conversation_history(
    storage, workspace_path: str, conversation_id: str, max_messages: int = DEFAULT_HISTORY_LENGTH,
) -> list[dict]:
    """Charge l'historique de conversation depuis le storage.

    Args:
        storage: Backend de stockage (StorageProtocol).
        workspace_path: Chemin du workspace.
        conversation_id: Identifiant de la conversation.
        max_messages: Nombre max de messages à charger.

    Returns:
        Liste de messages (dicts avec role/content). Vide si pas d'historique.
    """
    if not workspace_path:
        return []

    # Sécuriser le nom de fichier (conversation_id peut contenir des caractères spéciaux)
    safe_id = _sanitize_id(conversation_id)
    history_path = f"{workspace_path.rstrip('/')}/.colaig/conversations/{safe_id}.json"

    try:
        content = await storage.download(history_path)
        messages = json.loads(content.decode("utf-8"))
        if not isinstance(messages, list):
            return []
        return messages[-max_messages:]
    except Exception:
        # Pas d'historique ou erreur → pas grave
        return []


async def load_relevant_conversation_history(
    storage,
    workspace_path: str,
    conversation_id: str,
    current_query: str,
    max_messages: int = DEFAULT_HISTORY_LENGTH,
    conversation_memory=None,
) -> list[dict]:
    """Charge l'historique de manière contextualisée (sémantique si possible).

    Délègue à ConversationMemory si fournie et requête non vide.
    Sinon retombe sur load_conversation_history (comportement classique).

    Args:
        storage: Backend de stockage.
        workspace_path: Chemin du workspace.
        conversation_id: Identifiant de la conversation.
        current_query: Requête courante pour la récupération sémantique.
        max_messages: Nombre max de messages à retourner.
        conversation_memory: Instance ConversationMemory (optionnel).
    """
    if conversation_memory is not None and current_query.strip():
        return await conversation_memory.load_relevant_history(
            workspace_path, conversation_id, current_query, max_messages
        )
    return await load_conversation_history(storage, workspace_path, conversation_id, max_messages)


async def save_conversation_history(
    storage, workspace_path: str, conversation_id: str, messages: list[dict],
) -> None:
    """Sauvegarde l'historique de conversation sur le storage.

    Args:
        storage: Backend de stockage (StorageProtocol).
        workspace_path: Chemin du workspace.
        conversation_id: Identifiant de la conversation.
        messages: Liste de messages à sauvegarder.
    """
    if not workspace_path:
        return

    safe_id = _sanitize_id(conversation_id)
    conv_dir = f"{workspace_path.rstrip('/')}/.colaig/conversations/"
    history_path = f"{conv_dir}{safe_id}.json"

    try:
        await storage.mkdir(conv_dir)
        content = json.dumps(messages, ensure_ascii=False, indent=2).encode("utf-8")
        await storage.upload(history_path, content)
    except Exception:
        logger.exception("impossible de sauvegarder l'historique: %s", history_path)
