"""
Contrat — le vérificateur de fidélité, et ce qui se contrôle sans modèle.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L1.6

Pourquoi ce module existe
-------------------------
`verification_citations.py` contrôle la **provenance** : le numéro d'article cité
figure-t-il dans les passages ? Sa propre docstring dit ce qu'il ne sait pas faire —
juger si la réponse est **fidèle** au passage dont elle se réclame.

Le cas `mp-032` du jeu doré tient exactement dans cet écart. Le modèle répond à une
question dont la réponse précise n'est pas dans le corpus, en affirmant s'en tenir
strictement aux passages fournis. La provenance est correcte — `R2191-3` est bien là.
C'est l'**inférence** qui déborde. Aucun contrôle mécanique ne peut l'attraper : il
faut lire, et donc un modèle.

Ce que ce module reprend au poste de rédaction `Editeur`
--------------------------------------------------------
Deux choix, et ce sont eux qui font la valeur du dispositif :

1. **La recette de contexte est délibérément la plus pauvre du système** — une
   affirmation, un extrait, rien d'autre. Ni le nom du document, ni la page, ni
   l'autorité de la source. Un modèle à qui l'on dit que la source fait autorité
   trouve plus facilement qu'elle étaye. La pauvreté du contexte ferme ce biais par
   construction, là où une consigne ne ferait que le déconseiller.

2. **Le passage d'appui doit être une portion verbatim de l'extrait**, et le code le
   vérifie. C'est la seule part du verdict qui se contrôle **sans modèle** : si l'appui
   ne se trouve pas dans l'extrait, le vérificateur l'a fabriqué, et on le signale au
   lieu de le croire.

Ce que ces tests ne prouvent pas
---------------------------------
Ils fixent le comportement du **module**, pas la justesse des verdicts d'un modèle
réel. La doublure rend des verdicts scriptés ; ce qui est vérifié ici est que le
module lit, valide, contraint et signale correctement. La qualité des verdicts se
mesure sur le jeu doré, ailleurs.
"""
from __future__ import annotations

import json

import pytest

from colaig.rag.verificateur_fidelite import VERDICTS, verifier_fidelite

EXTRAIT = (
    "Article R2191-3. L'avance est versée au titulaire dans un délai de trente jours "
    "à compter de la date de notification de l'acte qui emporte commencement "
    "d'exécution du marché."
)


def _client(reponse: dict | str):
    """Doublure minimale : rend ce qu'on lui dit de rendre."""
    from tests.fakes import FakeLLM

    llm = FakeLLM()
    llm.chat_responses = [reponse if isinstance(reponse, str)
                          else json.dumps(reponse, ensure_ascii=False)]
    return llm


# ── Le verdict est fermé ────────────────────────────────────────────────────


@pytest.mark.parametrize("attendu", VERDICTS)
async def test_les_quatre_verdicts_sont_acceptes(attendu):
    d = await verifier_fidelite(
        "L'avance est versée sous trente jours.", EXTRAIT,
        _client({"verdict": attendu, "motif": "un motif suffisamment long pour être utile",
                 "passage_appui": "dans un délai de trente jours"}),
    )
    assert d.verdict == attendu


async def test_un_verdict_hors_liste_devient_illisible():
    """Hors des quatre valeurs, on ne devine pas ce que le modèle a voulu dire.

    Une sortie illisible est un **cinquième état**, pas un verdict. La rabattre sur
    « ne_dit_pas_cela » ferait passer une panne du vérificateur pour un jugement.
    """
    d = await verifier_fidelite("peu importe", EXTRAIT,
                                _client({"verdict": "plutôt oui", "motif": "..."}))
    assert d.verdict == "illisible"
    assert not d.exploitable


async def test_une_sortie_non_json_devient_illisible():
    d = await verifier_fidelite("peu importe", EXTRAIT, _client("je pense que oui"))
    assert d.verdict == "illisible"


# ── Ce qui se contrôle sans modèle ──────────────────────────────────────────


async def test_un_appui_verbatim_est_reconnu():
    d = await verifier_fidelite(
        "L'avance est versée sous trente jours.", EXTRAIT,
        _client({"verdict": "etaye", "motif": "le délai est énoncé tel quel",
                 "passage_appui": "dans un délai de trente jours"}),
    )
    assert d.appui_dans_extrait


