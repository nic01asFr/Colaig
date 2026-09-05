"""
Colaig — une page d'OCR coupee ne doit pas entrer entiere dans l'index.

RELEVE EN PRODUCTION, LE 30/08/2026
------------------------------------
Pendant l'indexation qui a fait passer le corpus de 52 a 60 documents :

    OpenAI : reponse tronquee (max_tokens=4096 atteint, 4130 caracteres rendus)
    OCR reussi pour /colaig-mesure-sst/debriefing.pdf (38916 caracteres)
    OpenAI : reponse tronquee (max_tokens=4096 atteint, 7717 caracteres rendus)
    OCR reussi pour /colaig-mesure-sst/perren_psychotraumatologie.pdf (21380 car.)

« OCR reussi » suit immediatement l'avertissement de troncature. **Le document est
indexe comme s'il etait complet**, amoute de ce qui depassait le budget. Une
question portant sur la fin d'une page recevra un refus, ou pire, une reponse
partielle presentee comme complete.

C'est le meme motif que les douze precedents de ce depot — une capacite qui
s'annonce accomplie alors qu'elle a fait la moitie du travail — a ceci pres qu'ici
l'avertissement EXISTAIT. Il ne nommait simplement pas le document, et se perdait
dans le flot d'une indexation de soixante fichiers.

CE QUI N'EST PAS FAIT ICI, ET POURQUOI
----------------------------------------
On n'augmente PAS `max_tokens` a une valeur choisie. Le catalogue de SSPCloud,
interroge le 30/08/2026, ne publie ni fenetre de contexte ni limite de sortie pour
`chandra-ocr-2` : y mettre 16384 serait inventer une donnee plausible, ce que le
CLAUDE.md racine interdit (§4.8). Et cela ne reglerait rien au fond — une page plus
dense franchirait la nouvelle limite comme elle a franchi l'ancienne.

LA PROPRIETE FIGEE ICI
------------------------
Une page coupee est **reprise** la ou elle s'est arretee, et concatenee. Aucune
limite du modele n'a besoin d'etre connue pour cela. Si la reprise n'aboutit pas
dans un nombre borne de tours, le document est nomme dans le journal — un exploitant
doit pouvoir savoir QUEL document est incomplet, pas seulement qu'un l'est.
"""

from __future__ import annotations

import pytest


def _client(**kwargs):
    from colaig.integrations.llm.openai_client import OpenAIClient

    return OpenAIClient(api_key="k", base_url="https://exemple.invalid",
                        model_ocr="chandra-ocr-2", **kwargs)


class _Reponse:
    """Une reponse de l'API, avec son `finish_reason`."""

    def __init__(self, contenu: str, raison: str = "stop"):
        self._c, self._r = contenu, raison

    def json(self):
        return {"choices": [{"message": {"content": self._c}, "finish_reason": self._r}]}


def _scripter(client, reponses):
    """Sert `reponses` dans l'ordre et journalise les charges utiles emises."""
    emises, restant = [], list(reponses)

    async def _appel(url, payload, timeout):
        emises.append(payload)
        return restant.pop(0) if restant else _Reponse("")

    client._request_with_retry = _appel
    return emises


# ─────────────────────────────────────────────────────────────────────────────
# La reprise
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_une_page_coupee_est_reprise():
    """LE defaut du 30/08. La fin de la page etait perdue en silence."""
    c = _client()
    _scripter(c, [_Reponse("debut de page", "length"),
                  _Reponse("et sa fin", "stop")])

    texte = await c.ocr(b"\x89PNG image", "page.png")

    assert "debut de page" in texte
    assert "et sa fin" in texte, "la suite de la page est perdue"


@pytest.mark.asyncio
async def test_la_reprise_montre_au_modele_ou_il_s_est_arrete():
    """Sans le deja-transcrit, le modele recommence la page au lieu de la finir."""
    c = _client()
    emises = _scripter(c, [_Reponse("premiere moitie", "length"),
                           _Reponse("seconde moitie", "stop")])

    await c.ocr(b"\x89PNG image", "page.png")

    assert len(emises) == 2, f"{len(emises)} requete(s), la reprise n'a pas eu lieu"
    suite = emises[1]["messages"]
    assert any(m.get("role") == "assistant" and "premiere moitie" in str(m.get("content"))
               for m in suite), "la reprise n'indique pas ce qui est deja transcrit"


