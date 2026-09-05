"""
Colaig — les jetons de raisonnement ne doivent pas manger la réponse
(campagne d'usage réel du 30/08/2026).

Ce que la campagne a montré
-----------------------------
Une fois le corpus enfin transmis au Synthétiseur — cinq passages au lieu de zéro —
la génération a échoué :

    GenerationError: OpenAI : réponse vide, budget de tokens épuisé (max_tokens=2048).
    Un modèle à raisonnement peut consommer tout le budget avant d'émettre sa réponse.

Le message de diagnostic était juste et précis. Le comportement, non : Colaig ne
répondait pas du tout.

Le point qui fait mal
----------------------
**Le dépôt connaissait déjà ce piège, et le corrigeait — mais seulement dans son
harnais de mesure.** `_chantier/scripts/mesure_ancre_empoisonnee.py` porte ce
commentaire :

    SANS CECI, LA MESURE EST VIDE. Le modele emet des jetons de raisonnement qui
    consomment le budget, et `content` revient vide.
    […] Tous les autres harnais de ce dossier passent deja ce parametre.

Le paramètre en question — `chat_template_kwargs: {"enable_thinking": false}` —
n'apparaissait **nulle part** dans `colaig/`. Les mesures étaient donc faites sur une
configuration que le produit n'avait pas : elles ne mesuraient pas le produit.

Le choix
----------
Le défaut est **raisonnement désactivé**. Un modèle qui réfléchit mieux mais ne répond
pas vaut moins qu'un modèle qui répond. Le réglage reste ouvert par
`COLAIG_LLM_THINKING=true`, pour une instance dont le budget de jetons le permet.
"""

from __future__ import annotations

import pytest


def _charges_capturees(client):
    """Remplace l'envoi réseau par une capture des charges utiles."""
    captees = []

    class _Reponse:
        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "ok"}}]}

    async def _capter(url, payload, timeout):
        captees.append(payload)
        return _Reponse()

    client._request_with_retry = _capter
    return captees


def _client(**kwargs):
    from colaig.integrations.llm.openai_client import OpenAIClient

    return OpenAIClient(api_key="k", base_url="https://exemple.invalid",
                        model_chat="qwen3-6-35b-moe", **kwargs)


@pytest.mark.asyncio
async def test_le_raisonnement_est_desactive_par_defaut():
    """LE défaut du 30/08 : la réponse revenait vide, budget épuisé."""
    c = _client()
    charges = _charges_capturees(c)

    await c.chat([{"role": "user", "content": "bonjour"}])

    assert charges, "aucune requête émise"
    assert charges[0].get("chat_template_kwargs") == {"enable_thinking": False}, (
        "sans ce paramètre, un modèle à raisonnement consomme tout le budget avant "
        "d'émettre sa réponse, et Colaig ne répond pas"
    )


@pytest.mark.asyncio
async def test_le_raisonnement_peut_etre_reactive():
    """Le réglage reste ouvert pour une instance qui a le budget."""
    c = _client(enable_thinking=True)
    charges = _charges_capturees(c)

    await c.chat([{"role": "user", "content": "bonjour"}])

    assert "chat_template_kwargs" not in charges[0], (
        "réactivé, le paramètre ne doit pas être envoyé du tout — c'est le défaut du "
        "fournisseur qui reprend la main"
    )


@pytest.mark.asyncio
async def test_le_chemin_avec_outils_est_couvert_aussi():
    """`chat_with_tools` est le chemin de l'Orchestrateur.

    Ne corriger que `chat` laisserait le défaut sur la moitié du pipeline — et sur
    celle qui décide des actions.
    """
    c = _client()
    captees = []

    class _Reponse:
        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "ok", "tool_calls": []}}]}

    async def _capter(url, payload, timeout):
        captees.append(payload)
        return _Reponse()

    c._request_with_retry = _capter
    await c.chat_with_tools([{"role": "user", "content": "bonjour"}], tools=[])

    assert captees[0].get("chat_template_kwargs") == {"enable_thinking": False}


def test_la_configuration_expose_le_reglage(monkeypatch):
    from colaig.config import load_config

    monkeypatch.delenv("COLAIG_LLM_THINKING", raising=False)
    assert load_config().llm_enable_thinking is False

    monkeypatch.setenv("COLAIG_LLM_THINKING", "true")
    assert load_config().llm_enable_thinking is True
