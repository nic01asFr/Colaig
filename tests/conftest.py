"""
Colaig — Fixtures de test partagées

Point d'entrée **unique** du harnais de test. Les doublures elles-mêmes vivent dans
`tests/fakes.py` — `FakeStorage`, `FakeMessaging`, `FakeLLM` — et sont réexportées ici
sous leurs anciens noms (`MockStorage`, `MockAlbertClient`…) pour que les tests
existants continuent de fonctionner sans modification.

Tout est déterministe et hors ligne : aucune horloge murale, aucun hasard non semé,
aucun accès réseau.
"""

import asyncio
import os
from datetime import datetime

import pytest

from colaig.models import (
    ColaigConfig,
    ConversationType,
    DocumentChunk,
    DocumentRecord,
    DocumentStatus,
    IncomingMessage,
    StorageFile,
    WorkspaceConfig,
)
from tests.fakes import (  # noqa: F401 - reexportes pour les tests existants
    FakeLLM,
    FakeMessaging,
    FakeStorage,
    MockAlbertClient,
    MockStorage,
    MockWebDAVClient,
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


@pytest.fixture
def mock_albert() -> MockAlbertClient:
    return MockAlbertClient()


@pytest.fixture
def fake_llm() -> FakeLLM:
    """Doublure LLM déterministe. Nom canonique de `mock_albert` (lot L0.4)."""
    return FakeLLM()


@pytest.fixture
def fake_storage() -> FakeStorage:
    """Doublure de stockage déterministe. Nom canonique de `mock_storage` (lot L0.4)."""
    return FakeStorage()


@pytest.fixture
def fake_messaging() -> FakeMessaging:
    """Doublure de messagerie déterministe.

    À préférer à un `AsyncMock()` : celui-ci accepte n'importe quel appel et ne
    vérifie donc rien du contrat `MessagingProtocol`.
    """
    return FakeMessaging()


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


def code_seul(source: str) -> str:
    """Le code d'un module, sans ses commentaires ni ses docstrings.

    Plusieurs gardes de ce depot cherchent un motif interdit dans les sources —
    l'ancien marqueur de balisage, une constante de secret. Ces gardes doivent porter
    sur ce qui S'EXECUTE : un module a le droit de citer dans sa docstring la faille
    qu'il supprime, et cette trace a de la valeur.

    Filtrer par nom de fichier creerait une derogation ; filtrer les commentaires
    supprime le besoin d'en avoir une.
    """
    import io as _io
    import tokenize

    # Une docstring est une chaine qui OUVRE une ligne logique : le jeton qui la precede
    # est un INDENT, un DEDENT, une fin de ligne, ou le debut du fichier.
    #
    # Piege mesure le 24/08/2026 : une premiere version n'actualisait pas `precedent`
    # pour ces jetons-la. Il gardait donc le `:` de la signature, et le test ne
    # reconnaissait QUE les docstrings de module — jamais celles de fonction. La garde
    # du marqueur de balisage passait pour une raison partielle.
    OUVRE_UNE_LIGNE = {
        tokenize.INDENT, tokenize.DEDENT, tokenize.NEWLINE, tokenize.NL,
        tokenize.ENCODING,
    }
    # LE TEXTE EST PRESERVE : on BLANCHIT commentaires et docstrings sur place, on ne
    # reconstruit pas depuis les jetons.
    #
    # Second piege, mesure le 25/08/2026 : une version anterieure joignait les JETONS
    # par des sauts de ligne. Elle coupait donc les noms pointes —
    # `colaig.metrics.quota` devenait sept jetons sur sept lignes — et une garde
    # cherchant « metrics.quota » ne trouvait plus rien. La garde de L2.2 sur
    # `.mcp_connectors` etait ainsi ENTIEREMENT INERTE : aucun fichier n'etait examine.
    #
    # Un filtre qui denature ce qu'il filtre ne filtre pas.
    lignes = source.splitlines(keepends=True)
    a_blanchir = []
    precedent = tokenize.ENCODING
    for jeton in tokenize.generate_tokens(_io.StringIO(source).readline):
        docstring = jeton.type == tokenize.STRING and precedent in OUVRE_UNE_LIGNE
        if jeton.type == tokenize.COMMENT or docstring:
            a_blanchir.append((jeton.start, jeton.end))
        precedent = jeton.type

    for (l1, c1), (l2, c2) in reversed(a_blanchir):
        if l1 == l2:
            ligne = lignes[l1 - 1]
            lignes[l1 - 1] = ligne[:c1] + " " * (c2 - c1) + ligne[c2:]
        else:
            lignes[l1 - 1] = lignes[l1 - 1][:c1] + "\n"
            for i in range(l1, l2 - 1):
                lignes[i] = "\n"
            lignes[l2 - 1] = " " * c2 + lignes[l2 - 1][c2:]
    return "".join(lignes)


@pytest.fixture(autouse=True, scope="session")
def magasin_de_pins_isole(tmp_path_factory):
    """Le magasin d'épinglage MCP vit dans un dossier temporaire, pas dans le dépôt.

    LE DÉFAUT QUE CETTE FIXTURE FERME
    -----------------------------------
    `mcp_pins.CHEMIN_PAR_DEFAUT` vaut `config/mcp_pins.json` — un fichier **suivi par
    git**. Chaque exécution de la suite y écrivait les empreintes des outils factices, et
    ces écritures ont fini par être commitées : `test_connector::search` (lot L2.3),
    puis `commun::search`, `espace-a::search`, `juridique::search`, `rh::search` (L3.4).

    Deux conséquences, toutes deux mesurées :

    1. **Le dépôt portait des données fabriquées** dans un fichier de configuration —
       de quoi faire croire que ces épinglages étaient réels.
    2. **L'issue d'un test dépendait des exécutions précédentes.** Un test qui déclare
       un outil sous une description, puis la change, échouait ou non selon ce qu'une
       exécution antérieure avait laissé sur le disque. C'est exactement le contraire du
       contrat de `tests/CLAUDE.md` : « deux exécutions de la suite doivent produire
       exactement le même résultat, dans le même processus comme dans un autre ».

    Le magasin reste PARTAGÉ sur la session, et c'est voulu : l'épinglage protège d'un
    contrat qui change entre deux découvertes, et un magasin vidé à chaque test ne
    pourrait rien épingler du tout.
    """
    import colaig.security.mcp_pins as mcp_pins
    from colaig.integrations import mcp_connector

    ancien = mcp_pins.CHEMIN_PAR_DEFAUT
    mcp_pins.CHEMIN_PAR_DEFAUT = tmp_path_factory.mktemp("pins") / "mcp_pins.json"
    mcp_connector._MAGASIN_PINS = None
    yield
    mcp_pins.CHEMIN_PAR_DEFAUT = ancien
    mcp_connector._MAGASIN_PINS = None


@pytest.fixture(autouse=True)
def _drapeaux_neutralises(monkeypatch):
    """Aucun drapeau de comportement de la machine n'entre dans la suite.

    `tests/CLAUDE.md` pose le contrat : « deux exécutions de la suite doivent produire
    exactement le même résultat, dans le même processus comme dans un autre ». Une
    variable d'environnement posée sur le poste viole ce contrat en silence.

    Mesuré le 01/09/2026 : la suite lancée avec `COLAIG_GARDE_FOU_ENABLED=1` — la
    valeur qu'un déploiement peut porter — donne **trois échecs** que la même suite ne
    produit pas sans elle. Aucun ne signale un défaut du produit : ce sont des tests
    dont la réponse factice ne cite aucun article, donc légitimement remplacée par un
    refus. Mais un développeur qui aurait cette variable dans son shell aurait vu la
    suite rouge sans comprendre pourquoi, et un correctif inutile serait parti de là.

    SEULS LES `_ENABLED` SONT EFFACÉS, et cette limite est délibérée. Les autres
    `COLAIG_*` fournissent des ressources — `COLAIG_S3_*`, `COLAIG_WEBDAV_*`,
    `COLAIG_BASE_URL` — et les effacer empêcherait de faire tourner les contrats de
    stockage avec de vrais accès, qui n'ont pas d'autre moyen de s'exécuter. Un drapeau
    choisit un chemin de code ; un identifiant ouvre une porte. On neutralise le
    premier, jamais le second.

    Un test qui veut un drapeau le pose lui-même : `monkeypatch.setenv` s'applique
    après cette fixture, donc reste souverain.
    """
    for nom in [n for n in os.environ
                if n.startswith("COLAIG_") and n.endswith("_ENABLED")]:
        monkeypatch.delenv(nom, raising=False)
