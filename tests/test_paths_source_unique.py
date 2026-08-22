"""
Contrat du lot L0.2 — `colaig/paths.py` est la source unique des chemins `.colaig/`.

STATUT: TESTE
VERSION: 2026-08-22 - v1.0
LOT: L0.2

Ce fichier contient deux choses :

1. `test_aucun_chemin_colaig_hors_paths` — le **critère de fin du lot**, vérifié
   mécaniquement. Aucun fichier de `colaig/` ne doit construire un chemin `.colaig/`
   ou `.albert/` en dur.

2. Les tests unitaires de `paths.py` lui-même : normalisation de la base, slash final,
   compatibilité `.albert`.

Sur la formulation du critère
-----------------------------
`PLAN.md` énonçait le critère ainsi :

    grep -rn '\\.colaig\\|\\.albert' colaig/ --include=*.py | grep -v paths.py  → vide

Ce grep ne peut **jamais** être vide, pour deux raisons qui n'ont rien à voir avec la
qualité du code :

- `from colaig.rag.colaig_index import ColaigIndex` contient la sous-chaîne `.colaig`
  (dans `rag.colaig_index`) et matche donc le motif ;
- les docstrings et commentaires mentionnent légitimement `.colaig/` pour expliquer
  ce que fait le code.

Sur 206 lignes remontées par le grep initial, **70** correspondaient à une vraie
construction de chemin. Le critère est donc reformulé ici de façon exécutable :

    Aucun **littéral de chaîne**, hors docstring, **sans espace**, contenant
    `.colaig` ou `.albert`, en dehors de `colaig/paths.py`.

L'analyse AST écarte d'office les commentaires (absents de l'AST) et les noms de
modules (qui sont des identifiants, pas des chaînes). L'exclusion des chaînes
contenant une espace écarte la prose destinée aux humains — messages d'accueil,
descriptions d'outils MCP, gabarit `docker-compose` — qui mentionne `.colaig/` sans
le construire. Aucun fragment de chemin réel ne contient d'espace.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from colaig import paths

RACINE = pathlib.Path(__file__).resolve().parent.parent / "colaig"
MODULE_AUTORISE = "paths.py"
JETONS = (".colaig", ".albert")


def _ids_docstrings(arbre: ast.AST) -> set[int]:
    """id() des noeuds Constant qui sont la docstring d'un module/classe/fonction."""
    ids: set[int] = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            corps = getattr(noeud, "body", [])
            if (
                corps
                and isinstance(corps[0], ast.Expr)
                and isinstance(corps[0].value, ast.Constant)
                and isinstance(corps[0].value.value, str)
            ):
                ids.add(id(corps[0].value))
    return ids


