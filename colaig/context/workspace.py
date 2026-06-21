"""
Colaig — Gestion des workspaces

Charge, liste et crée les configurations de workspace depuis le backend de stockage.
Chaque workspace est un dossier contenant .colaig/config.yaml.

Cycle de vie :
    create_workspace()              → scaffold complet + config.yaml
    add_conversation_to_workspace() → lie un salon au workspace
    update_workspace_config()       → met à jour les champs de config
    load_workspace()                → charge depuis storage
    list_workspaces()               → scan de la racine storage
"""

from __future__ import annotations

import logging
import re
from typing import Any

import yaml

from colaig.exceptions import WorkspaceConfigError
from colaig.models import WorkspaceConfig

logger = logging.getLogger(__name__)


async def _ensure_scaffold(storage, workspace_path: str) -> None:
    """Crée silencieusement les sous-dossiers .colaig/ manquants.

    Idempotent — ne lève pas d'erreur si les dossiers existent déjà.
    """
    base = workspace_path.rstrip("/")
    for subdir in (
        f"{base}/.colaig/",
        f"{base}/.colaig/indexes/",
        f"{base}/.colaig/conversations/",
        f"{base}/.colaig/tasks/",
    ):
        try:
            await storage.mkdir(subdir)
        except Exception:
            pass


async def load_workspace(storage, workspace_path: str, repair: bool = False) -> WorkspaceConfig:
    """Charge un WorkspaceConfig depuis le fichier .colaig/config.yaml.

    Normalise toujours workspace_path (trailing slash unique) pour éviter
    les doubles slashes dans les sous-chemins générés.

    Args:
        storage: Backend de stockage (StorageProtocol).
        workspace_path: Chemin racine du workspace (ex: /espace-support/).
        repair: Si True et que config.yaml est absent ou invalide,
                tente une réparation automatique (scaffold + config minimal)
                au lieu de lever WorkspaceConfigError.

    Returns:
        WorkspaceConfig chargé et validé.

    Raises:
        WorkspaceConfigError: Si le fichier est absent/invalide et repair=False,
                              ou si la réparation elle-même échoue.
    """
    # Normaliser : toujours exactement une trailing slash
    workspace_path = workspace_path.rstrip("/") + "/"
    base = workspace_path.rstrip("/")
    config_path = f"{base}/.colaig/config.yaml"

    data: dict | None = None
    load_error: str = ""

    try:
        content = await storage.download(config_path)
        parsed = yaml.safe_load(content.decode("utf-8"))
        if isinstance(parsed, dict):
            data = parsed
        else:
            load_error = "vide ou invalide (non-dict)"
    except Exception as e:
        load_error = str(e)

    if data is None:
        if not repair:
            raise WorkspaceConfigError(
                f"config.yaml absent ou invalide: {config_path} — {load_error}"
            )
        # Réparation automatique : scaffold + config minimal depuis le nom du dossier
        folder_name = base.split("/")[-1] or "workspace"
        ws = WorkspaceConfig(
            workspace_id=_slugify(folder_name),
            name=folder_name,
            storage_path=workspace_path,
            index_path=f"{base}/.colaig/indexes/",
        )
        try:
            await _ensure_scaffold(storage, workspace_path)
            await _save_workspace_config(storage, workspace_path, ws)
            logger.warning(
                "config.yaml absent/corrompu — réparé automatiquement: %s (%s)",
                workspace_path, load_error,
            )
        except Exception as repair_err:
            raise WorkspaceConfigError(
                f"réparation impossible pour {workspace_path}: {repair_err}"
            ) from repair_err
        return ws

    # Construire le WorkspaceConfig depuis le YAML valide
    workspace_id = data.get("workspace_id", base.split("/")[-1])

    # Support des deux noms de champs (conversations + rétrocompat tchap_rooms)
    conversations = data.get("conversations", data.get("tchap_rooms", []))

    # Parser mcp_connectors (liste de dicts → liste de MCPConnectorConfig)
    mcp_connectors_raw = data.get("mcp_connectors", [])
    mcp_connectors = []
    if isinstance(mcp_connectors_raw, list):
        from colaig.models import MCPConnectorConfig
        for mc in mcp_connectors_raw:
            if not isinstance(mc, dict) or not mc.get("name") or not mc.get("url"):
                continue
            try:
                mcp_connectors.append(MCPConnectorConfig(
                    name=mc["name"],
                    url=mc["url"],
                    transport=mc.get("transport", "http"),
                    auth_token=mc.get("auth_token", ""),
                    index_resources=mc.get("index_resources", True),
                    expose_tools=mc.get("expose_tools", True),
                    enabled=mc.get("enabled", True),
                    tool_policy=mc.get("tool_policy", "all"),
                    allowed_tools=mc.get("allowed_tools", []),
                    allowed_domains=mc.get("allowed_domains", []),
                    blocked_ip_ranges=mc.get("blocked_ip_ranges", [
                        "169.254.0.0/16", "10.0.0.0/8", "172.16.0.0/12",
                        "192.168.0.0/16", "127.0.0.0/8", "0.0.0.0/8",
                    ]),
                    session_scope=mc.get("session_scope", "none"),
                    session_header=mc.get("session_header", "X-Session-Id"),
                    max_calls_per_minute=mc.get("max_calls_per_minute", 0),
                    max_result_length=mc.get("max_result_length", 10000),
                ))
            except Exception:
                logger.warning("workspace: mcp_connector invalide ignoré: %s", mc.get("name", "?"))

    return WorkspaceConfig(
        workspace_id=workspace_id,
        name=data.get("name", workspace_id),
        storage_path=workspace_path,
        description=data.get("description", ""),
        conversations=conversations,
        user_ids=data.get("user_ids", []),
        owners=data.get("owners", []),
        system_prompt=data.get("system_prompt", ""),
        tone=data.get("tone", "professional"),
        expertise_level=data.get("expertise_level", "general"),
        language=data.get("language", "fr"),
        rag_enabled=data.get("rag_enabled", True),
        similarity_threshold=data.get("similarity_threshold", 0.3),
        max_results=data.get("max_results", 5),
        priority_documents=data.get("priority_documents", []),
        tools_enabled=data.get("tools_enabled", []),
        storage_readonly=data.get("storage_readonly", False),
        index_path=f"{base}/.colaig/indexes/",
        proactive_notifications=data.get("proactive_notifications", False),
        notification_channels=data.get("notification_channels", []),
        public=data.get("public", False),
        mcp_connectors=mcp_connectors,
    )


