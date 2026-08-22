"""
Contrat du lot L0.3 — doctrine LLM : multi-provider, et URL de base correctes.

STATUT: TESTE
VERSION: 2026-08-22 - v1.0
LOT: L0.3

Ces tests sont **statiques et hors ligne** : ils lisent les sources, ne joignent aucun
endpoint. Ils verrouillent deux invariants découverts à la mesure, pas supposés.

1. L'hôte `albert-api.etalab.gouv.fr` (avec un tiret) **ne résout pas**. Il figurait
   pourtant dans 20 endroits du dépôt, dont le défaut de `provider_registry.py` — le
   module que la doctrine désigne comme point d'entrée multi-provider. Un déploiement
   qui sélectionnait `albert` sans surcharger l'URL échouait donc en résolution DNS.
   L'hôte correct est `albert.api.etalab.gouv.fr` (avec un point).

2. Les clients construisent eux-mêmes `f"{base_url}/v1/chat/completions"`. Une URL de
   base **ne doit donc jamais** se terminer par `/v1` : cela produirait `/v1/v1/`.
   Cet invariant est explicité ici parce qu'il a déjà induit un diagnostic erroné en
   sens inverse — « il manque `/v1` à la configuration », ce qui aurait tout cassé.
"""
from __future__ import annotations

import pathlib

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent
HOTE_MORT = "albert-api.etalab.gouv.fr"
HOTE_VIVANT = "albert.api.etalab.gouv.fr"

EXTENSIONS = ("*.py", "*.md", "*.yml", "*.yaml", "*.example")
EXCLUS = {"_chantier", ".git", "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache"}
# `CLAUDE.v3-original.md` : archive volontairement conservée telle quelle, c'est un
# document historique. `test_doctrine_llm.py` : ce fichier-ci, qui cite forcément
# l'hôte fautif pour pouvoir le refuser.
FICHIERS_EXCLUS = {"CLAUDE.v3-original.md", "test_doctrine_llm.py"}


def _sources() -> list[pathlib.Path]:
    fichiers: list[pathlib.Path] = []
    for motif in EXTENSIONS:
        for f in RACINE.rglob(motif):
            if any(part in EXCLUS for part in f.parts):
                continue
            if f.name in FICHIERS_EXCLUS:
                continue
            fichiers.append(f)
    return sorted(fichiers)


def test_aucun_hote_albert_non_resolvant():
    """`albert-api.etalab.gouv.fr` ne résout pas — il ne doit apparaître nulle part."""
    fautifs = []
    for f in _sources():
        try:
            contenu = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):  # pragma: no cover
            continue
        if HOTE_MORT in contenu:
            for numero, ligne in enumerate(contenu.split("\n"), 1):
                if HOTE_MORT in ligne:
                    fautifs.append(f"  {f.relative_to(RACINE)}:{numero}")
    if fautifs:
        pytest.fail(
            f"Hôte `{HOTE_MORT}` (avec tiret) — il ne résout pas. "
            f"Utiliser `{HOTE_VIVANT}` (avec point) :\n" + "\n".join(fautifs)
        )


def test_url_de_base_sans_suffixe_v1():
    """Les clients ajoutent `/v1` : une URL de base qui le porte donne `/v1/v1/`."""
    from colaig.integrations.llm.provider_registry import _KNOWN_PROVIDERS

    for nom, url in _KNOWN_PROVIDERS.items():
        assert not url.rstrip("/").endswith("/v1"), (
            f"provider `{nom}` : l'URL de base {url!r} se termine par /v1. "
            "Les clients construisent eux-mêmes f'{base}/v1/chat/completions'."
        )


def test_registre_multi_provider():
    """La doctrine est multi-provider : le registre en porte plusieurs, pas un seul."""
    from colaig.integrations.llm.provider_registry import _KNOWN_PROVIDERS

    assert len(_KNOWN_PROVIDERS) > 1, "le registre doit rester multi-provider"
    assert "albert" in _KNOWN_PROVIDERS, "Albert reste un provider parmi d'autres"
    assert _KNOWN_PROVIDERS["albert"] == f"https://{HOTE_VIVANT}"


def test_config_defaut_albert_coherent_avec_le_registre():
    """Le défaut de `config.py` et celui du registre désignent le même endpoint."""
    from colaig.config import load_config  # noqa: F401  (import = fumée)
    from colaig.integrations.llm.provider_registry import _KNOWN_PROVIDERS

    source = (RACINE / "colaig" / "config.py").read_text(encoding="utf-8")
    assert f'"ALBERT_API_URL", "https://{HOTE_VIVANT}"' in source, (
        "le défaut d'ALBERT_API_URL dans config.py doit être "
        f"https://{HOTE_VIVANT}, sans /v1"
    )
    assert _KNOWN_PROVIDERS["albert"] == f"https://{HOTE_VIVANT}"