def _violations() -> list[tuple[str, int, str]]:
    """→ [(fichier, ligne, littéral)] des constructions de chemin hors paths.py."""
    trouvees: list[tuple[str, int, str]] = []
    for fichier in sorted(RACINE.rglob("*.py")):
        if fichier.name == MODULE_AUTORISE:
            continue
        try:
            arbre = ast.parse(fichier.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - un fichier illisible est un autre problème
            continue
        docstrings = _ids_docstrings(arbre)
        for noeud in ast.walk(arbre):
            if not isinstance(noeud, ast.Constant) or not isinstance(noeud.value, str):
                continue
            if id(noeud) in docstrings:
                continue
            valeur = noeud.value
            if any(c.isspace() for c in valeur):
                continue  # prose destinée à un humain, pas un chemin
            if any(jeton in valeur for jeton in JETONS):
                trouvees.append(
                    (str(fichier.relative_to(RACINE.parent)), noeud.lineno, valeur)
                )
    return trouvees


def test_aucun_chemin_colaig_hors_paths():
    """Critère de fin L0.2 : `paths.py` est la seule source des chemins `.colaig/`."""
    violations = _violations()
    if violations:
        detail = "\n".join(
            f"  {f}:{ligne}  {valeur!r}" for f, ligne, valeur in violations
        )
        pytest.fail(
            f"{len(violations)} construction(s) de chemin hors de colaig/paths.py :\n"
            f"{detail}\n\n"
            "Utiliser les fonctions de `colaig.paths` au lieu d'un littéral."
        )


def test_aucune_concatenation_avec_slash_apres_un_dossier():
    """Un chemin de dossier finit déjà par `/` : le concaténer avec `/` fait `//`.

    Ce test existe parce que le portage du lot a introduit précisément ce bug à trois
    endroits (`behavior_indexer`, `skill_indexer`, `pre_execution`) : les fonctions
    `*_dir()` retournent un slash final, alors que le code d'origine construisait ces
    dossiers sans. **Les 1574 tests de la suite n'ont rien vu**, parce qu'aucun ne
    vérifie la forme des chemins de persistance.

    Un `//` produit un objet distinct sur certains backends de stockage : l'index
    s'écrit à un endroit et se relit à un autre, sans erreur visible. C'est le mode de
    défaillance le plus coûteux — silencieux.
    """
    motif_variable = re.compile(r"(\w+)\s*=\s*paths\.\w*_dir\(")
    motif_direct = re.compile(r"paths\.\w*_dir\([^)]*\)\}/")
    suspects: list[str] = []

    for fichier in sorted(RACINE.rglob("*.py")):
        if fichier.name == MODULE_AUTORISE:
            continue
        lignes = fichier.read_text(encoding="utf-8").split("\n")
        variables = {m.group(1) for ligne in lignes for m in [motif_variable.search(ligne)] if m}
        for numero, ligne in enumerate(lignes, 1):
            if motif_direct.search(ligne):
                suspects.append(f"  {fichier.name}:{numero}  {ligne.strip()[:90]}")
                continue
            for variable in variables:
                if re.search(r"\{" + re.escape(variable) + r"\}/", ligne):
                    suspects.append(f"  {fichier.name}:{numero}  {ligne.strip()[:90]}")

    if suspects:
        pytest.fail(
            "Chemin de dossier concaténé avec un '/' — produit un double slash :\n"
            + "\n".join(suspects)
            + "\n\nUtiliser la fonction de fichier correspondante (index_file, "
            "user_file, conversation_file…) ou concaténer sans slash."
        )


# ── Tests unitaires de paths.py ─────────────────────────────────────────────


@pytest.mark.parametrize("base", ["/equipe-rh", "/equipe-rh/", "/equipe-rh///"])
def test_base_normalisee_quel_que_soit_le_slash_final(base):
    """Une base avec ou sans slash final produit exactement le même chemin.

    C'est le bug que le lot corrige : avant, `/equipe-rh/` donnait
    `/equipe-rh//.colaig/tasks/` dans les appelants qui omettaient le `rstrip`.
    """
    assert paths.colaig_dir(base) == "/equipe-rh/.colaig/"
    assert paths.config_file(base) == "/equipe-rh/.colaig/config.yaml"
    assert paths.tasks_dir(base) == "/equipe-rh/.colaig/tasks/"
    assert "//" not in paths.tasks_dir(base)


def test_dossiers_avec_slash_final_fichiers_sans():
    """Convention : dossier → slash final (mkdir/list_dir), fichier → sans."""
    ws = "/ws"
    for dossier in (
        paths.colaig_dir(ws),
        paths.conversations_dir(ws),
        paths.tasks_dir(ws),
        paths.indexes_dir(ws),
        paths.users_dir(ws),
        paths.user_dir(ws, "alice"),
        paths.profile_dir(ws),
        paths.behaviors_dir(ws),
        paths.prompts_dir(ws),
        paths.skills_dir(ws),
        paths.tokens_dir(ws),
        paths.mcp_configs_dir(ws),
        paths.federation_dir(ws),
    ):
        assert dossier.endswith("/"), dossier
    for fichier in (
        paths.config_file(ws),
        paths.conversation_file(ws, "salon1"),
        paths.task_file(ws, "t1"),
        paths.identity_file(ws),
        paths.prompt_file(ws, "analyste"),
        paths.workspace_knowledge_file(ws),
        paths.ignore_file(ws),
        paths.federation_peers_file(ws),
    ):
        assert not fichier.endswith("/"), fichier


def test_chemins_attendus():
    ws = "/espace-rh/"
    assert paths.conversation_file(ws, "!salon:tchap") == "/espace-rh/.colaig/conversations/!salon:tchap.json"
    assert paths.task_file(ws, "t-42") == "/espace-rh/.colaig/tasks/t-42.json"
    assert paths.index_file(ws, "index.faiss") == "/espace-rh/.colaig/indexes/index.faiss"
    assert paths.user_file(ws, "alice", "profile.json") == "/espace-rh/.colaig/users/alice/profile.json"
    assert paths.identity_file(ws) == "/espace-rh/.colaig/profile/identity.yaml"
    assert paths.prompt_file(ws, "synthetiseur") == "/espace-rh/.colaig/prompts/synthetiseur.md"
    assert paths.ignore_file(ws) == "/espace-rh/.colaig-ignore"


def test_federation_a_la_racine_par_defaut():
    """La fédération décrit l'ensemble des espaces : elle vit à la racine."""
    assert paths.federation_dir() == "/.colaig/federation/"
    faiss, meta = paths.federation_index_files()
    assert faiss == "/.colaig/federation/workspaces.faiss"
    assert meta == "/.colaig/federation/workspaces.pkl"
    assert paths.federation_peers_file() == "/.colaig/federation/peers.yaml"


def test_legacy_albert():
    """`legacy_albert_path` reproduit l'arborescence sous l'ancien nom de dossier."""
    assert paths.legacy_albert_path("/ws/") == "/ws/.albert/"
    assert paths.legacy_albert_path("/ws", "config.yaml") == "/ws/.albert/config.yaml"
    assert paths.legacy_albert_path("/ws", "conversations", "a.json") == "/ws/.albert/conversations/a.json"


def test_is_instance_path():
    """Le validateur de chemins doit reconnaître les deux noms de dossier."""
    assert paths.is_instance_path("/ws/.colaig/config.yaml")
    assert paths.is_instance_path("/ws/.albert/config.yaml")
    assert paths.is_instance_path(".colaig")
    assert not paths.is_instance_path("/ws/documents/rapport.pdf")
    # Un nom de fichier qui *contient* la chaîne n'est pas un dossier d'instance.
    assert not paths.is_instance_path("/ws/mon.colaig.txt")
