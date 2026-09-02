"""
Les agents du pipeline doivent appeler un modèle que le fournisseur sert vraiment.

CE QUI A ÉTÉ TROUVÉ, le 01/09/2026, avant d'activer le pipeline sur `colaig-test`
--------------------------------------------------------------------------------
`config.py` fixait en dur, comme valeurs par défaut :

    albert_model_light  = "mistralai/Ministral-3-8B-Instruct-2512"    → l'Analyseur
    albert_model_medium = "mistralai/Mistral-Small-3.2-24B-..."       → le Synthétiseur

L'endpoint de production (SSPCloud) sert : `chandra-ocr-2`, `gemma4-26b-moe`,
`qwen3-6-35b-moe`, `qwen3-8-27b`, `qwen3-cursor`, `qwen3-embedding-8b`, `qwen3-vl`.
**Aucun Mistral.** Et le chart ne pose ni `ALBERT_MODEL_LIGHT` ni `ALBERT_MODEL_MEDIUM`.

Activer le pipeline aurait donc fait appeler, à chaque question, deux modèles
inexistants. Le pipeline n'a jamais pu fonctionner en service — ce qui explique
qu'il n'ait jamais été activé, sans que la raison soit écrite nulle part.

POURQUOI LA MESURE NE L'AVAIT PAS VU
-------------------------------------
`reference_pipeline.py` construit `Synthesiser(albert=llm, storage=...)` **sans**
passer `model=`. Le client choisit alors son modèle par défaut — celui de la
référence. Le harnais mesurait donc un montage que la production n'emprunte pas :
encore une mesure qui ne mesure pas ce qui tourne.

CE QUE FIXE CE TEST
--------------------
Un modèle non déclaré suit le modèle de chat **configuré**, quel qu'en soit le
fournisseur. C'est le principe provider-agnostic du dépôt : un nom de modèle d'un
fournisseur particulier n'a rien à faire dans un défaut. `CLAUDE.md` signale
justement ces résidus de la doctrine « Albert uniquement ».
"""
from __future__ import annotations

import pytest

from colaig.config import load_config


@pytest.fixture
def _sspcloud(monkeypatch):
    """L'instance `colaig-test` telle que son chart la décrit."""
    for nom in ("ALBERT_MODEL_LIGHT", "ALBERT_MODEL_MEDIUM"):
        monkeypatch.delenv(nom, raising=False)
    monkeypatch.setenv("LLM_BACKEND", "openai")
    monkeypatch.setenv("LLM_API_URL", "https://llm.lab.sspcloud.fr/api")
    monkeypatch.setenv("LLM_MODEL_CHAT", "qwen3-6-35b-moe")
    monkeypatch.setenv("ALBERT_MODEL_CHAT", "qwen3-6-35b-moe")


def test_le_synthetiseur_n_appelle_pas_un_modele_absent_du_fournisseur(_sspcloud):
    """Le Synthétiseur demandait un Mistral que l'endpoint ne sert pas."""
    cfg = load_config()
    assert "mistral" not in cfg.albert_model_medium.lower(), (
        "le Synthétiseur appellerait un modèle d'un autre fournisseur")
    assert cfg.albert_model_medium == "qwen3-6-35b-moe"


def test_l_analyseur_n_appelle_pas_un_modele_absent_du_fournisseur(_sspcloud):
    """Même défaut sur l'Analyseur, qui ouvre le pipeline : rien ne serait passé."""
    cfg = load_config()
    assert "ministral" not in cfg.albert_model_light.lower()
    assert cfg.albert_model_light == "qwen3-6-35b-moe"


def test_un_modele_explicitement_declare_reste_souverain(monkeypatch, _sspcloud):
    """Le repli ne doit pas écraser un choix délibéré.

    Un exploitant qui veut un modèle plus léger pour l'Analyseur doit pouvoir le
    dire — c'est tout l'intérêt d'avoir deux réglages distincts.
    """
    monkeypatch.setenv("ALBERT_MODEL_LIGHT", "qwen3-8-27b")
    cfg = load_config()
    assert cfg.albert_model_light == "qwen3-8-27b"
    assert cfg.albert_model_medium == "qwen3-6-35b-moe"
