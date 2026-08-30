"""
Colaig — la référence doit pouvoir mesurer la configuration de production.

Ce que le branchement du 30/08/2026 a montré
----------------------------------------------
Le harnais de référence L1.5 code en dur sa pile de recherche :

    MODELE_EMBED = "BAAI/bge-m3"
    DIMENSION    = 1024
    BASE_ALBERT  = "https://albert.api.etalab.gouv.fr/v1"

Or `CLAUDE.md` §3 pose que **la cible de production est SSPCloud**, dont le catalogue
— relevé le 30/08 — ne contient que sept modèles, et **aucun `bge-m3`** :

    chandra-ocr-2 · gemma4-26b-moe · qwen3-6-35b-moe · qwen3-8-27b
    qwen3-cursor · qwen3-embedding-8b · qwen3-vl

L'unique modèle d'embedding y rend **4096** dimensions, pas 1024.

**La configuration que la référence mesure ne peut donc pas exister sur la cible.** Le
rapport le déclare honnêtement en tête — ce n'est pas dissimulé — mais cela signifie que
la référence ne décrit pas le système déployable. C'est le même écart que celui trouvé
le matin même : le harnais passait `enable_thinking: false`, le produit non.

Ce que ce test tient
----------------------
Les trois valeurs se règlent par l'environnement, **avec les mêmes défauts qu'avant** —
aucune mesure existante n'est invalidée. Sans cela, comparer la production à la
référence resterait impossible.

Le test lit la source plutôt que d'importer le script : `reference_l15.py` s'exécute au
chargement (il lit le corpus, réclame une clé). Un test qui l'importerait mesurerait
l'environnement de la CI, pas le code.
"""

from __future__ import annotations

import pathlib
import re

import pytest

SOURCE = (pathlib.Path(__file__).resolve().parent.parent
          / "_chantier" / "scripts" / "reference_l15.py").read_text(encoding="utf-8")


@pytest.mark.parametrize(("constante", "variable", "defaut"), [
    ("MODELE_EMBED", "COLAIG_REF_EMBED_MODELE", '"BAAI/bge-m3"'),
    ("DIMENSION", "COLAIG_REF_EMBED_DIM", "1024"),
    ("BASE_EMBED", "COLAIG_REF_EMBED_BASE", '"https://albert.api.etalab.gouv.fr/v1"'),
])
def test_la_pile_de_recherche_est_reglable(constante, variable, defaut):
    """Sans réglage, la référence ne peut pas mesurer la cible de production."""
    motif = rf"^{constante}\s*=.*{re.escape(variable)}"
    assert re.search(motif, SOURCE, re.MULTILINE), (
        f"{constante} n'est pas réglable par {variable} : la référence reste enfermée "
        f"dans une pile que SSPCloud ne peut pas servir"
    )
    assert defaut in SOURCE, (
        f"le défaut {defaut} a disparu : les mesures antérieures deviendraient "
        f"incomparables sans que rien ne le signale"
    )


def test_le_cache_reste_indexe_sur_le_modele():
    """Un changement de modèle ne doit jamais être servi depuis le cache.

    Le fichier de cache porte le nom du modèle — c'est ce qui empêche de comparer des
    vecteurs de 1024 dimensions à des vecteurs de 4096 sans s'en apercevoir. Rendre le
    modèle configurable augmente la valeur de cette garde, il ne la supprime pas.
    """
    assert "cache-embeddings-{MODELE_EMBED" in SOURCE or (
        "MODELE_EMBED.replace" in SOURCE
    ), "le nom du cache ne dépend plus du modèle"


def test_le_rapport_declare_la_pile_mesuree():
    """Une référence qui ne dit pas ce qu'elle a mesuré n'est pas une référence."""
    assert "MODELE_EMBED" in SOURCE and "DIMENSION" in SOURCE
    assert re.search(r"Embeddings.*MODELE_EMBED.*DIMENSION", SOURCE), (
        "le rapport ne déclare plus le modèle et la dimension employés"
    )
