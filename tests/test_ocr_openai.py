"""
Colaig — l'OCR sur le fournisseur de production.

Ce que la campagne du 30/08/2026 a montré
-------------------------------------------
Sur les 59 documents du corpus déposé, **sept restent invisibles** — des PDF scannés,
sans texte natif. Colaig le dit lui-même à chaque indexation :

    document non indexé (document sans texte natif —
    le backend LLM (OpenAIClient) ne fournit pas la capacité « ocr »)

Le message est juste : `AlbertClient` sait faire l'OCR, `OpenAIClient` non — et c'est
lui qui tourne. **Or le catalogue de SSPCloud contient `chandra-ocr-2`**, relevé le
30/08. La capacité existe des deux côtés ; personne ne les avait reliées.

LE PIÈGE À NE PAS REPRODUIRE
------------------------------
`supporte(client, "ocr")` teste `callable(getattr(client, "ocr", None))`. **Ajouter la
méthode sans condition annoncerait donc la capacité même sans modèle configuré** — et
l'indexation échouerait à chaud au lieu de sauter le document proprement.

Ce serait la treizième « capacité déclarée qui ne fait rien » de ce dépôt, et la
première que j'aurais introduite en croyant en corriger une.

D'où la règle que ces tests figent : **sans modèle d'OCR configuré, la capacité est
honnêtement absente.** `self.ocr = None` le dit à `supporte()` dans son propre langage.
"""

from __future__ import annotations

import pytest

from colaig.integrations.llm.capabilities import supporte


def _client(**kwargs):
    from colaig.integrations.llm.openai_client import OpenAIClient

    return OpenAIClient(api_key="k", base_url="https://exemple.invalid", **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# La capacité n'est annoncée que si elle existe
# ─────────────────────────────────────────────────────────────────────────────


def test_sans_modele_la_capacite_est_absente():
    """LE piège. Annoncer l'OCR sans modèle ferait échouer l'indexation à chaud."""
    assert supporte(_client(), "ocr") is False, (
        "la capacité « ocr » est annoncée sans modèle configuré : l'indexeur "
        "l'appellera et échouera au lieu de sauter le document"
    )


def test_avec_un_modele_la_capacite_est_annoncee():
    assert supporte(_client(model_ocr="chandra-ocr-2"), "ocr") is True


def test_le_motif_d_absence_reste_lisible():
    """Le message qui a permis de trouver ce défaut doit rester juste."""
    from colaig.integrations.llm.capabilities import motif_absence

    motif = motif_absence(_client(), "ocr")
    assert "ocr" in motif.lower()


# ─────────────────────────────────────────────────────────────────────────────
# L'appel lui-même
# ─────────────────────────────────────────────────────────────────────────────


def _capture(client):
    captees = []

    class _Reponse:
        @staticmethod
        def json():
            return {"choices": [{"message": {"content": "texte de la page"}}]}

    async def _capter(url, payload, timeout):
        captees.append(payload)
        return _Reponse()

    client._request_with_retry = _capter
    return captees


@pytest.mark.asyncio
async def test_une_image_part_en_vision_multimodale(monkeypatch):
    """Le format attendu par un modèle de vision : texte + `image_url` en data URL."""
    c = _client(model_ocr="chandra-ocr-2")
    charges = _capture(c)

    texte = await c.ocr(b"\x89PNG\r\n\x1a\n fausse image", "plan.png")

    assert texte == "texte de la page"
    assert charges, "aucune requête émise"
    contenu = charges[0]["messages"][0]["content"]
    types = [p["type"] for p in contenu]
    assert types == ["text", "image_url"], f"format inattendu : {types}"
    assert contenu[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert charges[0]["model"] == "chandra-ocr-2"


@pytest.mark.asyncio
async def test_un_pdf_est_envoye_page_par_page(monkeypatch):
    """Une requête par page — c'est ce qui évite le délai d'attente sur un gros PDF.

    La conversion est simulée : ce test porte sur le découpage en requêtes, pas sur
    pymupdf, qui a son propre contrat.
    """
    from colaig.integrations.llm import openai_client as mod

    monkeypatch.setattr(mod, "_pdf_pages_to_png",
                        lambda content, dpi=150: [b"page1", b"page2", b"page3"])
    c = _client(model_ocr="chandra-ocr-2")
    charges = _capture(c)

    texte = await c.ocr(b"%PDF-1.4 faux", "fiche.pdf")

    assert len(charges) == 3, f"{len(charges)} requête(s) pour 3 pages"
    assert texte.count("texte de la page") == 3


@pytest.mark.asyncio
async def test_un_pdf_illisible_le_dit(monkeypatch):
    """Sans pymupdf, la conversion rend une liste vide.

    Rendre une chaîne vide ferait indexer un document sans contenu — pire que de ne
    pas l'indexer, car il occuperait une place et répondrait du vide.
    """
    from colaig.exceptions import LLMUnavailableError
    from colaig.integrations.llm import openai_client as mod

    monkeypatch.setattr(mod, "_pdf_pages_to_png", lambda content, dpi=150: [])
    c = _client(model_ocr="chandra-ocr-2")

    with pytest.raises(LLMUnavailableError):
        await c.ocr(b"%PDF-1.4 faux", "fiche.pdf")
