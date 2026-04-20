"""
Colaig — Context Resolver

Le cerveau de Colaig : pour chaque message, identifie le workspace et
construit le contexte complet (mode, system_prompt, historique, profil).

Implémente ContextResolverProtocol.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from colaig.models import (
    ContextMode,
    ConversationType,
    IncomingMessage,
    WorkspaceConfig,
    WorkspaceContext,
)
from colaig.context.workspace import (
    create_default_workspace,
    find_workspace_for_conversation,
    find_workspace_for_user,
    get_or_create_personal_workspace,
    list_workspaces,
)
from colaig.context.layers import (
    build_context,
    load_conversation_history,
)

logger = logging.getLogger(__name__)


class ContextResolver:
    """Résolveur de contexte — identifie le workspace et construit le contexte.

    Args:
        storage: Backend de stockage (StorageProtocol).
        cache_ttl: Durée de vie du cache des workspaces en secondes.
    """

    def __init__(self, storage, cache_ttl: int = 60, default_workspace_id: str = "") -> None:
        self._storage = storage
        self._cache_ttl = cache_ttl
        self._default_workspace_id = default_workspace_id

        # Cache des workspaces chargés
        self._workspaces: list[WorkspaceConfig] = []
        self._cache_timestamp: float = 0.0

        # Cache conversation_id → workspace rapide
        self._conversation_mapping: dict[str, WorkspaceConfig] = {}

    async def resolve(self, message: IncomingMessage) -> WorkspaceContext:
        """Résout le contexte complet pour un message.

        Algorithme :
        1. Chercher conversation_id dans le cache des mappings
        2. Si pas trouvé → scanner les workspaces
        3. Si toujours pas trouvé → mode chatbot ou personnel selon conversation_type

        Args:
            message: Message entrant.

        Returns:
            WorkspaceContext complet.
        """
        # 1. Chercher dans le cache rapide
        workspace = self._conversation_mapping.get(message.conversation_id)

        # 2. Si pas en cache, rafraîchir les workspaces
        if workspace is None:
            await self._ensure_cache_fresh()
            workspace = find_workspace_for_conversation(self._workspaces, message.conversation_id)
            if workspace:
                self._conversation_mapping[message.conversation_id] = workspace

        # 2b. En DM, essayer le mapping par user_id (workspace personnel)
        if workspace is None and message.conversation_type == ConversationType.DM:
            workspace = find_workspace_for_user(self._workspaces, message.user_id)
            if workspace:
                self._conversation_mapping[message.conversation_id] = workspace

        # 3. Déterminer le mode
        # Un workspace personnel (workspace_id="personal-*") trouvé en DM → PERSONAL
        # Un workspace métier trouvé par conversation_id → ASSISTANT
        if workspace and workspace.workspace_id.startswith("personal-"):
            mode = ContextMode.PERSONAL
        elif workspace:
            mode = ContextMode.ASSISTANT
        elif message.conversation_type == ConversationType.DM:
            mode = ContextMode.PERSONAL
            # Créer (ou charger) le workspace personnel de cet utilisateur
            try:
                workspace = await get_or_create_personal_workspace(self._storage, message.user_id)
                self._conversation_mapping[message.conversation_id] = workspace
                # Enregistrer dans la liste pour les résolutions futures
                if workspace not in self._workspaces:
                    self._workspaces.append(workspace)
            except Exception as e:
                logger.warning("workspace personnel indisponible pour %s: %s", message.user_id, e)
                workspace = create_default_workspace()
        else:
            mode = ContextMode.CHATBOT
            # Utiliser le workspace par défaut configuré si disponible (ex: "accueil")
            default_ws = None
            if self._default_workspace_id:
                default_ws = next(
                    (ws for ws in self._workspaces if ws.workspace_id == self._default_workspace_id),
                    None,
                )
            workspace = default_ws or create_default_workspace()

        # 4. Charger l'historique de conversation
        history = await load_conversation_history(
            self._storage,
            workspace.storage_path,
            message.conversation_id,
        )

        # 5. Construire le contexte
        ctx = build_context(
            workspace=workspace,
            message=message,
            mode=mode,
            conversation_history=history,
        )

        logger.debug(
            "résolu: conversation=%s → mode=%s, workspace=%s",
            message.conversation_id, mode.value,
            workspace.workspace_id if workspace else "none",
        )

        return ctx

    async def refresh_cache(self) -> None:
        """Force le rechargement du cache des workspaces."""
        self._workspaces = await list_workspaces(self._storage)
        self._cache_timestamp = time.monotonic()

        # Reconstruire le mapping conversation → workspace + user_id → workspace
        self._conversation_mapping.clear()
        for ws in self._workspaces:
            for conv_id in ws.conversations:
                self._conversation_mapping[conv_id] = ws
            # user_ids sont résolus dynamiquement dans resolve() pour les DMs

        logger.info(
            "cache workspaces rafraîchi: %d workspaces, %d mappings conversation, %d mappings user",
            len(self._workspaces), len(self._conversation_mapping),
            sum(len(ws.user_ids) for ws in self._workspaces),
        )

    async def _ensure_cache_fresh(self) -> None:
        """Rafraîchit le cache si le TTL est expiré."""
        now = time.monotonic()
        if now - self._cache_timestamp > self._cache_ttl:
            await self.refresh_cache()

    async def register_workspace(self, workspace: WorkspaceConfig) -> None:
        """Enregistre un workspace dans le cache sans re-scanner tout le storage.

        Utilisé après create_workspace() pour rendre le workspace immédiatement
        disponible sans attendre le prochain cycle de refresh. Réinitialise
        le timestamp du cache pour éviter un refresh immédiat qui écraserait
        le workspace nouvellement enregistré.

        Args:
            workspace: WorkspaceConfig à enregistrer.
        """
        import time

        # Remplacer si déjà présent (mise à jour), sinon ajouter
        self._workspaces = [
            ws for ws in self._workspaces if ws.workspace_id != workspace.workspace_id
        ]
        self._workspaces.append(workspace)

        # Mettre à jour le mapping conversation → workspace
        for conv_id in workspace.conversations:
            self._conversation_mapping[conv_id] = workspace

        # Réinitialiser le timestamp : le cache est considéré frais après
        # un register_workspace, évitant un refresh immédiat qui écraserait
        # les workspaces enregistrés manuellement.
        self._cache_timestamp = time.monotonic()

        logger.info(
            "workspace enregistré en cache: %s (%d conversations)",
            workspace.workspace_id, len(workspace.conversations),
        )

    def invalidate_conversation(self, conversation_id: str) -> None:
        """Retire un conversation_id du cache de mapping.

        Utilisé après remove_conversation_from_workspace() pour forcer
        une re-résolution au prochain message.

        Args:
            conversation_id: Identifiant de la conversation à invalider.
        """
        self._conversation_mapping.pop(conversation_id, None)
        logger.debug("mapping invalidé: %s", conversation_id)

    @property
    def workspaces(self) -> list[WorkspaceConfig]:
        """Liste des workspaces actuellement en cache."""
        return list(self._workspaces)
