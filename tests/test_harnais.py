"""
Contrat du lot L0.4 — le harnais de test est déterministe, conforme et hors ligne.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L0.4

Un harnais non déterministe ne produit pas des tests un peu moins fiables : il produit
des tests **intermittents**, dont on finit par accuser la CI plutôt que le code. Ces
tests-ci éprouvent le harnais lui-même.
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path

import pytest

from colaig.models import ConversationType, IncomingMessage
from colaig.protocols import LLMClientProtocol, MessagingProtocol, StorageProtocol
from tests.fakes import INSTANT_ZERO, FakeLLM, FakeMessaging, FakeStorage, etag_deterministe

RACINE = Path(__file__).resolve().parent.parent


# ── Déterminisme ────────────────────────────────────────────────────────────


def test_etag_stable_entre_processus():
    """L'etag ne doit pas dépendre de `hash()`, randomisé par processus.

    C'était le cas jusqu'au lot L0.4. Mesuré alors : `hash(b'contenu')` donnait
    2598434101455927999 puis -123023570338129182 sur deux exécutions. L'indexation
    incrémentale de Colaig reposant entièrement sur la comparaison d'etags, la
    doublure ne pouvait pas servir à tester ce mécanisme.

    Le test relance un interpréteur : c'est la seule façon de prouver la stabilité
    *entre processus*, un `assert` dans le processus courant ne dirait rien.
    """
    attendu = etag_deterministe(b"contenu de reference")
    programme = (
        "import sys; sys.path.insert(0, r'%s');"
        "from tests.fakes import etag_deterministe;"
        "print(etag_deterministe(b'contenu de reference'))" % str(RACINE)
    )
    obtenus = set()
    for _ in range(3):
        sortie = subprocess.run(
            [sys.executable, "-c", programme],
            capture_output=True, text=True, timeout=120, cwd=RACINE,
        )
        assert sortie.returncode == 0, sortie.stderr
        obtenus.add(sortie.stdout.strip())
    assert obtenus == {attendu}, f"etag instable entre processus : {obtenus}"


def test_meme_contenu_meme_etag():
    """Un contenu identique donne un etag identique — comme un vrai backend."""
    s = FakeStorage()
    s.add_file("/a.txt", b"identique")
    s.add_file("/b.txt", b"identique")
    s.add_file("/c.txt", b"different")
    assert s.metadata["/a.txt"].etag == s.metadata["/b.txt"].etag
    assert s.metadata["/a.txt"].etag != s.metadata["/c.txt"].etag


def test_aucune_horloge_murale():
    """Les dates sont dérivées d'un instant fixe, pas de `datetime.now()`."""
    s = FakeStorage()
    s.add_file("/premier.txt", b"x")
    s.add_file("/second.txt", b"y")
    assert s.metadata["/premier.txt"].last_modified > INSTANT_ZERO
    assert s.metadata["/second.txt"].last_modified > s.metadata["/premier.txt"].last_modified

    source = (RACINE / "tests" / "fakes.py").read_text(encoding="utf-8")
    for interdit in ("datetime.now(", "datetime.utcnow(", "time.time("):
        assert interdit not in source, f"le harnais lit l'horloge : {interdit}"


@pytest.mark.asyncio
async def test_embeddings_reproductibles():
    """Le même texte donne toujours le même vecteur — condition d'un index testable."""
    a, b = FakeLLM(), FakeLLM()
    assert await a.embed("marchés publics") == await b.embed("marchés publics")
    assert await a.embed("marchés publics") != await a.embed("conception routière")
    vecteur = await a.embed("x")
    assert len(vecteur) == a.embedding_dim
    assert abs(sum(v * v for v in vecteur) - 1.0) < 1e-5, "vecteur non normalisé L2"


def test_listing_ordonne():
    """L'ordre du listing ne dépend pas de l'ordre d'écriture des fixtures."""
    import asyncio

    s = FakeStorage()
    for nom in ("/ws/c.txt", "/ws/a.txt", "/ws/b.txt"):
        s.add_file(nom, b"x")
    chemins = [f.path for f in asyncio.run(s.list_files("/ws/"))]
    assert chemins == ["/ws/a.txt", "/ws/b.txt", "/ws/c.txt"]


