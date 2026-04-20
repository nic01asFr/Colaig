"""Tests d'intégration — Pipeline complet avec tous les composants réels (sauf I/O)."""

import pytest

from colaig.models import (
    ContextMode,
    IncomingMessage,
    ConversationType,
    StorageFile,
)

# Composants réels
from colaig.context.resolver import ContextResolver
from colaig.rag.chunker import Chunker
from colaig.rag.embeddings import EmbeddingService
from colaig.rag.faiss_store import FaissStore
from colaig.rag.retriever import Retriever
from colaig.rag.indexer import Indexer
from colaig.rag.generator import Generator
from colaig.messaging.handlers import MessageHandler


DIM = 384


@pytest.fixture
def full_storage(mock_storage):
    """Storage avec workspace complet (config + documents)."""
    # Dossier racine
    mock_storage.metadata["/espace-test/"] = StorageFile(
        path="/espace-test/", name="espace-test", is_directory=True,
    )

    # Config workspace
    import yaml
    config = {
        "workspace_id": "integration-test",
        "name": "Espace Intégration",
        "conversations": ["!integration:test.local"],
        "system_prompt": "Tu es un assistant de test d'intégration.",
        "rag_enabled": True,
    }
    mock_storage.add_file(
        "/espace-test/.colaig/config.yaml",
        yaml.dump(config).encode("utf-8"),
    )

    # Documents
    mock_storage.add_file(
        "/espace-test/documents/procedure.txt",
        (
            b"La procedure de validation comprend trois etapes essentielles. "
            b"Premierement, soumettre le formulaire au bureau des entrees. "
            b"Deuxiemement, le chef de service examine et valide la demande. "
            b"Troisiemement, le dossier est archive dans le systeme informatique."
        ),
    )
    mock_storage.add_file(
        "/espace-test/documents/reglement.txt",
        (
            b"Le reglement interieur stipule que les demandes doivent "
            b"etre soumises avant le quinze de chaque mois. "
            b"Tout retard entraine un report au mois suivant."
        ),
    )

    return mock_storage


class MockMessagingForIntegration:
    """Mock du MessagingProtocol pour les tests d'intégration."""

    def __init__(self):
        self.messages_sent: list[tuple[str, str]] = []

    async def send(self, conversation_id, text, formatted=None):
        self.messages_sent.append((conversation_id, text))

    async def send_typing(self, conversation_id, typing=True, timeout=10000):
        pass


class TestEndToEndPipeline:
    """Test end-to-end : message → indexation → retrieval → génération → réponse."""

    async def test_full_pipeline_with_rag(self, full_storage, mock_albert):
        """Scénario complet : workspace indexé, message reçu, réponse avec sources."""
        # 1. Initialiser les composants
        chunker = Chunker(chunk_size=200, chunk_overlap=20)
        embeddings = EmbeddingService(mock_albert, dimension=DIM)
        store = FaissStore(DIM)
        retriever = Retriever(embeddings, store)
        indexer = Indexer(full_storage, chunker, embeddings, store)
        resolver = ContextResolver(full_storage, cache_ttl=0)
        generator = Generator(mock_albert)
        messaging = MockMessagingForIntegration()

        handler = MessageHandler(messaging, resolver, retriever, generator, full_storage)

        # 2. Indexer le workspace
        count = await indexer.index_workspace("/espace-test/")
        assert count == 2  # procedure.txt + reglement.txt
        assert store.count > 0

        # 3. Envoyer un message
        message = IncomingMessage(
            user_id="@agent-education.gouv.fr:agent.tchap.gouv.fr",
            conversation_id="!integration:test.local",
            body="Quelle est la procédure de validation ?",
            conversation_type=ConversationType.PRIVATE,
            message_id="$evt_integration",
            display_name="Agent Test",
        )

        await handler.handle_message(message)

        # 4. Vérifier qu'une réponse a été envoyée
        assert len(messaging.messages_sent) == 1
        conversation_id, response_text = messaging.messages_sent[0]
        assert conversation_id == "!integration:test.local"
        assert response_text != ""

    async def test_chatbot_mode_no_workspace(self, mock_storage, mock_albert):
        """Mode chatbot quand la conversation n'est pas mappée à un workspace."""
        embeddings = EmbeddingService(mock_albert, dimension=DIM)
        store = FaissStore(DIM)
        retriever = Retriever(embeddings, store)
        resolver = ContextResolver(mock_storage, cache_ttl=0)
        generator = Generator(mock_albert)
        messaging = MockMessagingForIntegration()

        handler = MessageHandler(messaging, resolver, retriever, generator, mock_storage)

        message = IncomingMessage(
            user_id="@user:test.local",
            conversation_id="!unknown:test.local",
            body="Bonjour",
            conversation_type=ConversationType.PUBLIC,
            message_id="$evt_chatbot",
        )

        await handler.handle_message(message)

        # Réponse envoyée même sans workspace
        assert len(messaging.messages_sent) == 1
        assert messaging.messages_sent[0][1] != ""

    async def test_dm_personal_mode(self, mock_storage, mock_albert):
        """DM → mode personnel."""
        embeddings = EmbeddingService(mock_albert, dimension=DIM)
        store = FaissStore(DIM)
        retriever = Retriever(embeddings, store)
        resolver = ContextResolver(mock_storage, cache_ttl=0)
        generator = Generator(mock_albert)
        messaging = MockMessagingForIntegration()

        handler = MessageHandler(messaging, resolver, retriever, generator, mock_storage)

        message = IncomingMessage(
            user_id="@user:test.local",
            conversation_id="!dm:test.local",
            body="Aide-moi",
            conversation_type=ConversationType.DM,
            message_id="$evt_dm",
        )

        await handler.handle_message(message)
        assert len(messaging.messages_sent) == 1
