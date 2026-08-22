"""
Colaig — paths.py

STATUT: COMPLET
VERSION: 2026-08-22 - v1.0
LOT: L0.2

**Source unique des chemins `.colaig/`.**

Aucun autre fichier du projet ne construit un chemin `.colaig/...` en dur. C'est un
principe inviolable (`CLAUDE.md` §2.3), et il est vérifié mécaniquement par
`tests/test_paths_source_unique.py`, qui analyse l'AST de tout `colaig/` et refuse
tout littéral de chaîne contenant `.colaig` ou `.albert` hors de ce module.

Pourquoi ce module existe
-------------------------
Avant lui, 70 littéraux répartis dans 24 fichiers construisaient ces chemins à la main,
avec deux conventions concurrentes : certains appelaient `.rstrip('/')` sur la base,
d'autres non. Un espace déclaré `"/equipe-rh/"` produisait donc tantôt
`/equipe-rh/.colaig/tasks/`, tantôt `/equipe-rh//.colaig/tasks/`. Selon le backend de
stockage, ces deux chemins désignent le même objet ou deux objets distincts — c'est une
source de bugs silencieux, pas une coquetterie.

Conventions
-----------
- La base est **toujours** normalisée par `rstrip('/')`. Un espace peut donc être
  déclaré avec ou sans slash final, indifféremment.
- Les fonctions de **dossier** retournent un chemin **avec** slash final : c'est ce
  qu'attendent `mkdir()` et `list_dir()` de `StorageProtocol`.
- Les fonctions de **fichier** retournent un chemin **sans** slash final.
- Les *clés* de registry FAISS restent la responsabilité de `rag/colaig_index.py`,
  qui délègue à ce module la construction de ses chemins de persistance.

Compatibilité `.albert`
-----------------------
`legacy_albert_path()` construit l'équivalent sous l'ancien nom de dossier. Le code
actuel ne contient **aucun** littéral `.albert` : un espace resté en `.albert` n'est
donc pas lisible aujourd'hui. C'est l'objet du lot L1.7 (migration), et cette fonction
en est la brique de base.
"""
from __future__ import annotations

COLAIG_DIR = ".colaig"
"""Nom du dossier d'instance. Un espace de stockage + ce dossier = une instance."""

LEGACY_DIR = ".albert"
"""Ancien nom du dossier d'instance, antérieur au renommage du projet."""

IGNORE_FILE = ".colaig-ignore"
"""Fichier d'exclusion d'indexation, à la racine de l'espace (hors `.colaig/`)."""


# ── Base ────────────────────────────────────────────────────────────────────


def _base(workspace_path: str) -> str:
    """Normalise la racine d'un espace : jamais de slash final.

    C'est le seul endroit du projet où cette normalisation est décidée.
    """
    return workspace_path.rstrip("/")


def colaig_dir(workspace_path: str) -> str:
    """Dossier d'instance d'un espace — `{ws}/.colaig/`."""
    return f"{_base(workspace_path)}/{COLAIG_DIR}/"


def legacy_albert_path(workspace_path: str, *segments: str) -> str:
    """Équivalent d'un chemin sous l'ancien dossier `.albert/`.

    `legacy_albert_path("/ws/", "config.yaml")` → `"/ws/.albert/config.yaml"`
    `legacy_albert_path("/ws/")`                → `"/ws/.albert/"`

    Sert à lire un espace non encore migré (lot L1.7). Ne jamais l'utiliser en
    écriture : Colaig écrit dans `.colaig/`, et uniquement là.
    """
    base = f"{_base(workspace_path)}/{LEGACY_DIR}"
    if not segments:
        return f"{base}/"
    return f"{base}/" + "/".join(s.strip("/") for s in segments)


def ignore_file(workspace_path: str) -> str:
    """Fichier d'exclusion d'indexation — `{ws}/.colaig-ignore`."""
    return f"{_base(workspace_path)}/{IGNORE_FILE}"


# ── Configuration ───────────────────────────────────────────────────────────


def config_file(workspace_path: str) -> str:
    """Configuration de l'espace — `{ws}/.colaig/config.yaml`.

    Sa présence est ce qui fait d'un dossier un espace Colaig.
    """
    return f"{colaig_dir(workspace_path)}config.yaml"


# ── Conversations et tâches ─────────────────────────────────────────────────


def conversations_dir(workspace_path: str) -> str:
    """Dossier des conversations — `{ws}/.colaig/conversations/`."""
    return f"{colaig_dir(workspace_path)}conversations/"


def conversation_file(workspace_path: str, conversation_id: str) -> str:
    """Fichier d'une conversation — `{ws}/.colaig/conversations/{id}.json`."""
    return f"{conversations_dir(workspace_path)}{conversation_id}.json"


def tasks_dir(workspace_path: str) -> str:
    """Dossier des tâches planifiées — `{ws}/.colaig/tasks/`."""
    return f"{colaig_dir(workspace_path)}tasks/"