async def list_workspaces(storage) -> list[WorkspaceConfig]:
    """Liste tous les workspaces configurés dans le storage.

    Scanne la racine pour trouver les dossiers contenant .colaig/config.yaml.

    Args:
        storage: Backend de stockage (StorageProtocol).

    Returns:
        Liste de WorkspaceConfig.
    """
    workspaces: list[WorkspaceConfig] = []

    try:
        root_entries = await storage.list_files("/", recursive=False)
    except Exception:
        logger.exception("impossible de lister la racine du storage")
        return workspaces

    for entry in root_entries:
        if not entry.is_directory:
            continue
        # Vérifier si ce dossier a un .colaig/config.yaml
        config_path = f"{entry.path.rstrip('/')}/.colaig/config.yaml"
        try:
            exists = await storage.exists(config_path)
            if not exists:
                continue
            # repair=True : si config.yaml existe mais est corrompu/invalide,
            # Colaig le recrée automatiquement depuis les standards.
            ws = await load_workspace(storage, entry.path, repair=True)
            workspaces.append(ws)
        except WorkspaceConfigError as e:
            logger.warning("workspace invalide ignoré après tentative réparation: %s — %s", entry.path, e)
        except Exception:
            logger.exception("erreur scan workspace: %s", entry.path)

    logger.info("workspaces trouvés: %d", len(workspaces))
    return workspaces


def find_workspace_for_conversation(
    workspaces: list[WorkspaceConfig], conversation_id: str
) -> WorkspaceConfig | None:
    """Cherche le workspace associé à un conversation_id.

    Args:
        workspaces: Liste de WorkspaceConfig.
        conversation_id: Identifiant de la conversation.

    Returns:
        WorkspaceConfig si trouvé, None sinon.
    """
    for ws in workspaces:
        if conversation_id in ws.conversations:
            return ws
    return None


def find_workspace_for_user(
    workspaces: list[WorkspaceConfig], user_id: str
) -> WorkspaceConfig | None:
    """Cherche le workspace personnel associé à un user_id (pour les DMs).

    Utilisé quand un utilisateur écrit en DM et dispose d'un workspace
    personnel lié à son identifiant (ex: @alice:agent.tchap.gouv.fr).

    Args:
        workspaces: Liste de WorkspaceConfig.
        user_id: Identifiant de l'utilisateur.

    Returns:
        WorkspaceConfig si trouvé, None sinon.
    """
    for ws in workspaces:
        if user_id in ws.user_ids:
            return ws
    return None


