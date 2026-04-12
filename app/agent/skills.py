# SPDX-License-Identifier: MIT
"""
Skills déclaratives pour Colaig.

Une skill est un fichier Markdown avec frontmatter YAML qui décrit
une procédure métier activable par regex trigger. Elle vit dans
`{workspace}/.albert/skills/*.md`.

Format type :

    ---
    name: instruction_opah
    description: Instruction d'un dossier OPAH
    priority: 50
    triggers:
      - "(?i)\\bOPAH\\b"
      - "(?i)opah[- ]ru"
    prefers_tools:
      - search_documents
      - datagouv__search_datasets
    ---

    ## Procédure d'instruction d'un dossier OPAH

    1. Vérifier l'éligibilité du logement (article R.321-12 du CCH)
    2. Consulter le règlement de l'opération
    3. ...

Quand une skill est activée :
1. Son corps Markdown (hors frontmatter) est injecté dans le system prompt
2. Ses `prefers_tools` sont garantis présents dans le registre
3. Plusieurs skills peuvent matcher : triées par priority décroissante

Charged-once-per-workspace avec cache mtime opportuniste, isolé par workspace.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from app.matrix_bot.config import logger

SKILLS_SUBDIR = ".albert/skills"

# TTL minimal entre deux re-listings WebDAV (anti-spam PROPFIND)
_REFRESH_INTERVAL_SECONDS = 30.0


@dataclass
class Skill:
    """Une skill déclarative chargée depuis un fichier .md.

    name : identifiant unique (extrait du frontmatter ou du nom de fichier)
    description : courte phrase descriptive (utilisée pour observabilité)
    priority : ordre d'application (plus haut = appliqué en premier)
    triggers : liste de patterns regex (compilés)
    prefers_tools : outils à garantir dans le registre quand cette skill matche
    body : contenu Markdown (sans frontmatter) à injecter dans le system prompt
    """
    name: str
    description: str = ""
    priority: int = 0
    triggers: List[re.Pattern] = field(default_factory=list)
    prefers_tools: List[str] = field(default_factory=list)
    body: str = ""

    @classmethod
    def from_markdown(cls, content: str, fallback_name: str = "") -> Optional["Skill"]:
        """Parse un fichier .md avec frontmatter YAML.

        Returns:
            Skill ou None si le fichier est invalide.
        """
        if not content:
            return None

        meta, body = _split_frontmatter(content)
        if not body and not meta:
            return None

        name = (meta.get("name") or fallback_name or "").strip()
        if not name:
            return None

        # Compiler les triggers regex (tolère les invalides)
        raw_triggers = meta.get("triggers") or []
        if isinstance(raw_triggers, str):
            raw_triggers = [raw_triggers]
        compiled = []
        for pattern in raw_triggers:
            try:
                compiled.append(re.compile(pattern))
            except re.error as e:
                logger.warning(f"[SKILL] {name}: regex invalide {pattern!r}: {e}")

        prefers = meta.get("prefers_tools") or []
        if isinstance(prefers, str):
            prefers = [prefers]

        try:
            priority = int(meta.get("priority", 0))
        except (TypeError, ValueError):
            priority = 0

        return cls(
            name=name,
            description=str(meta.get("description", "")).strip(),
            priority=priority,
            triggers=compiled,
            prefers_tools=list(prefers),
            body=body.strip(),
        )

    def matches(self, message: str) -> bool:
        """True si au moins un trigger matche le message."""
        if not self.triggers or not message:
            return False
        return any(p.search(message) for p in self.triggers)


def _split_frontmatter(content: str):
    """Sépare le frontmatter YAML (entre ---) du corps Markdown.

    Returns:
        (metadata_dict, body_text)
    """
    import yaml

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, content

    # Chercher le second '---'
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, content

    fm_text = "\n".join(lines[1:end_idx])
    body = "\n".join(lines[end_idx + 1:])

    try:
        meta = yaml.safe_load(fm_text) or {}
        if not isinstance(meta, dict):
            meta = {}
    except Exception as e:
        logger.warning(f"[SKILL] Frontmatter YAML invalide: {e}")
        meta = {}

    return meta, body


# ─── Cache par workspace ─────────────────────────────────────────────────────

@dataclass
class _SkillsCacheEntry:
    last_check_ts: float
    skills: List[Skill]
    files_etags: Dict[str, str]  # file path → etag


_cache: Dict[str, _SkillsCacheEntry] = {}


async def load_workspace_skills(
    webdav_service,
    workspace_root: str,
    force_refresh: bool = False,
) -> List[Skill]:
    """Charge toutes les skills d'un workspace avec cache mtime opportuniste.

    Args:
        webdav_service: WebDAVService scopé au workspace.
        workspace_root: chemin du workspace (ex: 'rooms/!ROOMa').
        force_refresh: bypasse le cache.

    Returns:
        Liste de Skill triées par priority décroissante.
    """
    if workspace_root is None:
        return []

    now = time.time()
    entry = _cache.get(workspace_root)
    if entry and not force_refresh:
        if (now - entry.last_check_ts) < _REFRESH_INTERVAL_SECONDS:
            return entry.skills

    ws = workspace_root.rstrip("/")
    skills_dir = f"{ws}/{SKILLS_SUBDIR}" if ws else SKILLS_SUBDIR

    # Lister les fichiers .md du dossier skills
    try:
        files = await _list_md_files(webdav_service, skills_dir)
    except Exception as e:
        logger.debug(f"[SKILLS] list {skills_dir} échoué: {e}")
        files = []

    if not files:
        # Pas de skills : mémoriser pour ne pas re-checker à chaque message
        _cache[workspace_root] = _SkillsCacheEntry(
            last_check_ts=now, skills=[], files_etags={},
        )
        return []

    # Charger / recharger chaque fichier
    skills: List[Skill] = []
    new_etags: Dict[str, str] = {}
    old_etags = entry.files_etags if entry else {}
    old_skills_by_path = {p: s for p, s in zip(
        old_etags.keys(),
        entry.skills if entry else [],
    )} if entry else {}

    for path in files:
        try:
            raw_bytes = await webdav_service.download_file(path)
            raw_text = raw_bytes.decode("utf-8") if isinstance(raw_bytes, bytes) else raw_bytes
            etag = _content_etag(raw_text)
            new_etags[path] = etag

            # Cache hit local sur le fichier ?
            if entry and old_etags.get(path) == etag and path in old_skills_by_path:
                skills.append(old_skills_by_path[path])
                continue

            # Parser
            fallback_name = path.rsplit("/", 1)[-1].rsplit(".", 1)[0]
            skill = Skill.from_markdown(raw_text, fallback_name=fallback_name)
            if skill:
                skills.append(skill)
        except Exception as e:
            logger.warning(f"[SKILLS] Lecture {path} échouée: {e}")

    # Tri par priority décroissante
    skills.sort(key=lambda s: s.priority, reverse=True)

    _cache[workspace_root] = _SkillsCacheEntry(
        last_check_ts=now, skills=skills, files_etags=new_etags,
    )

    if skills:
        logger.info(
            f"[SKILLS] {workspace_root!r}: {len(skills)} skill(s) chargée(s) "
            f"({', '.join(s.name for s in skills[:5])})"
        )

    return skills


async def _list_md_files(webdav_service, skills_dir: str) -> List[str]:
    """Liste les fichiers .md dans un dossier skills (1 niveau, pas de récursion).

    Utilise list_directory (PROPFIND Depth:1) au lieu de list_documents
    (récursion profonde) car le dossier skills est plat.
    """
    try:
        if hasattr(webdav_service, "list_directory"):
            entries = await webdav_service.list_directory(skills_dir)
        elif hasattr(webdav_service, "list_documents"):
            # Fallback avec max_depth=1 pour éviter la récursion
            entries = await webdav_service.list_documents(skills_dir, max_depth=1)
        else:
            return []
    except Exception:
        return []

    if not entries:
        return []

    md_paths = []
    for e in entries:
        if isinstance(e, str):
            path = e
        elif isinstance(e, dict):
            path = e.get("path") or e.get("name") or ""
        else:
            path = str(e)

        if path.endswith(".md"):
            if not path.startswith(skills_dir):
                path = f"{skills_dir.rstrip('/')}/{path.lstrip('/')}"
            md_paths.append(path)

    return md_paths


def find_matching_skills(skills: List[Skill], message: str) -> List[Skill]:
    """Retourne les skills dont au moins un trigger matche le message.

    Triées par priority décroissante (déjà fait au chargement).
    """
    if not skills or not message:
        return []
    return [s for s in skills if s.matches(message)]


def _content_etag(text: str) -> str:
    """Etag déterministe basé sur le hash du contenu."""
    import hashlib
    return hashlib.md5(text.encode("utf-8", errors="ignore")).hexdigest()


def invalidate_skills_cache(workspace_root: Optional[str] = None) -> None:
    """Invalide le cache des skills d'un workspace (ou global)."""
    if workspace_root is None:
        _cache.clear()
        logger.info("[SKILLS] Cache global vidé")
    else:
        _cache.pop(workspace_root, None)
        logger.debug(f"[SKILLS] Cache vidé pour {workspace_root!r}")


def get_cache_stats() -> dict:
    """Stats d'observabilité."""
    return {
        "size": len(_cache),
        "workspaces": list(_cache.keys()),
        "total_skills": sum(len(e.skills) for e in _cache.values()),
    }
