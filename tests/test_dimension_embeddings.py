"""
Colaig — la dimension des vecteurs doit suivre le modèle d'embedding.

Ce que le branchement du 29/08/2026 a montré
----------------------------------------------
Le modèle d'embedding est configurable (`LLM_MODEL_EMBED`). **Sa dimension ne l'était
pas** : `main.py` écrivait `dimension=1024` en cinq endroits.

Le catalogue de SSPCloud ne propose que `qwen3-embedding-8b`, qui rend des vecteurs de
**4096**. Chaque document a donc échoué à l'indexation sur :

    AssertionError()

Une assertion **nue**, levée par FAISS au moment d'ajouter un vecteur à un index d'une
autre dimension. Aucun message, aucun nom de modèle, aucun chiffre : rien qui permette
de comprendre ce qui ne va pas. Les 63 documents du corpus ont échoué en silence, un
par un, et le journal ne disait que « erreur ré-indexation ».

Deux exigences, et la seconde compte autant que la première
-------------------------------------------------------------
1. **La dimension se configure**, sinon changer de fournisseur oblige à modifier le
   code — ce qui contredit le principe « provider-agnostic » du chantier.
2. **Une incohérence se dit.** Un défaut de configuration doit produire un message qui
   nomme le modèle et les deux dimensions, pas une assertion vide. C'est la différence
   entre cinq minutes et une heure de diagnostic.
"""

from __future__ import annotations

import pathlib
import re

import pytest

RACINE = pathlib.Path(__file__).resolve().parent.parent


# ─────────────────────────────────────────────────────────────────────────────
# 1. La dimension se configure
# ─────────────────────────────────────────────────────────────────────────────


def test_la_dimension_se_lit_dans_l_environnement(monkeypatch):
    from colaig.config import load_config

    monkeypatch.setenv("COLAIG_EMBEDDING_DIM", "4096")
    assert load_config().embedding_dimension == 4096


def test_la_dimension_par_defaut_reste_1024(monkeypatch):
    """Ne pas casser les instances existantes, dont l'index est en 1024."""
    from colaig.config import load_config

    monkeypatch.delenv("COLAIG_EMBEDDING_DIM", raising=False)
    assert load_config().embedding_dimension == 1024


def test_plus_aucune_dimension_codee_en_dur():
    """`main.py` ne doit plus décider de la dimension à la place de la configuration.

    Cinq occurrences de `dimension=1024` y vivaient. En laisser une seule suffit à
    reproduire le défaut sur le chemin qu'elle gouverne.
    """
    source = (RACINE / "colaig" / "main.py").read_text(encoding="utf-8")
    fautifs = re.findall(r"dimension\s*=\s*\d+", source)
    assert fautifs == [], f"dimension codée en dur dans main.py : {fautifs}"


# ─────────────────────────────────────────────────────────────────────────────
# 2. Une incohérence se dit
# ─────────────────────────────────────────────────────────────────────────────


class _LLMQuiRendUneAutreDimension:
    """Un fournisseur dont le modèle ne rend pas la dimension attendue."""

    def __init__(self, taille: int = 4096) -> None:
        self._taille = taille
        self.model_embed = "qwen3-embedding-8b"

    async def embed(self, text: str):
        return [0.1] * self._taille

    async def embed_batch(self, texts: list[str]):
        return [[0.1] * self._taille for _ in texts]


@pytest.mark.asyncio
async def test_une_dimension_inattendue_est_nommee():
    """Le message doit porter les deux chiffres — sans quoi on cherche à l'aveugle."""
    from colaig.exceptions import EmbeddingError
    from colaig.rag.embeddings import EmbeddingService

    service = EmbeddingService(_LLMQuiRendUneAutreDimension(4096), dimension=1024)

    with pytest.raises(EmbeddingError) as erreur:
        await service.embed_text("un texte")

    message = str(erreur.value)
    assert "4096" in message, "la dimension reçue doit être nommée"
    assert "1024" in message, "la dimension attendue doit être nommée"


@pytest.mark.asyncio
async def test_la_bonne_dimension_ne_declenche_rien():
    from colaig.rag.embeddings import EmbeddingService

    service = EmbeddingService(_LLMQuiRendUneAutreDimension(1024), dimension=1024)
    vecteur = await service.embed_text("un texte")

    assert len(vecteur) == 1024