async def test_un_appui_fabrique_est_signale():
    """Le seul contrôle du verdict qui ne dépend d'aucun modèle.

    Si l'appui ne se trouve pas dans l'extrait, le vérificateur l'a inventé. On ne
    le croit pas sur parole au motif qu'il est, lui, le contrôleur.
    """
    d = await verifier_fidelite(
        "L'avance est versée sous quinze jours.", EXTRAIT,
        _client({"verdict": "etaye", "motif": "le délai correspond",
                 "passage_appui": "dans un délai de quinze jours"}),
    )
    assert d.verdict == "etaye"
    assert not d.appui_dans_extrait
    assert not d.exploitable, "un verdict positif sans appui réel n'est pas exploitable"


async def test_l_appui_est_reconnu_malgre_la_mise_en_forme():
    """Espaces multiples, retours à la ligne, casse : ce sont des accidents de copie.

    Exiger l'égalité stricte ferait rejeter des appuis authentiques et rendrait le
    contrôle inutilisable — un garde-fou qui crie au loup trop souvent finit ignoré.
    """
    d = await verifier_fidelite(
        "L'avance est versée sous trente jours.", EXTRAIT,
        _client({"verdict": "etaye", "motif": "le délai est énoncé",
                 "passage_appui": "Dans un   délai\nde TRENTE jours"}),
    )
    assert d.appui_dans_extrait


# ── La recette de contexte ──────────────────────────────────────────────────


async def test_le_modele_ne_recoit_que_l_affirmation_et_l_extrait():
    """Le cœur du dispositif, et ce qui doit résister aux ajouts bien intentionnés.

    Ni nom de document, ni page, ni autorité, ni intention du livrable. Chaque ajout
    ouvre la porte à la complaisance. Ce test est là pour que l'ajout se voie.
    """
    from tests.fakes import FakeLLM

    llm = FakeLLM()
    llm.chat_responses = [json.dumps({"verdict": "etaye", "motif": "oui", "passage_appui": ""})]
    await verifier_fidelite("une affirmation", "un extrait", llm)

    envoye = " ".join(m["content"] for m in llm.appels_chat[-1])
    assert "une affirmation" in envoye and "un extrait" in envoye
    for interdit in ("page", "autorité", "document source", "fait autorité"):
        assert interdit not in envoye.lower(), (
            f"« {interdit} » a été transmis au vérificateur : la recette de contexte "
            "s'est enrichie, et avec elle le biais de complaisance"
        )


async def test_la_temperature_est_nulle():
    """Un contrôle qui varie d'une exécution à l'autre ne contrôle rien."""
    from tests.fakes import FakeLLM

    llm = FakeLLM()
    llm.chat_responses = [json.dumps({"verdict": "etaye", "motif": "oui", "passage_appui": ""})]
    await verifier_fidelite("a", "b", llm)
    assert llm.temperatures[-1] == 0


# ── Le garde-fou sait échouer ───────────────────────────────────────────────


async def test_on_ne_verifie_pas_dans_le_vide():
    with pytest.raises(ValueError):
        await verifier_fidelite("", EXTRAIT, _client({"verdict": "etaye"}))
    with pytest.raises(ValueError):
        await verifier_fidelite("une affirmation", "   ", _client({"verdict": "etaye"}))


async def test_exploitable_distingue_les_trois_situations():
    """Un verdict n'est exploitable que s'il est dans la liste **et** ancré.

    Sans cette distinction, l'appelant devrait recomposer la règle à chaque usage —
    et l'un d'eux l'oublierait.
    """
    ancre = {"verdict": "etaye", "motif": "m", "passage_appui": "délai de trente jours"}
    invente = {"verdict": "etaye", "motif": "m", "passage_appui": "délai de quinze jours"}
    negatif = {"verdict": "ne_dit_pas_cela", "motif": "m", "passage_appui": ""}

    assert (await verifier_fidelite("a", EXTRAIT, _client(ancre))).exploitable
    assert not (await verifier_fidelite("a", EXTRAIT, _client(invente))).exploitable
    assert (await verifier_fidelite("a", EXTRAIT, _client(negatif))).exploitable, (
        "un verdict négatif n'a pas besoin d'appui : l'absence est ce qu'il constate"
    )
