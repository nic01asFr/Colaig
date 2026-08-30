"""
Contrat `LLMClientProtocol` — L1.3.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L1.3

Troisième et dernier contrat de Protocol, après `StorageProtocol` (L1.1) et
`MessagingProtocol` (L1.2). Même principe : une seule suite, exécutée contre chaque
implémentation, `FakeLLM` compris.

Le Protocol sous-déclare, et cela a un coût
--------------------------------------------
`LLMClientProtocol` déclare **cinq** méthodes. Le code métier en appelle **huit** :

| capacité | appelée par | déclarée au Protocol ? | albert | openai | azure | ollama |
|---|---|---|---|---|---|---|
| `chat` | partout | ✅ | ✅ | ✅ | ✅ | ✅ |
| `chat_stream` | générateur | ✅ | ✅ | ✅ | ✅ | ✅ |
| `chat_with_tools` | boucle agent | ✅ | ✅ | ✅ | ✅ | ✅ |
| `embed` / `embed_batch` | RAG | ✅ | ✅ | ✅ | ✅ | ✅ |
| **`ocr`** | `rag/indexer.py` | ❌ | ✅ | — | — | — |
| **`rerank`** | `rag/retriever.py` | ❌ | ✅ | ✅ | — | — |
| **`transcribe`** | `messaging/handlers.py` | ❌ | ✅ | ✅ | — | — |

Ces trois-là ne peuvent pas devenir obligatoires : exiger un OCR d'Ollama n'a pas de
sens. Mais leur absence doit être **demandable**, pas découverte par un `AttributeError`
au milieu d'une indexation — rattrapé par un `except Exception` et rapporté comme « OCR
en échec », message qui envoie chercher du côté du modèle alors que le backend n'a
simplement pas la capacité. D'où `integrations/llm/capabilities.py`.

Ce que ces tests couvrent, et ce qu'ils ne couvrent pas
--------------------------------------------------------
Ils vérifient les **signatures** et la **cohérence des capacités déclarées**, hors
ligne, pour les quatre backends. Le **comportement** réel — un chat qui répond, un
embedding de la bonne dimension — a été mesuré séparément contre les endpoints, et
consigné dans `docs/llm-capabilities.md`. Il n'est pas rejoué ici : un test unitaire
qui appelle un LLM distant n'est ni hors ligne ni déterministe.
"""
from __future__ import annotations

import inspect

import pytest

from colaig.integrations.llm.capabilities import (
    CAPACITES_OPTIONNELLES,
    capacites,
    motif_absence,
    supporte,
)
from colaig.protocols import LLMClientProtocol
from tests.fakes import FakeLLM

METHODES_DU_PROTOCOLE = ("chat", "chat_stream", "chat_with_tools", "embed", "embed_batch")


def _classes_reelles():
    """Les quatre implémentations, importées paresseusement.

    Import direct plutôt qu'instanciation : construire un `AlbertClient` exige une
    configuration et une clé. Ce qui est éprouvé ici est la **classe**, pas une session.
    """
    from colaig.integrations.albert import AlbertClient
    from colaig.integrations.llm.azure_client import AzureClient
    from colaig.integrations.llm.ollama_client import OllamaClient
    from colaig.integrations.llm.openai_client import OpenAIClient

    return {
        "albert": AlbertClient,
        "openai": OpenAIClient,
        "azure": AzureClient,
        "ollama": OllamaClient,
        "fake": FakeLLM,
    }


@pytest.fixture(params=list(_classes_reelles()), ids=list(_classes_reelles()))
def implementation(request):
    return _classes_reelles()[request.param]


# ── Le socle obligatoire ────────────────────────────────────────────────────


def test_les_cinq_methodes_du_protocole_existent(implementation):
    manquantes = [m for m in METHODES_DU_PROTOCOLE if not callable(getattr(implementation, m, None))]
    assert not manquantes, f"{implementation.__name__} n'implémente pas {manquantes}"


@pytest.mark.parametrize("methode", METHODES_DU_PROTOCOLE)
def test_les_parametres_declares_sont_acceptes(implementation, methode):
    """Un appelant écrit `chat(messages, temperature=0.2)` sans savoir quel backend est injecté.

    Si un backend nomme ce paramètre autrement, l'appel lève `TypeError` **à
    l'exécution**, en pleine conversation. Le vérifier statiquement coûte moins cher.
    """
    attendue = inspect.signature(getattr(LLMClientProtocol, methode))
    reelle = inspect.signature(getattr(implementation, methode))
    accepte_kwargs = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in reelle.parameters.values()
    )
    manquants = [
        nom for nom in attendue.parameters
        if nom not in ("self", "kwargs") and nom not in reelle.parameters
    ]
    assert not manquants or accepte_kwargs, (
        f"{implementation.__name__}.{methode} n'accepte pas {manquants}\n"
        f"  attendu : {attendue}\n  obtenu  : {reelle}"
    )


