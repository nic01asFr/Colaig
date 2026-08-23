"""
Contrat — une réponse tronquée ou vide ne passe plus en silence.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L1.5

Les quatre clients LLM faisaient `return data["choices"][0]["message"]["content"]`
sans regarder `finish_reason`. Avec un **modèle à raisonnement** — `qwen3-6-35b-moe`,
la cible de production D3 — le raisonnement consomme le même budget que la réponse.

Mesuré sur une question de rédaction avec six passages de contexte :

| `max_tokens` | `finish_reason` | raisonnement | réponse |
|---|---|---|---|
| 900 | `length` | 3 842 car. | **0 car.** |
| **2048** — défaut du Protocol | `length` | 6 532 car. | tronquée |
| 4000 | `stop` | 10 170 car. | 2 959 car. |

**3,4× plus de raisonnement que de réponse.** En dessous d'environ mille tokens,
l'utilisateur recevait une chaîne vide. Sans erreur, sans journal. Le service répondait
sans rien dire, et rien ne permettait de comprendre pourquoi.

Le défaut a été découvert en mesurant le palier génération de L1.5 : le rapport
annonçait « 0 citation attendue sur 37 » — un résultat trop propre pour être vrai, et
qui masquait des réponses vides.
"""
from __future__ import annotations

import logging

import pytest

from colaig.exceptions import LLMError
from colaig.utils.reponses_llm import extraire_contenu


def _reponse(contenu, finish="stop"):
    return {"choices": [{"message": {"content": contenu}, "finish_reason": finish}]}


def test_reponse_normale_passe():
    assert extraire_contenu(_reponse("Selon L2113-10, les marchés sont allotis."),
                            "SSPCloud", 2048) == "Selon L2113-10, les marchés sont allotis."


def test_reponse_vide_par_epuisement_leve():
    """Le cas mesuré à `max_tokens=900` : le raisonnement a tout consommé.

    Une chaîne vide remontée à l'utilisateur est le pire résultat : elle ressemble à
    une réponse. Une erreur explicite, elle, se diagnostique.
    """
    with pytest.raises(LLMError, match="budget de tokens épuisé"):
        extraire_contenu(_reponse("", finish="length"), "SSPCloud", 900)


def test_le_message_d_erreur_dit_quoi_faire():
    with pytest.raises(LLMError) as excinfo:
        extraire_contenu(_reponse("   \n  ", finish="length"), "SSPCloud", 900)
    message = str(excinfo.value)
    assert "max_tokens=900" in message, "le message doit rappeler la valeur en cause"
    assert "raisonnement" in message, "le message doit expliquer pourquoi le budget part"
    assert "augmenter" in message, "le message doit indiquer l'action"


def test_reponse_tronquee_est_rendue_mais_journalisee(caplog):
    """Une réponse partielle reste utile — mais l'exploitant doit le savoir."""
    with caplog.at_level(logging.WARNING, logger="colaig.utils.reponses_llm"):
        contenu = extraire_contenu(
            _reponse("Selon L2113-10, les marchés sont passés en lots sépar",
                     finish="length"),
            "SSPCloud", 2048,
        )
    assert contenu.startswith("Selon L2113-10")
    messages = [e.getMessage() for e in caplog.records]
    assert any("tronquée" in m and "2048" in m for m in messages), messages


def test_une_reponse_vide_sans_troncature_ne_leve_pas():
    """`finish_reason=stop` avec contenu vide est une réponse vide légitime.

    Rare, mais ce n'est pas le même défaut : ne pas le confondre avec l'épuisement du
    budget, sous peine de rendre l'erreur trompeuse.
    """
    assert extraire_contenu(_reponse(""), "SSPCloud", 2048) == ""


def test_content_absent_ou_null():
    """Certains modèles rendent `content: null` — notamment lors d'un appel d'outil."""
    assert extraire_contenu(_reponse(None), "SSPCloud", 2048) == ""
    assert extraire_contenu({"choices": [{"message": {}, "finish_reason": "stop"}]},
                            "SSPCloud", 2048) == ""


def test_reponse_malformee_leve_une_erreur_claire():
    with pytest.raises(LLMError, match="inattendue"):
        extraire_contenu({"choices": []}, "SSPCloud", 2048)
    with pytest.raises(LLMError, match="inattendue"):
        extraire_contenu({}, "SSPCloud", 2048)


def test_les_quatre_clients_passent_par_le_controle():
    """Régression : le contrôle doit rester branché partout.

    Il a été omis dans les quatre clients pendant toute la vie du projet. Rien
    n'empêche qu'un cinquième arrive sans lui — sinon ce test.
    """
    import pathlib

    racine = pathlib.Path(__file__).resolve().parent.parent
    clients = [
        "colaig/integrations/albert.py",
        "colaig/integrations/llm/openai_client.py",
        "colaig/integrations/llm/azure_client.py",
        "colaig/integrations/llm/ollama_client.py",
    ]
    fautifs = []
    for rel in clients:
        source = (racine / rel).read_text(encoding="utf-8")
        if "extraire_contenu" not in source:
            fautifs.append(f"  {rel} n'utilise pas extraire_contenu()")
        if '["message"]["content"]' in source:
            fautifs.append(f"  {rel} lit encore content sans contrôle de troncature")
    assert not fautifs, "\n".join(fautifs)