@pytest.mark.asyncio
async def test_une_page_complete_n_est_pas_reprise():
    """Le cas courant ne doit rien couter de plus."""
    c = _client()
    emises = _scripter(c, [_Reponse("page entiere", "stop")])

    texte = await c.ocr(b"\x89PNG image", "page.png")

    assert texte == "page entiere"
    assert len(emises) == 1, "une requete de reprise inutile a ete emise"


@pytest.mark.asyncio
async def test_la_reprise_est_bornee(caplog):
    """Une page qui ne finit jamais ne doit pas boucler sans fin.

    La borne n'est pas une limite du modele — c'est un garde-fou de boucle. Ce qui
    compte est qu'au bout, **le document soit nomme** : c'est ce qui manquait a
    l'avertissement d'origine, invisible dans le flot de soixante fichiers.
    """
    import logging

    c = _client()
    emises = _scripter(c, [_Reponse(f"morceau {i}", "length") for i in range(20)])

    with caplog.at_level(logging.WARNING):
        texte = await c.ocr(b"\x89PNG image", "rapport-dense.png")

    assert len(emises) < 20, "la reprise n'est pas bornee"
    assert "morceau 0" in texte, "le transcrit partiel doit etre conserve"
    assert any("rapport-dense.png" in r.getMessage() for r in caplog.records), (
        "le document incomplet n'est pas nomme dans le journal"
    )


@pytest.mark.asyncio
async def test_chaque_page_d_un_pdf_est_reprise_pour_son_compte(monkeypatch):
    """Une page coupee ne doit pas decaler les suivantes."""
    from colaig.integrations.llm import openai_client as mod

    monkeypatch.setattr(mod, "_pdf_pages_to_png", lambda content, dpi=150: [b"p1", b"p2"])
    c = _client()
    _scripter(c, [_Reponse("p1 debut", "length"),
                  _Reponse("p1 fin", "stop"),
                  _Reponse("p2 entiere", "stop")])

    texte = await c.ocr(b"%PDF faux", "doc.pdf")

    for attendu in ("p1 debut", "p1 fin", "p2 entiere"):
        assert attendu in texte, f"« {attendu} » manque : {texte!r}"


# ─────────────────────────────────────────────────────────────────────────────
# CE QUE LA PREMIERE VERSION DE CE CORRECTIF FAISAIT DE FAUX
# ─────────────────────────────────────────────────────────────────────────────
#
# Mesure contre l'API reelle (chandra-ocr-2, SSPCloud), budget abaisse a 700 tokens
# pour forcer la troncature sur une page de 3646 caracteres :
#
#     appel 1 msg ->  1772 car., tronquee=True
#     appel 3 msg ->  2453 car., tronquee=True
#     appel 3 msg ->  2453 car., tronquee=True     <- identique au precedent
#     appel 3 msg ->  1772 car., tronquee=True     <- identique au premier
#     appel 3 msg ->  2453 car., tronquee=True
#     total : 10907
#
# Le modele NE CONTINUE PAS : il recommence la page. C'est comprehensible — il
# regarde l'image entiere a chaque appel, et « poursuis ou tu t'arretes » n'est pas
# un ordre qu'un modele de vision honore de facon fiable.
#
# La concatenation produisait donc 10907 caracteres pour une page qui en compte
# 3646 : du contenu TRIPLE dans l'index. C'est pire que la troncature d'origine, qui
# perdait du texte sans en inventer. Un chunk duplique remonte plusieurs fois dans
# une recherche et evince des passages pertinents.
#
# D'ou la regle ajoutee : une reprise qui REPETE est refusee, et on s'arrete la. La
# reprise reste utile pour un modele qui continue vraiment ; elle ne peut plus nuire
# quand le modele n'en fait rien.


@pytest.mark.asyncio
async def test_une_reprise_qui_repete_est_refusee(caplog):
    """LE defaut de ma premiere version, mesure contre l'API reelle."""
    import logging

    c = _client()
    _scripter(c, [_Reponse("le debut de la page", "length"),
                  _Reponse("le debut de la page", "length"),
                  _Reponse("le debut de la page", "length")])

    with caplog.at_level(logging.WARNING):
        texte = await c.ocr(b"\x89PNG image", "page-dense.png")

    assert texte.count("le debut de la page") == 1, (
        f"le texte est duplique dans l'index : {texte!r}"
    )
    assert any("page-dense.png" in r.getMessage() for r in caplog.records), (
        "une page restee incomplete doit nommer son document"
    )