def task_file(workspace_path: str, task_id: str) -> str:
    """Fichier d'une tâche — `{ws}/.colaig/tasks/{id}.json`."""
    return f"{tasks_dir(workspace_path)}{task_id}.json"


# ── Index ───────────────────────────────────────────────────────────────────


def indexes_dir(workspace_path: str) -> str:
    """Dossier des index FAISS et BM25 — `{ws}/.colaig/indexes/`."""
    return f"{colaig_dir(workspace_path)}indexes/"


def index_file(workspace_path: str, nom: str) -> str:
    """Fichier d'index — `{ws}/.colaig/indexes/{nom}`.

    `nom` est un nom de fichier complet : `index.faiss`, `metadata.pkl`,
    `behaviors.faiss`, `etags.json`, `bm25.pkl`…
    """
    return f"{indexes_dir(workspace_path)}{nom.lstrip('/')}"


def workspace_knowledge_file(workspace_path: str) -> str:
    """Cartographie sémantique — `{ws}/.colaig/workspace_knowledge.json`."""
    return f"{colaig_dir(workspace_path)}workspace_knowledge.json"


# ── Utilisateurs ────────────────────────────────────────────────────────────


def users_dir(workspace_path: str) -> str:
    """Dossier des données par utilisateur — `{ws}/.colaig/users/`."""
    return f"{colaig_dir(workspace_path)}users/"


def user_dir(workspace_path: str, safe_uid: str) -> str:
    """Dossier d'un utilisateur — `{ws}/.colaig/users/{uid}/`."""
    return f"{users_dir(workspace_path)}{safe_uid}/"


def user_file(workspace_path: str, safe_uid: str, nom: str) -> str:
    """Fichier d'un utilisateur — `{ws}/.colaig/users/{uid}/{nom}`."""
    return f"{user_dir(workspace_path, safe_uid)}{nom.lstrip('/')}"


# ── Profil, prompts, compétences ────────────────────────────────────────────


def profile_dir(workspace_path: str) -> str:
    """Dossier du profil de l'espace — `{ws}/.colaig/profile/`."""
    return f"{colaig_dir(workspace_path)}profile/"


def identity_file(workspace_path: str) -> str:
    """Identité de l'espace — `{ws}/.colaig/profile/identity.yaml`."""
    return f"{profile_dir(workspace_path)}identity.yaml"


def behaviors_dir(workspace_path: str) -> str:
    """Dossier des comportements — `{ws}/.colaig/profile/behaviors/`."""
    return f"{profile_dir(workspace_path)}behaviors/"


def prompts_dir(workspace_path: str) -> str:
    """Dossier des surcharges de prompt — `{ws}/.colaig/prompts/`."""
    return f"{colaig_dir(workspace_path)}prompts/"


def prompt_file(workspace_path: str, role: str) -> str:
    """Surcharge de prompt d'un rôle — `{ws}/.colaig/prompts/{role}.md`."""
    return f"{prompts_dir(workspace_path)}{role}.md"


def skills_dir(workspace_path: str) -> str:
    """Dossier des compétences — `{ws}/.colaig/skills/`."""
    return f"{colaig_dir(workspace_path)}skills/"


# ── Authentification ────────────────────────────────────────────────────────


def tokens_dir(workspace_path: str) -> str:
    """Dossier des jetons — `{ws}/.colaig/tokens/`."""
    return f"{colaig_dir(workspace_path)}tokens/"


def mcp_configs_dir(workspace_path: str) -> str:
    """Dossier des configurations MCP — `{ws}/.colaig/mcp-configs/`."""
    return f"{colaig_dir(workspace_path)}mcp-configs/"


# ── Fédération ──────────────────────────────────────────────────────────────
#
# La fédération est indexée à la racine du storage, pas dans un espace : elle
# décrit l'ensemble des espaces. D'où le `workspace_path` par défaut vide.


def federation_dir(workspace_path: str = "") -> str:
    """Dossier de fédération — `{ws}/.colaig/federation/`, racine par défaut."""
    return f"{colaig_dir(workspace_path)}federation/"


def federation_peers_file(workspace_path: str = "") -> str:
    """Déclaration des pairs — `{ws}/.colaig/federation/peers.yaml`."""
    return f"{federation_dir(workspace_path)}peers.yaml"


def federation_index_files(workspace_path: str = "") -> tuple[str, str]:
    """Index vectoriel des espaces — `(workspaces.faiss, workspaces.pkl)`."""
    base = federation_dir(workspace_path)
    return f"{base}workspaces.faiss", f"{base}workspaces.pkl"


# ── Inspection ──────────────────────────────────────────────────────────────


def is_instance_path(path: str) -> bool:
    """Le chemin traverse-t-il un dossier d'instance, `.colaig/` ou `.albert/` ?

    Utilisé par `security/path_validator.py` : le contenu d'un dossier d'instance
    n'est jamais servi comme document, quel que soit le chemin demandé.
    """
    segments = path.replace("\\", "/").split("/")
    return COLAIG_DIR in segments or LEGACY_DIR in segments