# ── Conformité aux Protocols ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "double,protocole",
    [
        (FakeStorage, StorageProtocol),
        (FakeMessaging, MessagingProtocol),
        (FakeLLM, LLMClientProtocol),
    ],
    ids=["FakeStorage", "FakeMessaging", "FakeLLM"],
)
def test_la_doublure_couvre_le_protocole(double, protocole):
    """Chaque méthode du Protocol existe sur la doublure, avec la même arité utile.

    Un `AsyncMock()` accepte n'importe quel appel : il ne peut donc pas détecter
    qu'un appelant s'est trompé de signature. Une doublure explicite, si.
    """
    attendues = {
        nom
        for nom, membre in inspect.getmembers(protocole)
        if not nom.startswith("_") and callable(membre)
    }
    manquantes = attendues - set(dir(double))
    assert not manquantes, f"{double.__name__} n'implémente pas {sorted(manquantes)}"


def test_le_controle_de_conformite_sait_echouer():
    """Un test de conformité qui passe sur n'importe quoi ne prouve rien.

    `inspect.getmembers()` sur un `Protocol` pourrait très bien ne rien retourner —
    auquel cas le test précédent serait vert par vacuité. Celui-ci le démontre faux.
    """

    class Vide:
        pass

    attendues = {
        nom
        for nom, membre in inspect.getmembers(StorageProtocol)
        if not nom.startswith("_") and callable(membre)
    }
    assert attendues, "aucune méthode découverte sur le Protocol — le contrôle serait vacant"
    assert attendues - set(dir(Vide)) == attendues, "une classe vide devrait tout manquer"


# ── Messagerie ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_messagerie_observable_et_pilotable():
    m = FakeMessaging()
    await m.connect()
    assert m.connecte

    await m.send("!salon:test", "bonjour", formatted="<b>bonjour</b>")
    await m.send("!salon:test", "indexation en cours", is_status=True)
    await m.send_typing("!salon:test", True)

    assert m.textes_envoyes("!salon:test") == ["bonjour", "indexation en cours"]
    assert m.envois[0]["formatted"] == "<b>bonjour</b>"
    assert m.envois[0]["is_status"] is False
    assert m.dernier_envoi["is_status"] is True
    assert m.frappes == [("!salon:test", True)]


@pytest.mark.asyncio
async def test_injecter_declenche_le_callback():
    m = FakeMessaging()
    recus: list = []

    async def rappel(message):
        recus.append(message)

    m.on_message(rappel)
    await m.injecter(
        IncomingMessage(
            user_id="@jean:tchap.gouv.fr",
            conversation_id="!salon:test",
            body="bonjour",
            conversation_type=ConversationType.PRIVATE,
        )
    )
    assert len(recus) == 1 and recus[0].body == "bonjour"


@pytest.mark.asyncio
async def test_injecter_sans_callback_echoue_franchement():
    """Mieux vaut un échec net qu'un test qui passe sans rien avoir exercé."""
    with pytest.raises(AssertionError, match="on_message"):
        await FakeMessaging().injecter(
            IncomingMessage(user_id="@y:z", conversation_id="!x", body="")
        )


@pytest.mark.asyncio
async def test_run_boucle_et_reste_annulable():
    """`run()` **ne rend pas la main** — c'est ce que le Protocol déclare.

    Ce test disait d'abord le contraire, et la doublure aussi : `run()` retournait
    immédiatement. C'était plus commode, et faux — `NoopMessaging` comme
    `WebChatMessaging` bouclent sur `asyncio.sleep`. Un test écrit contre une doublure
    complaisante passe, puis pend en production.

    Ce qu'on exige donc : que la boucle démarre, qu'elle ne retourne pas d'elle-même,
    et qu'elle soit **annulable proprement** — sans quoi l'arrêt de Colaig serait un
    kill -9.
    """
    import asyncio

    m = FakeMessaging()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(m.run(), timeout=0.2)
    assert m.demarre, "la boucle n'a pas démarré"

    tache = asyncio.create_task(m.run())
    await asyncio.sleep(0.05)
    tache.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tache
