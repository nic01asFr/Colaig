"""
Colaig — les sources sont numerotees, une seule fois, a la fin.

CE QUE CELA REMPLACE
----------------------
Le modele ecrit ses citations en clair, dans le fil du texte :

    Un debriefing est organise [AccEvtGrave Support participants  septembre  2024.pdf]
    puis un suivi est propose [AccEvtGrave Support participants  septembre  2024.pdf].

Le meme nom, deux fois, cinquante caracteres chacun, au milieu d'une phrase. Rendu :

    Un debriefing est organise¹ puis un suivi est propose¹.

    ¹ AccEvtGrave Support participants  septembre  2024.pdf

LES TROIS CONTRAINTES QUI DECIDENT DE LA CONCEPTION
-----------------------------------------------------
**1. Le modele continue d'ecrire `[nom.pdf]`.** `citation_checker` s'ancre sur cette
forme pour verifier qu'une source citee a bien ete fournie. Si le modele produisait
directement des exposants, l'audit anti-hallucination perdrait sa prise. La
numerotation est donc faite par le SYSTEME, **apres** le controle — c'est D66 :
« la presentation appartient au systeme, pas au modele ».

**2. L'historique garde la forme brute.** `_save_history` enregistre le texte envoye ;
y mettre des exposants ferait recopier des exposants par le modele au tour suivant, et
le verificateur n'aurait plus rien a verifier. **C'est exactement le defaut des emojis
de gestes**, corrige le 30/08/2026 : « le modele recopiait les gestes depuis son propre
historique ». On ne le reproduit pas.

**3. Une citation sans source ne recoit pas de numero.** Lui en donner un la
maquillerait en reference legitime — un nom invente prendrait l'apparence d'un document
verifie. Elle reste visible telle quelle, et le journal continue de la signaler.
"""

from __future__ import annotations

from colaig.messaging.sources_numerotees import numeroter_les_sources

SOURCE = "AccEvtGrave Support participants  septembre  2024.pdf"


# ─────────────────────────────────────────────────────────────────────────────
# La forme rendue
# ─────────────────────────────────────────────────────────────────────────────


def test_une_source_citee_deux_fois_ne_porte_qu_un_numero():
    texte = f"Un debriefing est organise [{SOURCE}] puis un suivi est propose [{SOURCE}]."

    rendu = numeroter_les_sources(texte, [SOURCE])
    corps = rendu.split("\n\n")[0]

    assert corps.count("¹") == 2, f"deux appels attendus : {corps!r}"
    assert "organise¹ puis" in corps, (
        f"l'appel de note doit s'attacher au mot, sans espace : {corps!r}")
    assert rendu.count(SOURCE) == 1, "la source doit n'apparaitre qu'une fois, a la fin"
    assert rendu.rstrip().endswith(f"¹ {SOURCE}")


def test_les_numeros_suivent_l_ordre_d_apparition():
    texte = "D'abord [b.pdf], ensuite [a.pdf], puis encore [b.pdf]."

    rendu = numeroter_les_sources(texte, ["a.pdf", "b.pdf"])

    corps, _, notes = rendu.partition("\n\n")
    assert corps == "D'abord¹, ensuite², puis encore¹."
    assert notes.index("¹ b.pdf") < notes.index("² a.pdf")


def test_sans_citation_le_texte_ne_bouge_pas():
    """Une salutation ne doit pas se voir affubler d'un bloc de notes vide."""
    texte = "Bonjour, en quoi puis-je aider ?"

    assert numeroter_les_sources(texte, ["a.pdf"]) == texte


def test_le_nom_affiche_est_celui_de_la_source_pas_celui_ecrit_par_le_modele():
    """Le modele normalise les espaces en redigeant ; la note doit porter le vrai nom.

    C'est le pendant de la correction du 30/08 sur `citation_checker._norm` : les deux
    formes designent le meme document, et c'est celle du STOCKAGE qui fait foi.
    """
    ecrit_par_le_modele = "AccEvtGrave Support participants septembre 2024.pdf"
    texte = f"Voir [{ecrit_par_le_modele}]."

    rendu = numeroter_les_sources(texte, [SOURCE])

    assert SOURCE in rendu, "la note doit porter le nom reel du document"
    assert ecrit_par_le_modele not in rendu.split("¹")[0]


def test_un_chemin_est_affiche_par_son_nom_de_fichier():
    texte = "Voir [/espace/dossier/rapport.pdf]."

    rendu = numeroter_les_sources(texte, ["/espace/dossier/rapport.pdf"])

    assert "¹ rapport.pdf" in rendu
    assert "/espace/dossier/" not in rendu, "le chemin complet encombre la note"


# ─────────────────────────────────────────────────────────────────────────────
# Ce qui ne doit PAS etre numerote
# ─────────────────────────────────────────────────────────────────────────────


def test_une_citation_sans_source_ne_recoit_pas_de_numero():
    """Contrainte 3. Numeroter un nom invente le maquillerait en reference verifiee."""
    texte = "Voir [rapport-inexistant.pdf] et [a.pdf]."

    rendu = numeroter_les_sources(texte, ["a.pdf"])

    assert "[rapport-inexistant.pdf]" in rendu, (
        "une citation sans source doit rester visible telle quelle"
    )
    assert "rapport-inexistant" not in rendu.split("\n\n")[-1], (
        "elle ne doit pas figurer dans les notes"
    )


