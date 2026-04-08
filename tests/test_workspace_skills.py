"""
Test des skills déclaratives par workspace (phase 3).

Couvre :
- Parsing du frontmatter YAML + body Markdown
- Compilation des regex triggers (tolérance aux invalides)
- find_matching_skills : matching par regex
- Tri par priority décroissante
- Cache mtime opportuniste par workspace
- Isolation entre workspaces
- Injection des skills dans build_system_prompt

Usage :
    PYTHONPATH=. python tests/test_workspace_skills.py
"""
import asyncio
import sys
from unittest.mock import MagicMock


SKILL_OPAH = """---
name: instruction_opah
description: Instruction d'un dossier OPAH
priority: 50
triggers:
  - "(?i)\\\\bOPAH\\\\b"
  - "(?i)opah[- ]ru"
prefers_tools:
  - search_documents
  - datagouv__search_datasets
---

## Procédure d'instruction d'un dossier OPAH

1. Vérifier l'éligibilité du logement (article R.321-12 du CCH)
2. Consulter le règlement de l'opération
3. Vérifier le statut d'occupation
"""

SKILL_PLU = """---
name: consultation_plu
description: Consultation du PLU
priority: 30
triggers:
  - "(?i)PLU(i)?"
  - "(?i)plan local d'urbanisme"
prefers_tools:
  - search_documents
---

## Consultation d'un PLU

Étapes :
1. Identifier la commune
2. Consulter le règlement
"""

SKILL_INVALID_REGEX = """---
name: bad_regex
triggers:
  - "[invalid("
---

Body
"""


def _make_webdav_mock(file_contents: dict, dir_listings: dict = None):
    """Mock WebDAVService avec exists, download_file, list_documents."""
    svc = MagicMock()

    async def fake_exists(path):
        return path in file_contents

    async def fake_download(path):
        content = file_contents.get(path)
        if content is None:
            raise FileNotFoundError(path)
        return content.encode("utf-8") if isinstance(content, str) else content

    async def fake_list(directory):
        return (dir_listings or {}).get(directory, [])

    svc.exists = fake_exists
    svc.download_file = fake_download
    svc.list_documents = fake_list
    return svc


def _reset_cache():
    from app.agent import skills
    skills._cache.clear()


def test_skill_parsing():
    """Parse correct du frontmatter + body."""
    from app.agent.skills import Skill
    skill = Skill.from_markdown(SKILL_OPAH)
    assert skill is not None
    assert skill.name == "instruction_opah"
    assert skill.description == "Instruction d'un dossier OPAH"
    assert skill.priority == 50
    assert len(skill.triggers) == 2
    assert "search_documents" in skill.prefers_tools
    assert "Procédure d'instruction" in skill.body
    assert "Vérifier l'éligibilité" in skill.body
    print("OK parsing frontmatter + body")


def test_skill_matching():
    """Triggers regex matchent correctement."""
    from app.agent.skills import Skill
    skill = Skill.from_markdown(SKILL_OPAH)

    assert skill.matches("Comment instruire un OPAH ?")
    assert skill.matches("Procédure OPAH-RU à Laval")
    assert skill.matches("opah ru en zone rurale")
    assert not skill.matches("Bonjour comment vas-tu ?")
    assert not skill.matches("Cherche dans les documents")
    print("OK matching regex (insensible à la casse)")


def test_skill_invalid_regex_tolerance():
    """Regex invalide → skill créée avec triggers vides, pas de crash."""
    from app.agent.skills import Skill
    skill = Skill.from_markdown(SKILL_INVALID_REGEX)
    assert skill is not None
    assert skill.name == "bad_regex"
    assert len(skill.triggers) == 0  # regex invalide ignorée
    assert not skill.matches("anything")
    print("OK regex invalide tolérée")


def test_skill_no_frontmatter():
    """Markdown sans frontmatter → None (skill invalide)."""
    from app.agent.skills import Skill
    skill = Skill.from_markdown("# Just markdown\n\nNo frontmatter here.")
    assert skill is None
    print("OK pas de frontmatter → None")


def test_find_matching_skills_priority():
    """find_matching_skills retourne dans l'ordre de priority décroissant."""
    from app.agent.skills import Skill, find_matching_skills

    s1 = Skill(name="low", priority=10, triggers=[__import__("re").compile(r"test")])
    s2 = Skill(name="high", priority=100, triggers=[__import__("re").compile(r"test")])
    s3 = Skill(name="mid", priority=50, triggers=[__import__("re").compile(r"test")])

    # Tri préalable (load_workspace_skills le fait)
    skills_sorted = sorted([s1, s2, s3], key=lambda s: s.priority, reverse=True)
    matched = find_matching_skills(skills_sorted, "ceci est un test")

    assert [s.name for s in matched] == ["high", "mid", "low"]
    print("OK tri par priority décroissante")


async def test_load_workspace_skills():
    """load_workspace_skills lit et parse les .md du workspace."""
    from app.agent.skills import load_workspace_skills
    _reset_cache()

    skills_dir = "rooms/!ROOM/.albert/skills"
    svc = _make_webdav_mock(
        file_contents={
            f"{skills_dir}/opah.md": SKILL_OPAH,
            f"{skills_dir}/plu.md": SKILL_PLU,
        },
        dir_listings={
            skills_dir: [
                f"{skills_dir}/opah.md",
                f"{skills_dir}/plu.md",
            ],
        },
    )

    skills = await load_workspace_skills(svc, "rooms/!ROOM")
    assert len(skills) == 2
    # Triées par priority desc : OPAH (50) avant PLU (30)
    assert skills[0].name == "instruction_opah"
    assert skills[1].name == "consultation_plu"
    print("OK chargement workspace skills")


