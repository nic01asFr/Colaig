"""
Colaig — Auto-spécialisation de workspace depuis son corpus

Dérive automatiquement persona / vocabulaire / ton / niveau d'expertise d'un
workspace à partir d'un échantillon de ses documents indexés, via un LLM léger.

Principes :
- Opt-in (déclenché explicitement après indexation, jamais par défaut).
- dry_run par défaut : calcule + écrit workspace_knowledge.json (observabilité),
  mais n'écrit la config.yaml que si demandé.
- preserve_manual : n'écrase JAMAIS un system_prompt configuré à la main.
- Graceful : LLM en échec ou JSON invalide → aucune écriture de config, log.
"""

from __future__ import annotations

import json
import logging

from colaig.rag.colaig_index import ColaigIndex
from colaig.security.wrap import CONSIGNE, baliser

logger = logging.getLogger(__name__)

_SYSTEM = (
    "Tu es un expert en analyse de corpus documentaire. À partir d'extraits, "
    "tu dérives le profil d'un assistant spécialisé. Réponds UNIQUEMENT en JSON "
    "valide, sans markdown ni commentaire.\n\n" + CONSIGNE
)

_USER_TEMPLATE = (
    "Analyse ces extraits de documents d'un espace de travail et dérive son profil.\n\n"
    "EXTRAITS :\n{samples}\n\n"
    "Réponds en JSON avec EXACTEMENT ces clés :\n"
    '{{"domain": "...", "sub_domains": ["..."], "vocabulary": ["terme1", "..."], '
    '"tone": "professional|casual|formal|technical", '
    '"expertise_level": "beginner|general|expert", '
    '"system_prompt": "Tu es un assistant expert en ...", '
    '"confidence": 0.0}}'
)

_VALID_TONES = {"professional", "casual", "formal", "technical"}
_VALID_LEVELS = {"beginner", "general", "expert"}


class WorkspaceSpecializer:
    """Dérive le persona d'un workspace depuis son corpus indexé."""

    def __init__(self, storage, albert, model: str = "", max_samples: int = 12,
                 max_sample_chars: int = 800) -> None:
        self._storage = storage
        self._albert = albert
        self._model = model or None
        self._max_samples = max_samples
        self._max_sample_chars = max_sample_chars

    def _sample_texts(self, faiss_store) -> list[str]:
        """Échantillonne des textes de chunks répartis dans le corpus."""
        try:
            chunks = faiss_store.get_all_active_chunks()
        except Exception:  # noqa: BLE001
            return []
        if not chunks:
            return []
        n = len(chunks)
        if n <= self._max_samples:
            picked = chunks
        else:
            step = n / self._max_samples
            picked = [chunks[int(i * step)] for i in range(self._max_samples)]
        return [
            (getattr(c, "text", "") or "")[: self._max_sample_chars]
            for c in picked
            if getattr(c, "text", "")
        ]

    async def derive(self, workspace_path: str, faiss_store, *,
                     dry_run: bool = True, preserve_manual: bool = True) -> dict:
        """Dérive et (optionnellement) applique le persona du workspace.

        Returns:
            dict avec les champs dérivés + 'applied' (bool) + 'updated_fields'.
        """
        samples = self._sample_texts(faiss_store)
        if not samples:
            return {"success": False, "error": "corpus vide", "applied": False}

        # Balisage des échantillons (L2.1). C'est le site le plus conséquent du chemin
        # d'indexation : ce prompt DÉRIVE LE PERSONA DE L'ESPACE depuis le corpus, et
        # l'écrit dans la configuration. Un document déposé pouvait donc réécrire le
        # `system_prompt` de l'instance — une injection qui survit à la conversation,
        # au lieu de s'éteindre avec elle.
        #
        # Le séparateur « --- » était lui aussi forgeable : un document contenant une
        # ligne de tirets se faisait passer pour deux échantillons.
        prompt = _USER_TEMPLATE.format(samples="\n\n".join(
            baliser(echantillon, source=f"extrait {i}", nature="document")
            for i, echantillon in enumerate(samples, 1)
        ))
        try:
            raw = await self._albert.chat(
                messages=[
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": prompt},
                ],
                model=self._model,
                temperature=0.1,
            )
            derived = _parse_persona(raw)
        except Exception as e:  # noqa: BLE001
            logger.warning("specializer: dérivation échouée (%s)", e)
            return {"success": False, "error": str(e), "applied": False}

        if derived is None:
            logger.warning("specializer: réponse LLM non parsable pour %s", workspace_path)
            return {"success": False, "error": "réponse non parsable", "applied": False}

        # Toujours écrire le knowledge.json (observabilité, même en dry-run).
        await self._write_knowledge(workspace_path, samples, derived)

        applied, updated = False, []
        if not dry_run:
            applied, updated = await self._apply(workspace_path, derived, preserve_manual)

        return {
            "success": True,
            **derived,
            "applied": applied,
            "updated_fields": updated,
            "dry_run": dry_run,
        }

    async def _write_knowledge(self, workspace_path: str, samples: list[str],
                               derived: dict) -> None:
        path = ColaigIndex.knowledge_json_path(workspace_path)
        payload = {
            "auto_derived": True,
            "corpus_stats": {"sampled_chunks": len(samples)},
            "derived": derived,
        }
        try:
            await self._storage.upload(
                path, json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("specializer: écriture knowledge.json échouée (%s)", e)

    async def _apply(self, workspace_path: str, derived: dict,
                     preserve_manual: bool) -> tuple[bool, list]:
        from colaig.context.workspace import load_workspace, update_workspace_config

        ws = await load_workspace(self._storage, workspace_path)
        fields: dict = {}
        # preserve_manual : ne pas écraser un system_prompt déjà rempli à la main.
        if derived.get("system_prompt") and not (preserve_manual and ws.system_prompt):
            fields["system_prompt"] = derived["system_prompt"]
        if derived.get("tone") in _VALID_TONES:
            fields["tone"] = derived["tone"]
        if derived.get("expertise_level") in _VALID_LEVELS:
            fields["expertise_level"] = derived["expertise_level"]
        if not fields:
            return False, []
        await update_workspace_config(self._storage, workspace_path, **fields)
        return True, list(fields.keys())


def _parse_persona(raw: str) -> dict | None:
    """Parse la réponse LLM en dict persona (tolère un éventuel fence markdown)."""
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        data = json.loads(text[start: end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    # Normalisation douce
    vocab = data.get("vocabulary", [])
    if isinstance(vocab, list):
        data["vocabulary"] = [str(v) for v in vocab][:50]
    return data