# Alias de rétrocompatibilité
find_workspace_for_room = find_workspace_for_conversation


# =============================================================================
# Fonctions de cycle de vie
# =============================================================================

def _slugify(text: str) -> str:
    """Transforme un texte en slug valide pour workspace_id."""
    slug = text.lower().strip()
    slug = re.sub(r"[àáâãäå]", "a", slug)
    slug = re.sub(r"[èéêë]", "e", slug)
    slug = re.sub(r"[ìíîï]", "i", slug)
    slug = re.sub(r"[òóôõö]", "o", slug)
    slug = re.sub(r"[ùúûü]", "u", slug)
    slug = re.sub(r"[ç]", "c", slug)
    slug = re.sub(r"[^a-z0-9_\-]", "-", slug)
    slug = re.sub(r"-+", "-", slug).strip("-")
    return slug or "workspace"


def personal_workspace_path(user_id: str) -> str:
    """Dérive le chemin storage du workspace personnel depuis un user_id.

    Équivaut à f"/{personal_workspace_slug(user_id)}/".
    Utilisé en fallback défensif quand le WorkspaceConfig n'est pas disponible.
    """
    return f"/{personal_workspace_slug(user_id)}/"


def personal_workspace_slug(user_id: str) -> str:
    """Dérive le slug du workspace personnel depuis un user_id.

    Source unique de vérité — importée par user_memory.py et auth/tokens.py
    pour garantir que tous les modules utilisent la même transformation.

    Exemples :
        @alice:tchap.fr            → alice_tchap_fr
        @bob.dupont:matrix.org     → bob-dupont_matrix-org (via _slugify)
        ""                         → user

    Args:
        user_id: Identifiant utilisateur brut (ex: @alice:agent.tchap.gouv.fr).

    Returns:
        Slug valide (a-z, 0-9, _, -), jamais vide.
    """
    return _slugify(re.sub(r"[^a-zA-Z0-9_\-]", "_", user_id)[:64].strip("_") or "user")


def _workspace_to_dict(ws: WorkspaceConfig) -> dict:
    """Sérialise un WorkspaceConfig en dict YAML-compatible."""
    return {
        "workspace_id": ws.workspace_id,
        "name": ws.name,
        "description": ws.description,
        "storage_path": ws.storage_path,
        "conversations": ws.conversations,
        "system_prompt": ws.system_prompt,
        "tone": ws.tone,
        "expertise_level": ws.expertise_level,
        "language": ws.language,
        "rag_enabled": ws.rag_enabled,
        "similarity_threshold": ws.similarity_threshold,
        "max_results": ws.max_results,
        "priority_documents": ws.priority_documents,
        "tools_enabled": ws.tools_enabled,
        "storage_readonly": ws.storage_readonly,
        "user_ids": ws.user_ids,
        "owners": ws.owners,
        "public": ws.public,
        **({"mcp_connectors": [
            {
                "name": mc.name, "url": mc.url, "transport": mc.transport,
                "auth_token": mc.auth_token, "enabled": mc.enabled,
                "expose_tools": mc.expose_tools, "index_resources": mc.index_resources,
                "tool_policy": mc.tool_policy,
                **({"allowed_tools": mc.allowed_tools} if mc.allowed_tools else {}),
                **({"allowed_domains": mc.allowed_domains} if mc.allowed_domains else {}),
                "session_scope": mc.session_scope,
                **({"max_calls_per_minute": mc.max_calls_per_minute} if mc.max_calls_per_minute else {}),
            }
            for mc in ws.mcp_connectors
        ]} if ws.mcp_connectors else {}),
    }


async def _save_workspace_config(storage, workspace_path: str, ws: WorkspaceConfig) -> None:
    """Sérialise et sauvegarde le config.yaml d'un workspace."""
    config_path = f"{workspace_path.rstrip('/')}/.colaig/config.yaml"
    content = yaml.dump(
        _workspace_to_dict(ws),
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    ).encode("utf-8")
    await storage.upload(config_path, content)