async def test_workspace_skills_isolation():
    """Deux workspaces différents → caches de skills distincts."""
    from app.agent.skills import load_workspace_skills
    _reset_cache()

    svc = _make_webdav_mock(
        file_contents={
            "rooms/!A/.albert/skills/a.md": SKILL_OPAH,
            "rooms/!B/.albert/skills/b.md": SKILL_PLU,
        },
        dir_listings={
            "rooms/!A/.albert/skills": ["rooms/!A/.albert/skills/a.md"],
            "rooms/!B/.albert/skills": ["rooms/!B/.albert/skills/b.md"],
        },
    )

    skills_a = await load_workspace_skills(svc, "rooms/!A")
    skills_b = await load_workspace_skills(svc, "rooms/!B")

    names_a = {s.name for s in skills_a}
    names_b = {s.name for s in skills_b}
    assert names_a == {"instruction_opah"}
    assert names_b == {"consultation_plu"}
    print("OK isolation des skills entre workspaces")


async def test_skills_no_skills_dir():
    """Workspace sans skills/ → liste vide, pas d'erreur."""
    from app.agent.skills import load_workspace_skills
    _reset_cache()

    svc = _make_webdav_mock(file_contents={}, dir_listings={})
    skills = await load_workspace_skills(svc, "rooms/!EMPTY")
    assert skills == []
    print("OK workspace sans skills → vide")


async def test_skills_cache_mtime():
    """Deuxième appel rapide → utilise le cache."""
    from app.agent.skills import load_workspace_skills
    _reset_cache()

    call_count = {"list": 0}

    async def fake_list(directory):
        call_count["list"] += 1
        return ["rooms/!CACHE/.albert/skills/x.md"]

    async def fake_download(path):
        return SKILL_OPAH.encode("utf-8")

    svc = MagicMock()
    svc.list_documents = fake_list
    svc.download_file = fake_download

    # 1er appel
    s1 = await load_workspace_skills(svc, "rooms/!CACHE")
    assert call_count["list"] == 1

    # 2e appel immédiat → cache
    s2 = await load_workspace_skills(svc, "rooms/!CACHE")
    assert call_count["list"] == 1, f"list rappelé {call_count['list']} fois"
    assert len(s2) == len(s1)
    print("OK cache mtime opportuniste")


def test_skills_in_system_prompt():
    """active_skills sont injectées dans le system prompt."""
    from app.agent.prompt import build_system_prompt
    from app.agent.tools import ToolRegistry
    from app.agent.skills import Skill

    skill = Skill(
        name="test_skill",
        description="Test",
        priority=10,
        body="## Procédure test\n\nÉtape 1 : faire X.",
    )

    reg = ToolRegistry()
    sp = build_system_prompt(reg, active_skills=[skill])

    assert "Procédures applicables" in sp
    assert "test_skill" in sp
    assert "Étape 1 : faire X" in sp
    print("OK skills injectées dans system prompt")


def test_no_skills_no_section():
    """Pas de skills actives → pas de section dans le prompt."""
    from app.agent.prompt import build_system_prompt
    from app.agent.tools import ToolRegistry

    reg = ToolRegistry()
    sp = build_system_prompt(reg, active_skills=None)
    assert "Procédures applicables" not in sp
    print("OK absence de section skills si vide")


def main():
    print("=" * 60)
    print("Test skills .md — phase 3")
    print("=" * 60)

    sync_tests = [
        ("Parsing frontmatter + body", test_skill_parsing),
        ("Matching regex", test_skill_matching),
        ("Tolérance regex invalide", test_skill_invalid_regex_tolerance),
        ("Pas de frontmatter → None", test_skill_no_frontmatter),
        ("Tri par priority", test_find_matching_skills_priority),
        ("Skills dans system prompt", test_skills_in_system_prompt),
        ("Absence de section si vide", test_no_skills_no_section),
    ]

    async_tests = [
        ("Load workspace skills", test_load_workspace_skills),
        ("Isolation entre workspaces", test_workspace_skills_isolation),
        ("Workspace sans skills/", test_skills_no_skills_dir),
        ("Cache mtime", test_skills_cache_mtime),
    ]

    failed = 0
    for name, fn in sync_tests:
        print(f"\n[{name}]")
        try:
            fn()
        except AssertionError as e:
            print(f"FAIL : {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"ERREUR : {e}")
            traceback.print_exc()
            failed += 1

    for name, fn in async_tests:
        print(f"\n[{name}]")
        try:
            asyncio.run(fn())
        except AssertionError as e:
            print(f"FAIL : {e}")
            failed += 1
        except Exception as e:
            import traceback
            print(f"ERREUR : {e}")
            traceback.print_exc()
            failed += 1

    print()
    print("=" * 60)
    if failed == 0:
        print("Tous les tests skills passent.")
        return 0
    print(f"{failed} test(s) en échec.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
