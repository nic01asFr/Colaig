"""
Colaig — TrameManager (Phase 6)

Seul composant autorisé à écrire la trame vivante d'une conversation.
Les agents lisent la trame (via AgentContext) mais ne l'écrivent jamais directement.

Cycle de vie par requête :
  load(conv_id) → pipeline → update(trame, intent, plan, response) → save(trame)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from colaig.exceptions import ColaigError
from colaig.models import (
    ContextAnchor,
    ConversationTrame,
    ExecutionPlan,
    GeneratedResponse,
    Intent,
    WorkflowStep,
)
from colaig.protocols import StorageProtocol

logger = logging.getLogger(__name__)


class TrameManager:
    """Gestion de la trame vivante par conversation.

    Cohérence garantie par TaskExecutor : une queue séquentielle par conversation
    assure qu'un seul pipeline à la fois écrit la trame d'une conversation donnée.

    Args:
        storage: Backend de stockage (StorageProtocol).
    """

    def __init__(self, storage: StorageProtocol) -> None:
        self._storage = storage

    async def load(self, conv_id: str, workspace_path: str) -> ConversationTrame:
        """Charge la trame d'une conversation depuis le storage.

        Si absente : retourne une ConversationTrame vide (phase=discovery, pas d'anchors).

        Args:
            conv_id: Identifiant de la conversation.
            workspace_path: Chemin racine du workspace dans le storage.

        Returns:
            ConversationTrame chargée ou initialisée à vide.
        """
        path = f"{workspace_path.rstrip('/')}/.colaig/conversations/{conv_id}_trame.json"
        try:
            data = await self._storage.download(path)
            trame = self._deserialize(json.loads(data.decode("utf-8")))
            logger.debug(
                "trame chargée conv=%s phase=%s anchors=%d",
                conv_id,
                trame.conversation_phase,
                len(trame.context_anchors),
            )
            return trame
        except Exception:
            logger.debug("trame absente conv=%s → initialisation discovery", conv_id)
            return ConversationTrame(conv_id=conv_id, workspace_id=workspace_path)

    async def update(
        self,
        trame: ConversationTrame,
        intent: Optional[Intent] = None,
        plan: Optional[ExecutionPlan] = None,
        response: Optional[GeneratedResponse] = None,
    ) -> ConversationTrame:
        """Met à jour la trame avec les résultats du pipeline.

        Applique les signaux fournis par les agents (suggested_next_phase, new_anchors,
        sources trouvées) sans que les agents n'écrivent la trame directement.

        Args:
            trame: Trame courante à mettre à jour (mutée en place).
            intent: Intent produit par l'Analyser (signaux trame optionnels).
            plan: Plan d'exécution de l'Orchestrateur (documents trouvés).
            response: Réponse générée par le Synthétiseur (anchors décisions).

        Returns:
            La trame mise à jour (même objet, mutée).
        """
        trame.turn_count += 1
        trame.updated_at = datetime.utcnow()

        # Signaux de l'Analyser
        if intent is not None:
            if intent.suggested_next_phase:
                old_phase = trame.conversation_phase
                trame.conversation_phase = intent.suggested_next_phase
                logger.debug("trame: phase %s → %s", old_phase, trame.conversation_phase)

            for anchor in intent.new_anchors:
                if anchor.ref and not self._anchor_exists(trame, anchor.ref):
                    anchor.turn_index = trame.turn_count
                    if anchor.established_at is None:
                        anchor.established_at = datetime.utcnow()
                    trame.context_anchors.append(anchor)
                    logger.debug("trame: ancre ajoutée (%s: %s)", anchor.anchor_type, anchor.ref)

        # Documents trouvés par l'Orchestrateur → anchors documentaires
        if plan is not None and plan.search_results:
            seen_in_plan: set[str] = set()
            for result in plan.search_results:
                chunk = getattr(result, "chunk", None)
                if chunk is None:
                    continue
                src_path = getattr(chunk, "source_path", "")
                src_name = getattr(chunk, "source_name", "")
                if src_path and src_path not in seen_in_plan:
                    seen_in_plan.add(src_path)
                    if not self._anchor_exists(trame, src_path):
                        trame.context_anchors.append(
                            ContextAnchor(
                                anchor_type="document",
                                ref=src_path,
                                description=src_name,
                                turn_index=trame.turn_count,
                                established_at=datetime.utcnow(),
                            )
                        )

        # Signaux du Synthétiseur (décisions, contraintes exprimées)
        if response is not None:
            meta = getattr(response, "metadata", None)
            if isinstance(meta, dict):
                for anchor_data in meta.get("new_anchors", []):
                    ref = anchor_data.get("ref", "")
                    if ref and not self._anchor_exists(trame, ref):
                        trame.context_anchors.append(
                            ContextAnchor(
                                anchor_type=anchor_data.get("type", "decision"),
                                ref=ref,
                                description=anchor_data.get("description", ""),
                                turn_index=trame.turn_count,
                            )
                        )

        return trame

    async def save(
        self,
        trame: ConversationTrame,
        workspace_path: str,
        storage_readonly: bool = False,
    ) -> None:
        """Persiste la trame dans le storage.

        No-op si storage_readonly=True (trame en mémoire uniquement, workspace partagé).

        Args:
            trame: Trame à persister.
            workspace_path: Chemin racine du workspace.
            storage_readonly: Si True, ne pas écrire dans le storage.
        """
        if storage_readonly:
            logger.debug("trame non persistée (storage_readonly=True) conv=%s", trame.conv_id)
            return

        path = f"{workspace_path.rstrip('/')}/.colaig/conversations/{trame.conv_id}_trame.json"
        data = json.dumps(self._serialize(trame), ensure_ascii=False, indent=2).encode("utf-8")
        try:
            await self._storage.upload(path, data)
            logger.debug("trame persistée conv=%s", trame.conv_id)
        except Exception as e:
            logger.warning("trame non persistée conv=%s: %s", trame.conv_id, e)

    # -------------------------------------------------------------------------
    # Helpers privés
    # -------------------------------------------------------------------------

    def _anchor_exists(self, trame: ConversationTrame, ref: str) -> bool:
        return any(a.ref == ref for a in trame.context_anchors)

    def _serialize(self, trame: ConversationTrame) -> dict:
        return {
            "conv_id": trame.conv_id,
            "workspace_id": trame.workspace_id,
            "conversation_phase": trame.conversation_phase,
            "active_behavior": trame.active_behavior,
            "workflow_steps": [
                {
                    "step_id": s.step_id,
                    "description": s.description,
                    "completed": s.completed,
                    "turn_index": s.turn_index,
                    "summary": s.summary,
                }
                for s in trame.workflow_steps
            ],
            "context_anchors": [
                {
                    "anchor_type": a.anchor_type,
                    "ref": a.ref,
                    "description": a.description,
                    "turn_index": a.turn_index,
                    "established_at": a.established_at.isoformat() if a.established_at else None,
                }
                for a in trame.context_anchors
            ],
            "turn_count": trame.turn_count,
            "created_at": trame.created_at.isoformat(),
            "updated_at": trame.updated_at.isoformat(),
        }

    def _deserialize(self, data: dict) -> ConversationTrame:
        return ConversationTrame(
            conv_id=data["conv_id"],
            workspace_id=data["workspace_id"],
            conversation_phase=data.get("conversation_phase", "discovery"),
            active_behavior=data.get("active_behavior"),
            workflow_steps=[
                WorkflowStep(
                    step_id=s["step_id"],
                    description=s["description"],
                    completed=s.get("completed", False),
                    turn_index=s.get("turn_index", -1),
                    summary=s.get("summary", ""),
                )
                for s in data.get("workflow_steps", [])
            ],
            context_anchors=[
                ContextAnchor(
                    anchor_type=a["anchor_type"],
                    ref=a["ref"],
                    description=a.get("description", ""),
                    turn_index=a.get("turn_index", 0),
                    established_at=(
                        datetime.fromisoformat(a["established_at"])
                        if a.get("established_at") else None
                    ),
                )
                for a in data.get("context_anchors", [])
            ],
            turn_count=data.get("turn_count", 0),
            created_at=datetime.fromisoformat(data["created_at"])
            if "created_at" in data
            else datetime.utcnow(),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if "updated_at" in data
            else datetime.utcnow(),
        )
