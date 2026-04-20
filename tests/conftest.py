"""
Colaig — Fixtures de test partagées

Fournit des mocks et fixtures utilisés par TOUS les tests.
Chaque agent Claude Code utilise ces fixtures pour ses tests unitaires.
"""

import pytest
import asyncio
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime

from colaig.models import (
    ColaigConfig,
    WorkspaceConfig,
    IncomingMessage,
    ConversationType,
    DocumentChunk,
    DocumentRecord,
    DocumentStatus,
    StorageFile,
)


@pytest.fixture
def event_loop():
    """Event loop pour tests async."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def test_config() -> ColaigConfig:
    return ColaigConfig(
        storage_backend="local",
        messaging_backend="matrix",
        matrix_homeserver="https://matrix.test.local",
        matrix_username="@colaig:test.local",
        matrix_password="test_password",
        albert_api_url="https://albert-api.test.local",
        albert_api_key="test_api_key",
        albert_model_chat="test-model",
        albert_model_embed="test-embed",
        webdav_url="https://nextcloud.test.local/remote.php/dav/files/colaig/",
        webdav_username="colaig",
        webdav_password="test_password",
        data_dir="/tmp/colaig-test",
    )


@pytest.fixture
def test_workspace() -> WorkspaceConfig:
    return WorkspaceConfig(
        workspace_id="test-workspace",
        name="Espace de test",
        storage_path="/espace-test/",
        description="Workspace pour les tests unitaires",
        conversations=["!test_room:test.local"],
        system_prompt="Tu es Colaig en mode test.",
        tone="professional",
        rag_enabled=True,
        similarity_threshold=0.3,
        max_results=5,
        index_path="/.colaig/indexes/",
    )


@pytest.fixture
def test_message() -> IncomingMessage:
    return IncomingMessage(
        user_id="@jean.dupont:agent.tchap.gouv.fr",
        conversation_id="!test_room:test.local",
        body="Quelle est la procédure de validation ?",
        conversation_type=ConversationType.PRIVATE,
        message_id="$test_event_123",
        display_name="Jean Dupont",
    )


@pytest.fixture
def test_dm_message() -> IncomingMessage:
    return IncomingMessage(
        user_id="@jean.dupont:agent.tchap.gouv.fr",
        conversation_id="!dm_room:test.local",
        body="Bonjour Colaig",
        conversation_type=ConversationType.DM,
        message_id="$test_dm_123",
        display_name="Jean Dupont",
    )


class MockStorage:
    """Mock du backend de stockage. Stocke fichiers en mémoire."""

    def __init__(self):
        self.files: dict[str, bytes] = {}
        self.metadata: dict[str, StorageFile] = {}

    def add_file(self, path: str, content: bytes, content_type: str = "text/plain"):
        self.files[path] = content
        self.metadata[path] = StorageFile(
            path=path, name=path.split("/")[-1], size=len(content),
            etag=f'"{hash(content)}"', last_modified=datetime.utcnow(),
            content_type=content_type,
        )

    async def list_files(self, path: str, recursive: bool = False) -> list[StorageFile]:
        prefix = path.rstrip("/") + "/"
        results = []
        for p, m in self.metadata.items():
            if not p.startswith(prefix):
                continue
            if not recursive:
                # Only direct children: relative path should have no "/" (except trailing)
                relative = p[len(prefix):].rstrip("/")
                if "/" in relative:
                    continue
            results.append(m)
        return results

    async def download(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(f"Storage 404: {path}")
        return self.files[path]

    async def download_if_changed(self, path: str, known_etag: str) -> Optional[bytes]:
        meta = self.metadata.get(path)
        if meta and meta.etag == known_etag:
            return None
        return await self.download(path)

    async def upload(self, path: str, content: bytes) -> None:
        self.add_file(path, content)

    async def mkdir(self, path: str) -> None:
        self.metadata[path] = StorageFile(path=path, name=path.split("/")[-1], is_directory=True)

    async def exists(self, path: str) -> bool:
        return path in self.files or path in self.metadata

    async def get_etag(self, path: str) -> Optional[str]:
        meta = self.metadata.get(path)
        return meta.etag if meta else None

    async def delete(self, path: str) -> None:
        self.files.pop(path, None)
        self.metadata.pop(path, None)


# Alias rétrocompatibilité
MockWebDAVClient = MockStorage


@pytest.fixture
def mock_storage() -> MockStorage:
    return MockStorage()


# Alias rétrocompatibilité
@pytest.fixture
def mock_webdav(mock_storage) -> MockStorage:
    return mock_storage


@pytest.fixture
def mock_storage_with_workspace(mock_storage, test_workspace) -> MockStorage:
    import yaml
    config_yaml = yaml.dump({
        "workspace_id": test_workspace.workspace_id,
        "name": test_workspace.name,
        "conversations": test_workspace.conversations,
        "system_prompt": test_workspace.system_prompt,
        "rag_enabled": test_workspace.rag_enabled,
    })
    mock_storage.add_file(f"{test_workspace.storage_path}.colaig/config.yaml", config_yaml.encode("utf-8"), "text/yaml")
    mock_storage.add_file(
        f"{test_workspace.storage_path}documents/guide.txt",
        b"La procedure de validation consiste en 3 etapes : 1. Soumettre le formulaire. 2. Validation par le chef de service. 3. Archivage dans le systeme.",
        "text/plain",
    )
    # Add directory entry for the workspace root so list_workspaces can discover it
    mock_storage.metadata[test_workspace.storage_path] = StorageFile(
        path=test_workspace.storage_path, name="espace-test", is_directory=True,
    )
    return mock_storage


# Alias rétrocompatibilité
@pytest.fixture
def mock_webdav_with_workspace(mock_storage_with_workspace) -> MockStorage:
    return mock_storage_with_workspace


class MockAlbertClient:
    """Mock du client Albert API.

    Supporte chat(), chat_with_tools(), embed(), embed_batch().
    Pour chat_with_tools, utilise tool_call_responses si défini,
    sinon retourne une ChatCompletionResult texte depuis chat_responses.
    """

    def __init__(self, embedding_dim: int = 384):
        self.embedding_dim = embedding_dim
        self.chat_responses = ["D'après les documents, la procédure comporte 3 étapes. [guide.txt]"]
        self._chat_call_count = 0
        # Pour chat_with_tools : liste de ChatCompletionResult à retourner séquentiellement
        self.tool_call_responses: list = []
        self._tool_call_count = 0

    async def chat(self, messages, model=None, temperature=0.3, max_tokens=2048) -> str:
        response = self.chat_responses[min(self._chat_call_count, len(self.chat_responses) - 1)]
        self._chat_call_count += 1
        return response

    async def chat_stream(self, messages, **kwargs):
        response = await self.chat(messages, **kwargs)
        for word in response.split():
            yield word + " "

    async def chat_with_tools(
        self,
        messages,
        tools,
        model=None,
        temperature=0.3,
        max_tokens=2048,
        tool_choice="auto",
    ):
        from colaig.models import ChatCompletionResult
        if self.tool_call_responses:
            idx = min(self._tool_call_count, len(self.tool_call_responses) - 1)
            result = self.tool_call_responses[idx]
            self._tool_call_count += 1
            return result
        # Fallback : retourne une réponse texte depuis chat_responses
        text = await self.chat(messages, model, temperature, max_tokens)
        return ChatCompletionResult(content=text, finish_reason="stop")

    async def embed(self, text: str) -> list[float]:
        import hashlib, numpy as np
        h = hashlib.md5(text.encode()).hexdigest()
        rng = np.random.RandomState(int(h[:8], 16))
        vec = rng.randn(self.embedding_dim).astype(np.float32)
        vec = vec / np.linalg.norm(vec)
        return vec.tolist()

    async def embed_batch(self, texts: list[str], batch_size=32) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


@pytest.fixture
def mock_albert() -> MockAlbertClient:
    return MockAlbertClient()


@pytest.fixture
def mock_retriever(sample_chunks):
    """MockRetriever retournant des SearchResult depuis sample_chunks."""
    from colaig.models import SearchResult

    class MockRetriever:
        async def retrieve(self, query: str, k: int = 5, threshold: float = 0.3):
            return [
                SearchResult(chunk=c, score=0.8 - i * 0.1, rank=i)
                for i, c in enumerate(sample_chunks[:k])
            ]

    return MockRetriever()


@pytest.fixture
def sample_document_records() -> list[DocumentRecord]:
    """Fixtures de DocumentRecord pour les tests du DocumentIndex."""
    return [
        DocumentRecord(
            path="/espace-test/documents/guide-rh.pdf",
            name="guide-rh.pdf",
            size=12345,
            checksum="abc123",
            mime_type="application/pdf",
            etag='"etag-guide-rh"',
            status=DocumentStatus.ANALYZED,
            ai_summary="Guide des procédures RH couvrant les congés, le télétravail et la formation.",
            ai_category="guide",
            ai_keywords=["congés", "télétravail", "formation", "RH"],
            ai_entities=["Direction des RH", "Service Formation"],
            ai_language="fr",
            ai_doc_type="guide pratique",
            chunk_count=5,
            faiss_doc_id=0,
            indexed_at=datetime(2026, 2, 1, 10, 0, 0),
            analyzed_at=datetime(2026, 2, 1, 10, 5, 0),
        ),
        DocumentRecord(
            path="/espace-test/documents/note-service.txt",
            name="note-service.txt",
            size=2048,
            checksum="def456",
            mime_type="text/plain",
            etag='"etag-note"',
            status=DocumentStatus.PENDING,
            indexed_at=datetime(2026, 2, 2, 9, 0, 0),
        ),
        DocumentRecord(
            path="/espace-test/documents/rapport-annuel.docx",
            name="rapport-annuel.docx",
            size=98765,
            checksum="ghi789",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            etag='"etag-rapport"',
            status=DocumentStatus.ANALYZED,
            ai_summary="Rapport annuel 2025 de la direction générale.",
            ai_category="rapport",
            ai_keywords=["rapport", "annuel", "bilan", "2025"],
            ai_entities=["Direction Générale"],
            ai_language="fr",
            ai_doc_type="rapport",
            chunk_count=12,
            faiss_doc_id=1,
            indexed_at=datetime(2026, 2, 3, 14, 0, 0),
            analyzed_at=datetime(2026, 2, 3, 14, 10, 0),
        ),
    ]


@pytest.fixture
def sample_chunks() -> list[DocumentChunk]:
    return [
        DocumentChunk(text="La procédure de validation consiste en 3 étapes.", source_path="/espace-test/documents/guide.txt", source_name="guide.txt", position=0),
        DocumentChunk(text="Le formulaire doit être soumis avant le 15 du mois.", source_path="/espace-test/documents/guide.txt", source_name="guide.txt", position=1),
        DocumentChunk(text="Le chef de service valide dans un délai de 5 jours.", source_path="/espace-test/documents/guide.txt", source_name="guide.txt", position=2),
    ]