@pytest.mark.asyncio
async def test_une_reprise_contenue_dans_le_deja_transcrit_est_refusee():
    """Variante : le modele rend un sous-ensemble de ce qu'on a deja."""
    c = _client()
    _scripter(c, [_Reponse("alpha beta gamma delta", "length"),
                  _Reponse("beta gamma", "length"),
                  _Reponse("encore autre chose", "stop")])

    texte = await c.ocr(b"\x89PNG image", "page.png")

    assert texte.count("beta gamma") == 1, f"fragment duplique : {texte!r}"


@pytest.mark.asyncio
async def test_une_vraie_continuation_est_toujours_acceptee():
    """Le garde-fou ne doit pas tuer le cas qu'on cherchait a traiter."""
    c = _client()
    _scripter(c, [_Reponse("premiere moitie du texte", "length"),
                  _Reponse("seconde moitie du texte", "stop")])

    texte = await c.ocr(b"\x89PNG image", "page.png")

    assert "premiere moitie du texte" in texte
    assert "seconde moitie du texte" in texte


@pytest.mark.asyncio
async def test_une_reprise_reussie_est_visible(caplog):
    """L'angle mort de ma premiere version : elle ne journalisait RIEN.

    L'ancien code loggait « reponse tronquee » via `extraire_contenu`. Le mien ne
    passe plus par la : j'avais donc supprime le signal sans le remplacer, et la
    campagne de validation ne pouvait pas conclure — c'est ce qui m'a fait sonder
    l'API a la main.
    """
    import logging

    c = _client()
    _scripter(c, [_Reponse("debut", "length"), _Reponse("suite", "stop")])

    with caplog.at_level(logging.INFO):
        await c.ocr(b"\x89PNG image", "rapport.png")

    assert any("rapport.png" in r.getMessage() for r in caplog.records), (
        "une reprise doit laisser une trace nommant le document"
    )


@pytest.mark.asyncio
async def test_une_reprise_qui_n_apporte_rien_ne_se_declare_pas_achevee(caplog):
    """Le dernier angle mort, releve en production le 30/08/2026.

    Le journal disait :

        OCR : debriefing.pdf page 3 depasse le budget, reprise 1/4
        OCR : debriefing.pdf page 3 reprise et achevee en 2 tour(s)

    et le document rendait **exactement** le meme nombre de caracteres qu'avant le
    correctif : 43592, au caractere pres, mesure dans l'index. La reprise n'avait
    rien rendu — le modele, invite a poursuivre, avait repondu du vide — et la page
    restait amputee. « Achevee » etait donc faux.

    C'est le meme motif que les quinze precedents de ce depot : un message qui
    declare le travail fait. Ici il etait de moi, et il masquait le cas ou la reprise
    echoue silencieusement.

    (A comparer avec `perren_psychotraumatologie.pdf`, ou la reprise a bien joue :
    24187 -> 30732 caracteres indexes, +6545 recuperes sur deux pages.)
    """
    import logging

    c = _client()
    _scripter(c, [_Reponse("le debut de la page", "length"),
                  _Reponse("", "stop")])

    with caplog.at_level(logging.INFO):
        texte = await c.ocr(b"\x89PNG image", "page-muette.png")

    assert texte == "le debut de la page"
    messages = [r.getMessage() for r in caplog.records]
    assert not any("achev" in m for m in messages), (
        f"une reprise sans apport se declare achevee : {messages}"
    )
    assert any("page-muette.png" in m and "amput" in m for m in messages), (
        f"la page reste amputee sans que le journal le dise : {messages}"
    )


@pytest.mark.asyncio
async def test_une_reprise_qui_apporte_dit_combien(caplog):
    """Le pendant : quand elle joue, on doit pouvoir le mesurer dans le journal."""
    import logging

    c = _client()
    _scripter(c, [_Reponse("premiere moitie", "length"),
                  _Reponse("seconde moitie du texte", "stop")])

    with caplog.at_level(logging.INFO):
        await c.ocr(b"\x89PNG image", "page.png")

    assert any("achev" in r.getMessage() for r in caplog.records)