def test_les_espaces_reserves_ne_sont_pas_numerotes():
    """« [nom de l'espace] » dans une consigne n'est pas une citation.

    Meme critere que `citation_checker._looks_like_ref`, pour la meme raison : le
    30/08, quatre crochets ecrits par Colaig dans ses propres consignes avaient ete
    relus comme des citations sans source.
    """
    texte = "Ecris : !space create [nom de l'espace], puis consulte [a.pdf]."

    rendu = numeroter_les_sources(texte, ["a.pdf"])

    corps = rendu.split("\n\n")[0]
    assert "[nom de l'espace]" in corps
    assert corps.count("¹") == 1


# ─────────────────────────────────────────────────────────────────────────────
# Le piege que ce lot ne doit pas reproduire
# ─────────────────────────────────────────────────────────────────────────────


def test_la_fonction_ne_modifie_pas_son_entree():
    """L'historique doit garder `[nom.pdf]`.

    Si le texte numerote remontait dans `_save_history`, le modele recopierait des
    numeros au tour suivant et `citation_checker` n'aurait plus d'ancrage — le defaut
    exact des emojis de gestes, corrige le meme jour.
    """
    texte = f"Voir [{SOURCE}]."
    copie = str(texte)

    numeroter_les_sources(texte, [SOURCE])

    assert texte == copie


# ─────────────────────────────────────────────────────────────────────────────
# LE BRANCHEMENT — c'est là, et non dans la fonction, qu'est le piège
# ─────────────────────────────────────────────────────────────────────────────
#
# La fonction pure ne prouve rien du câblage. Ce qui doit être établi :
#
#   - ce qui PART vers Tchap porte les exposants ;
#   - ce qui ENTRE dans l'historique garde `[nom.pdf]`.
#
# Si l'historique portait les exposants, le modèle en recopierait au tour suivant et
# `citation_checker` n'aurait plus d'ancrage. C'est le défaut des emojis de gestes,
# corrigé le 30/08/2026 dans le même fichier, deux lignes au-dessus du branchement.

import pytest

from colaig.messaging.handlers import MessageHandler
from colaig.models import GeneratedResponse, IncomingMessage

from tests.test_handlers import MockResolver, MockRetriever


class _Generateur:
    """Rend une réponse qui cite deux fois la même source, comme le modèle réel."""

    async def generate(self, *a, **k):
        return GeneratedResponse(
            text=("Un debriefing est organise [rapport annuel.pdf] "
                  "puis un suivi est propose [rapport annuel.pdf]."),
            sources=["/espace/rapport annuel.pdf"],
            confidence=0.8,
        )


class _MessagingCapteur:
    """Retient ce qui part réellement vers le salon."""

    def __init__(self):
        self.envois: list[str] = []

    async def send(self, conversation_id, text, **k):
        self.envois.append(text)
        return "$evt"

    async def send_typing(self, conversation_id, typing):
        return None

    async def connect(self):
        return None

    async def run(self):
        return None

    def on_message(self, callback):
        return None


@pytest.mark.asyncio
async def test_tchap_recoit_les_exposants(mock_storage, monkeypatch):
    """Ce qui part vers le salon porte les appels de note et la liste des sources."""
    messaging = _MessagingCapteur()
    handler = MessageHandler(messaging, MockResolver(), MockRetriever(),
                             _Generateur(), mock_storage)

    await handler.handle_message(IncomingMessage(
        message_id="$1", conversation_id="!salon:test.local",
        user_id="@u:test.local", body="que faire apres un evenement grave ?"))

    assert messaging.envois, "rien n'a ete envoye"
    envoye = messaging.envois[0]
    corps = envoye.split("\n\n")[0]

    assert corps.count("\u00b9") == 2, f"deux appels de note attendus : {corps!r}"
    assert "[rapport annuel.pdf]" not in envoye, "la citation brute part encore"
    assert envoye.rstrip().endswith("\u00b9 rapport annuel.pdf")


@pytest.mark.asyncio
async def test_l_historique_garde_la_forme_brute(mock_storage, monkeypatch):
    """LE piège. Des exposants en historique se recopieraient au tour suivant."""
    enregistres: list[list[dict]] = []

    async def _capter(storage, workspace_path, conversation_id, messages):
        enregistres.append(messages)

    monkeypatch.setattr("colaig.messaging.handlers.save_conversation_history", _capter)

    handler = MessageHandler(_MessagingCapteur(), MockResolver(), MockRetriever(),
                             _Generateur(), mock_storage)
    await handler.handle_message(IncomingMessage(
        message_id="$1", conversation_id="!salon:test.local",
        user_id="@u:test.local", body="question"))

    assert enregistres, "aucun historique enregistre"
    reponses = [m["content"] for tour in enregistres for m in tour
                if m.get("role") == "assistant"]
    assert reponses, "la reponse n'est pas dans l'historique"
    for r in reponses:
        assert "\u00b9" not in r, (
            f"l'historique porte des exposants : le modele les recopiera et "
            f"`citation_checker` perdra son ancrage — {r!r}"
        )
        assert "[rapport annuel.pdf]" in r, (
            f"l'historique a perdu la forme que le verificateur exige — {r!r}"
        )