def test_toutes_les_methodes_du_socle_sont_asynchrones(implementation):
    """`chat_stream` est un générateur asynchrone, les autres des coroutines.

    Un appelant qui `await` une méthode synchrone obtient un `TypeError` obscur.
    """
    for methode in ("chat", "chat_with_tools", "embed", "embed_batch"):
        fonction = getattr(implementation, methode)
        assert inspect.iscoroutinefunction(fonction), (
            f"{implementation.__name__}.{methode} n'est pas une coroutine"
        )
    flux = getattr(implementation, "chat_stream")
    assert inspect.isasyncgenfunction(flux) or inspect.iscoroutinefunction(flux), (
        f"{implementation.__name__}.chat_stream n'est ni coroutine ni générateur async"
    )


# ── Les capacités optionnelles ──────────────────────────────────────────────


def test_la_matrice_des_capacites_est_celle_attendue(implementation):
    """Fige l'état constaté. Un backend qui gagne ou perd une capacité doit le déclarer ici.

    Ce test n'exige rien : il **documente** et détecte la dérive. Exiger un OCR
    d'Ollama n'aurait pas de sens ; le découvrir en production non plus.
    """
    attendu = {
        "albert": {"ocr", "rerank", "transcribe"},
        # `ocr` ajoute le 30/08/2026 : sept documents scannes du corpus deploye
        # restaient invisibles faute de cette capacite sur le fournisseur de
        # production, alors que SSPCloud expose `chandra-ocr-2`.
        #
        # ATTENTION A LA LECTURE : ce test interroge la CLASSE. Sur une INSTANCE
        # sans modele d OCR configure, la capacite est absente — voir
        # `test_ocr_openai.py`. La classe dit ce que le backend SAIT faire ;
        # l instance dit ce qu il PEUT faire ici et maintenant.
        "openai": {"ocr", "rerank", "transcribe"},
        "azure": set(),
        "ollama": set(),
        "fake": set(),
    }
    nom = next(n for n, c in _classes_reelles().items() if c is implementation)
    assert capacites(implementation) == attendu[nom], (
        f"la matrice des capacités de {nom} a changé — mettre à jour ce test "
        "et `integrations/llm/capabilities.py`"
    )


def test_supporte_repond_sans_appeler():
    """`supporte()` doit se prononcer sans déclencher l'appel qu'il qualifie."""

    class Espion:
        appele = False

        async def ocr(self, content, filename):  # pragma: no cover - ne doit pas tourner
            Espion.appele = True
            return ""

    assert supporte(Espion(), "ocr") is True
    assert Espion.appele is False, "supporte() a appelé la méthode"


def test_supporte_tolere_l_absence_de_client():
    """Le cas `None` est courant : aucun LLM configuré. Il ne doit pas lever."""
    assert supporte(None, "ocr") is False
    assert capacites(None) == set()


def test_les_motifs_distinguent_les_deux_causes():
    """« pas de client » et « backend sans la capacité » n'envoient pas au même endroit."""
    sans_client = motif_absence(None, "ocr")
    assert "aucun client" in sans_client

    class SansOCR:
        pass

    sans_capacite = motif_absence(SansOCR(), "ocr")
    assert "SansOCR" in sans_capacite and "ocr" in sans_capacite
    assert sans_client != sans_capacite


def test_aucune_capacite_optionnelle_n_est_declaree_au_protocole():
    """Garde-fou de cohérence.

    Si l'une d'elles entre un jour au Protocol, elle devient obligatoire pour les
    quatre backends — et ce module n'a plus lieu d'être pour elle. Ce test le rappellera.
    """
    declarees = {
        nom for nom, membre in inspect.getmembers(LLMClientProtocol)
        if not nom.startswith("_") and callable(membre)
    }
    chevauchement = declarees & CAPACITES_OPTIONNELLES
    assert not chevauchement, (
        f"{chevauchement} est désormais au Protocol : la retirer de "
        "CAPACITES_OPTIONNELLES et l'exiger des quatre backends"
    )


# ── CapabilityChain ─────────────────────────────────────────────────────────


def test_la_chaine_expose_le_socle():
    """`CapabilityChain` s'injecte à la place d'un client : elle doit en avoir la forme."""
    from colaig.integrations.llm.capability_chain import CapabilityChain

    manquantes = [
        m for m in METHODES_DU_PROTOCOLE if not callable(getattr(CapabilityChain, m, None))
    ]
    assert not manquantes, f"CapabilityChain n'expose pas {manquantes}"


# ── Priorité des appels ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_la_doublure_enregistre_la_priorite():
    """`priority` n'est pas décoratif : c'est un mécanisme de qualité de service.

    Les appels `background` — OCR, indexation — acquièrent un sémaphore réduit pour
    laisser au moins un créneau aux requêtes utilisateur. Un travail de fond qui
    s'annoncerait `user` affamerait les conversations **sans erreur ni trace**.

    `FakeLLM` l'enregistre, ce qui rend la chose assertionnable — c'est la seule
    manière d'attraper ce défaut, qui ne se voit ni dans un log ni dans un test
    fonctionnel.
    """
    llm = FakeLLM()
    await llm.chat([{"role": "user", "content": "question d'un agent"}])
    await llm.chat([{"role": "user", "content": "indexation"}], priority="background")

    assert llm.priorites == ["user", "background"]


@pytest.mark.asyncio
async def test_la_priorite_par_defaut_est_user():
    """Le défaut protège l'usager : un appel non qualifié n'est pas relégué."""
    llm = FakeLLM()
    await llm.chat([{"role": "user", "content": "bonjour"}])
    assert llm.priorites == ["user"]
