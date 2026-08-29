"""
Contrat — les cinq commandes réduites, et ce qu'elles ne disent pas.

STATUT: TESTE
VERSION: 2026-08-29 - v1.0
LOT: L3.7

    !aide      les commandes disponibles
    !space     l'espace auquel ce salon est lié
    !index     l'état de l'index de cet espace
    !classer   où les documents ont été rangés
    !skills    les procédures déposées dans .colaig/skills/

Pourquoi elles sont toutes des LECTURES
-----------------------------------------
Le lot demande des « commandes réduites ». Aucune ne déclenche de travail coûteux :
relancer une indexation depuis une phrase de salon exigerait d'injecter l'`Indexer`
dans le handler — une dépendance de plus, décidée dans `main.py`, et un coût que
personne n'aurait consenti en tapant cinq caractères.

Ce qui manque pour aller plus loin est écrit dans `AVANCEMENT.md` plutôt que
partiellement construit.

La garde qui compte
--------------------
Une commande arrive par le même canal que n'importe quel message : elle est **du
contenu extérieur**. En mode CHATBOT — un salon que personne n'a lié — il n'y a pas
d'espace, et il ne doit y avoir aucune lecture. Sans cette garde, `!index` deviendrait
un moyen de faire parler Colaig d'un espace auquel le salon n'a pas accès.

C'est la même frontière que D42/D43 : **rien n'est lisible tant que rien n'est lié.**
"""
from __future__ import annotations

import pytest

from colaig.models import ContextMode, ConversationType, IncomingMessage
from tests.test_handlers import MockGenerator, MockMessaging, MockResolver, MockRetriever


def _message(corps: str) -> IncomingMessage:
    return IncomingMessage(
        user_id="@alice:tchap.gouv.fr",
        conversation_id="!salon:tchap.gouv.fr",
        body=corps,
        conversation_type=ConversationType.PRIVATE,
        message_id="$evt",
        display_name="Alice",
    )


@pytest.fixture
def espace(fake_storage):
    """Un espace lié, avec une compétence et un index."""
    from colaig import paths

    fake_storage.add_file(paths.skills_dir("/test/") + "procedures.md",
                          b"# Procedures\nvalidation en deux temps")
    fake_storage.add_file(paths.skills_dir("/test/") + "glossaire.md", b"# Glossaire")
    fake_storage.add_file(paths.index_file("/test/", "index.faiss"), b"\x00" * 2048)
    fake_storage.add_file(paths.index_file("/test/", "bm25.pkl"), b"\x00" * 512)
    return fake_storage


def _handler(messaging, storage, mode=ContextMode.ASSISTANT):
    from colaig.messaging.handlers import MessageHandler

    return MessageHandler(messaging, MockResolver(mode=mode), MockRetriever(),
                          MockGenerator(), storage)


async def _dire(corps: str, storage, mode=ContextMode.ASSISTANT) -> str:
    m = MockMessaging()
    await _handler(m, storage, mode).handle_message(_message(corps))
    return "\n".join(t for _, t in m.messages_sent)


# ── Les cinq ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_aide_annonce_les_cinq(espace):
    rendu = await _dire("!aide", espace)
    for commande in ("!aide", "!space", "!index", "!classer", "!skills"):
        assert commande in rendu, f"{commande} n'est pas annoncee"


@pytest.mark.asyncio
async def test_space_nomme_l_espace_lie(espace):
    rendu = await _dire("!space", espace)
    assert "test" in rendu.lower()


@pytest.mark.asyncio
async def test_skills_liste_les_procedures_deposees(espace):
    rendu = await _dire("!skills", espace)
    assert "procedures" in rendu and "glossaire" in rendu


@pytest.mark.asyncio
async def test_index_rend_compte_de_l_index(espace):
    rendu = await _dire("!index", espace)
    assert "index.faiss" in rendu


@pytest.mark.asyncio
async def test_classer_dit_ou_les_documents_sont_ranges(espace):
    """Sans classification encore faite, la commande le dit — elle n'invente pas."""
    rendu = await _dire("!classer", espace)
    assert rendu.strip(), "la commande doit repondre quelque chose"


# ── La garde ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dans_un_salon_NON_LIE_rien_n_est_lu(espace):
    """LA garde. Un salon que personne n'a lié n'a pas d'espace.

    Sans elle, `!index` ferait parler Colaig d'un espace auquel ce salon n'a pas accès —
    et il suffirait de l'inviter pour interroger le dossier par défaut.

    Même frontière que D42/D43 : rien n'est lisible tant que rien n'est lié.
    """
    espace.appels.clear()
    rendu = await _dire("!index", espace, mode=ContextMode.CHATBOT)

    lectures = [a for a in espace.appels if a[0] in ("download", "list_files")]
    assert lectures == [], f"le salon non lie a provoque des lectures : {lectures}"
    assert "lié" in rendu or "lie" in rendu.lower(), (
        "la reponse doit expliquer qu'aucun espace n'est lie"
    )


@pytest.mark.asyncio
async def test_une_commande_n_appelle_PAS_le_pipeline(espace):
    """Une commande est traitée et s'arrête là.

    La laisser tomber dans le pipeline coûterait un appel LLM pour répondre à cinq
    caractères, et l'assistant improviserait une réponse à `!index`.
    """
    m = MockMessaging()
    generateur = MockGenerator()
    from colaig.messaging.handlers import MessageHandler

    handler = MessageHandler(m, MockResolver(), MockRetriever(), generateur, espace)
    await handler.handle_message(_message("!skills"))

    assert not generateur.generate_called, "la commande a traverse jusqu'au generateur"


@pytest.mark.asyncio
async def test_un_mot_qui_CONTIENT_une_commande_n_en_est_pas_une(espace):
    """« que fait !index dans ce dossier ? » est une question, pas une commande.

    Intercepter sur la simple présence ferait disparaître des questions légitimes.
    """
    m = MockMessaging()
    generateur = MockGenerator()
    from colaig.messaging.handlers import MessageHandler

    handler = MessageHandler(m, MockResolver(), MockRetriever(), generateur, espace)
    await handler.handle_message(_message("que fait !index dans ce dossier ?"))

    assert generateur.generate_called, "une question a ete avalee comme une commande"


@pytest.mark.asyncio
async def test_une_commande_INCONNUE_passe_au_pipeline(espace):
    """`!truc` n'est pas des nôtres.

    L'avaler en silence ferait croire à une commande qui n'existe pas ; la laisser
    passer donne au moins une réponse.
    """
    m = MockMessaging()
    generateur = MockGenerator()
    from colaig.messaging.handlers import MessageHandler

    handler = MessageHandler(m, MockResolver(), MockRetriever(), generateur, espace)
    await handler.handle_message(_message("!truc"))

    assert generateur.generate_called


@pytest.mark.asyncio
async def test_une_panne_de_stockage_repond_quand_meme(espace, monkeypatch):
    """Une commande qui ne répond rien laisse l'utilisateur sans signal."""
    async def _tombe(*a, **k):
        raise OSError("stockage indisponible")

    monkeypatch.setattr(espace, "list_files", _tombe)
    rendu = await _dire("!skills", espace)
    assert rendu.strip(), "aucune reponse en cas de panne"