async def create_workspace(
    storage,
    storage_path: str,
    name: str,
    workspace_id: str | None = None,
    description: str = "",
    conversations: list[str] | None = None,
    system_prompt: str = "",
    tone: str = "professional",
    language: str = "fr",
    rag_enabled: bool = True,
    owners: list[str] | None = None,
) -> WorkspaceConfig:
    """Crée un workspace avec le scaffold .colaig/ complet.

    Crée les dossiers nécessaires et le config.yaml avec les valeurs fournies.
    Idempotent : si le workspace existe déjà, retourne la config existante.

    Args:
        storage: Backend de stockage (StorageProtocol).
        storage_path: Chemin racine du workspace (ex: /espace-rh/).
        name: Nom affiché du workspace.
        workspace_id: Identifiant slug unique. Auto-généré depuis name si absent.
        description: Description du workspace.
        conversations: Liste des conversation_ids initialement liés.
        system_prompt: Prompt système personnalisé (vide = défaut Colaig).
        tone: Ton de l'assistant (professional, casual, formal, technical).
        language: Langue principale (fr, en…).
        rag_enabled: Activer la recherche documentaire.

    Returns:
        WorkspaceConfig créé.

    Raises:
        WorkspaceConfigError: Si la création échoue.
    """
    storage_path = storage_path.rstrip("/") + "/"
    wid = workspace_id or _slugify(name) or _slugify(storage_path.strip("/").split("/")[-1])

    # Idempotent : si déjà configuré, retourner l'existant
    config_path = f"{storage_path.rstrip('/')}/.colaig/config.yaml"
    try:
        if await storage.exists(config_path):
            logger.info("workspace déjà existant, chargement: %s", storage_path)
            return await load_workspace(storage, storage_path)
    except Exception:
        pass

    ws = WorkspaceConfig(
        workspace_id=wid,
        name=name,
        storage_path=storage_path,
        description=description,
        conversations=list(conversations or []),
        owners=list(owners or []),
        system_prompt=system_prompt,
        tone=tone,
        language=language,
        rag_enabled=rag_enabled,
        index_path=f"{storage_path.rstrip('/')}/.colaig/indexes/",
    )

    try:
        # Scaffold : dossiers .colaig/
        await storage.mkdir(f"{storage_path.rstrip('/')}/.colaig/")
        await storage.mkdir(f"{storage_path.rstrip('/')}/.colaig/indexes/")
        await storage.mkdir(f"{storage_path.rstrip('/')}/.colaig/conversations/")

        # Écriture du config.yaml
        await _save_workspace_config(storage, storage_path, ws)
    except Exception as e:
        raise WorkspaceConfigError(
            f"impossible de créer le workspace {storage_path}: {e}"
        ) from e

    logger.info("workspace créé: %s (%s)", wid, storage_path)
    return ws


async def add_conversation_to_workspace(
    storage,
    workspace_path: str,
    conversation_id: str,
) -> WorkspaceConfig:
    """Lie un salon/conversation à un workspace existant.

    Ajoute conversation_id à la liste conversations: du config.yaml.
    Idempotent : si déjà présent, ne fait rien.

    Args:
        storage: Backend de stockage (StorageProtocol).
        workspace_path: Chemin racine du workspace.
        conversation_id: Identifiant de la conversation à lier.

    Returns:
        WorkspaceConfig mis à jour.

    Raises:
        WorkspaceConfigError: Si le workspace n'existe pas ou si la sauvegarde échoue.
    """
    ws = await load_workspace(storage, workspace_path)

    if conversation_id in ws.conversations:
        logger.debug("conversation déjà liée: %s → %s", conversation_id, ws.workspace_id)
        return ws

    ws.conversations.append(conversation_id)
    await _save_workspace_config(storage, workspace_path, ws)
    logger.info("conversation liée: %s → %s", conversation_id, ws.workspace_id)
    return ws


async def remove_conversation_from_workspace(
    storage,
    workspace_path: str,
    conversation_id: str,
) -> WorkspaceConfig:
    """Délie un salon/conversation d'un workspace.

    Args:
        storage: Backend de stockage (StorageProtocol).
        workspace_path: Chemin racine du workspace.
        conversation_id: Identifiant de la conversation à délier.

    Returns:
        WorkspaceConfig mis à jour.
    """
    ws = await load_workspace(storage, workspace_path)

    if conversation_id not in ws.conversations:
        return ws

    ws.conversations = [c for c in ws.conversations if c != conversation_id]
    await _save_workspace_config(storage, workspace_path, ws)
    logger.info("conversation déliée: %s ← %s", conversation_id, ws.workspace_id)
    return ws


async def update_workspace_config(
    storage,
    workspace_path: str,
    **fields: Any,
) -> WorkspaceConfig:
    """Met à jour un ou plusieurs champs du config.yaml d'un workspace.

    Seuls les champs reconnus de WorkspaceConfig sont acceptés.
    Les champs inconnus sont ignorés avec un warning.

    Args:
        storage: Backend de stockage (StorageProtocol).
        workspace_path: Chemin racine du workspace.
        **fields: Champs à mettre à jour (name, system_prompt, tone, language,
                  rag_enabled, similarity_threshold, max_results, description,
                  priority_documents, tools_enabled, expertise_level).

    Returns:
        WorkspaceConfig mis à jour.

    Raises:
        WorkspaceConfigError: Si le workspace n'existe pas.
    """
    _UPDATABLE = {
        "name", "description", "system_prompt", "tone", "language",
        "expertise_level", "rag_enabled", "similarity_threshold",
        "max_results", "priority_documents", "tools_enabled",
    }

    ws = await load_workspace(storage, workspace_path)

    updated = []
    for key, value in fields.items():
        if key not in _UPDATABLE:
            logger.warning("champ non modifiable ignoré: %s", key)
            continue
        setattr(ws, key, value)
        updated.append(key)

    if updated:
        await _save_workspace_config(storage, workspace_path, ws)
        logger.info("workspace mis à jour: %s → %s", ws.workspace_id, updated)

    return ws


async def get_or_create_personal_workspace(storage, user_id: str) -> WorkspaceConfig:
    """Retourne ou crée le workspace personnel d'un user (mode DM).

    Chemin : /.colaig/personal/{safe_user_id}/
    Le workspace est configuré avec rag_enabled=False et user_ids=[user_id].
    Idempotent — si le workspace existe déjà, retourne la config existante.

    Args:
        storage: Backend de stockage (StorageProtocol).
        user_id: Identifiant de l'utilisateur (ex: @alice:agent.tchap.gouv.fr).

    Returns:
        WorkspaceConfig du workspace personnel.
    """
    safe = personal_workspace_slug(user_id)
    storage_path = f"/{safe}/"

    # Idempotent : charger si déjà existant
    config_path = f"/{safe}/.colaig/config.yaml"
    try:
        if await storage.exists(config_path):
            return await load_workspace(storage, storage_path)
    except Exception:
        pass

    wid = f"personal-{safe}"
    ws = WorkspaceConfig(
        workspace_id=wid,
        name=f"Personnel — {user_id}",
        storage_path=storage_path,
        description="Workspace personnel (mode DM).",
        user_ids=[user_id],
        rag_enabled=False,
        storage_readonly=False,
        index_path=f"/{safe}/.colaig/indexes/",
    )

    try:
        await storage.mkdir(f"/{safe}/")
        await _ensure_scaffold(storage, storage_path)
        await _save_workspace_config(storage, storage_path, ws)
        logger.info("workspace personnel créé: %s → %s", user_id, storage_path)
    except Exception as e:
        logger.warning("impossible de créer le workspace personnel pour %s: %s", user_id, e)
        # Retourner le workspace en mémoire même si la persistance a échoué

    return ws


def create_default_workspace() -> WorkspaceConfig:
    """Crée un workspace par défaut (mode chatbot).

    Utilisé quand aucun workspace n'est associé à la conversation.
    """
    return WorkspaceConfig(
        workspace_id="_default",
        name="Colaig — Mode découverte",
        storage_path="",
        description="Mode chatbot par défaut, pas de documents associés.",
        system_prompt=(
            "Tu es Colaig, un assistant IA personnel. "
            "Tu es intégré dans un canal de messagerie et tu peux accéder aux documents "
            "partagés sur les espaces de stockage configurés.\n\n"
            "Cette conversation n'est pas encore associée à un espace de travail. "
            "Pour m'activer pleinement :\n"
            "1. Partage-moi un dossier sur ton espace de stockage\n"
            "2. Ajoute un fichier .colaig/config.yaml dans ce dossier\n"
            "3. Indique l'identifiant de cette conversation dans la configuration\n\n"
            "En attendant, je peux répondre à des questions générales sur mon fonctionnement."
        ),
        rag_enabled=False,
    )
