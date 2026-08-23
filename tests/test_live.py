"""
tests/test_live.py — Smoke tests contre le container Colaig en cours d'exécution.

Usage :
    # Container sur localhost:8000 (défaut)
    pytest tests/test_live.py -v -m live

    # Container sur une autre URL
    COLAIG_TEST_URL=http://localhost:9000 pytest tests/test_live.py -v -m live

    # Avec rapport détaillé
    pytest tests/test_live.py -v -m live -s

Prérequis :
    - Container Colaig tournant (`docker-compose up -d`)
    - httpx installé (déjà dans requirements.txt)

Les tests sont séquentiels (fixture scoped="session") pour partager l'état
CRUD : création workspace → liaison salon → mise à jour → déliaison.
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.environ.get("COLAIG_TEST_URL", "http://localhost:8000").rstrip("/")

# ID et chemin du workspace de test — doit être unique pour éviter les conflits
SMOKE_WS_ID = "smoke-test-ws"
SMOKE_WS_PATH = "/smoke-test/"
SMOKE_CONV_ID = "!smoke_test_conv:localhost"

pytestmark = pytest.mark.live


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def client():
    """Client httpx synchrone partagé pour toute la session.

    **Absence de container = `skip`, pas `fail`.** Sans cette vérification, un `pytest`
    nu sortait 41 échecs sur un dépôt sain : ces tests exigent un service HTTP en écoute,
    et l'absence d'un prérequis d'environnement n'est pas un défaut du code.

    La distinction n'est pas cosmétique. Une suite dont on sait qu'elle est rouge « pour
    de mauvaises raisons » cesse d'être lue : le jour où un vrai défaut s'y ajoute,
    personne ne le voit. C'est le même réflexe que pour les garde-fous — un contrôle qui
    ne discrimine pas ne contrôle rien.

    Le marqueur `live` reste : `pytest -m live` exprime l'intention de les exécuter, et
    la vérification dit alors précisément ce qui manque.
    """
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as c:
        try:
            c.get("/health", timeout=2.0)
        except httpx.HTTPError as exc:
            pytest.skip(
                f"aucun service Colaig sur {BASE_URL} ({type(exc).__name__}) — "
                "démarrer le container (docker-compose up -d) ou pointer "
                "COLAIG_TEST_URL vers une instance en écoute"
            )
        yield c


# ---------------------------------------------------------------------------
# Axe 1 — Infrastructure : santé, métriques, dashboard
# ---------------------------------------------------------------------------

class TestInfrastructure:
    """Vérifie que le container répond correctement sur les endpoints de base."""

    def test_health_returns_ok(self, client):
        """/health → status ok."""
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "uptime_seconds" in data
        assert "started_at" in data

    def test_health_uptime_positive(self, client):
        """/health → uptime > 0 (container a démarré depuis > 0 sec)."""
        r = client.get("/health")
        assert r.json()["uptime_seconds"] >= 0

    def test_metrics_returns_workspace_count(self, client):
        """/metrics → contient le nombre de workspaces."""
        r = client.get("/metrics")
        assert r.status_code == 200
        data = r.json()
        assert "workspaces" in data
        assert isinstance(data["workspaces"], int)
        assert data["workspaces"] >= 0

    def test_metrics_uptime_consistent_with_health(self, client):
        """/metrics uptime cohérent avec /health (± 2 sec)."""
        h = client.get("/health").json()["uptime_seconds"]
        m = client.get("/metrics").json()["uptime_seconds"]
        assert abs(h - m) <= 2

    def test_dashboard_html(self, client):
        """/ → HTML valide avec titre Colaig."""
        r = client.get("/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "Colaig" in r.text

    def test_openapi_schema_available(self, client):
        """/openapi.json → schéma OpenAPI disponible."""
        r = client.get("/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert schema["info"]["title"] == "Colaig Admin"


# ---------------------------------------------------------------------------
# Axe 2 — Lecture des workspaces existants
# ---------------------------------------------------------------------------

class TestWorkspacesReadOnly:
    """Opérations lecture sur les workspaces configurés dans le container."""

    def test_list_workspaces_returns_array(self, client):
        """/workspaces → tableau JSON."""
        r = client.get("/workspaces")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    def test_list_workspaces_fields(self, client):
        """Chaque workspace a les champs attendus."""
        r = client.get("/workspaces")
        for ws in r.json():
            assert "workspace_id" in ws
            assert "name" in ws
            assert "storage_path" in ws
            assert "rag_enabled" in ws
            assert "conversations" in ws

    def test_list_workspaces_matches_metrics(self, client):
        """Nombre de workspaces cohérent avec /metrics."""
        count_metrics = client.get("/metrics").json()["workspaces"]
        count_list = len(client.get("/workspaces").json())
        assert count_metrics == count_list

    def test_get_workspace_by_id(self, client):
        """GET /workspaces/{id} → détail complet du premier workspace."""
        workspaces = client.get("/workspaces").json()
        if not workspaces:
            pytest.skip("Aucun workspace configuré dans ce container")

        ws_id = workspaces[0]["workspace_id"]
        r = client.get(f"/workspaces/{ws_id}")
        assert r.status_code == 200
        detail = r.json()
        assert detail["workspace_id"] == ws_id
        assert "description" in detail
        assert "conversations" in detail
        assert "rag_enabled" in detail
        assert "similarity_threshold" in detail
        assert "max_results" in detail
        assert "tone" in detail
        assert "language" in detail

    def test_get_workspace_404(self, client):
        """GET /workspaces/inexistant → 404."""
        r = client.get("/workspaces/inexistant-00000")
        assert r.status_code == 404

    def test_existing_workspaces_have_storage_path(self, client):
        """Chaque workspace a un storage_path non vide."""
        for ws in client.get("/workspaces").json():
            assert ws["storage_path"], f"Workspace {ws['workspace_id']} sans storage_path"


# ---------------------------------------------------------------------------
# Axe 3 — Cycle CRUD complet (workspace de test dédié)
# ---------------------------------------------------------------------------

class TestWorkspaceCRUD:
    """
    Cycle complet sur un workspace de test :
    création → consultation → liaison salon → mise à jour → déliaison.

    Note : ces tests sont ordonnés et partagent l'état via les IDs constants
    SMOKE_WS_ID / SMOKE_CONV_ID définis en en-tête de fichier.
    Il n'y a pas d'endpoint DELETE workspace — le workspace de test persiste
    dans le storage local du container (chemin /smoke-test/).
    """

    def test_create_workspace(self, client):
        """POST /workspaces → crée le workspace de test (idempotent)."""
        r = client.post("/workspaces", json={
            "storage_path": SMOKE_WS_PATH,
            "name": "Smoke Test Workspace",
            "workspace_id": SMOKE_WS_ID,
            "description": "Workspace créé par les smoke tests automatiques.",
            "rag_enabled": True,
            "tone": "professional",
            "language": "fr",
        })
        # 201 (créé) ou 200 (existait déjà — idempotent)
        assert r.status_code in (200, 201), f"Unexpected {r.status_code}: {r.text}"
        data = r.json()
        assert data["workspace_id"] == SMOKE_WS_ID

    def test_created_workspace_appears_in_list(self, client):
        """Le workspace créé apparaît dans GET /workspaces."""
        ids = [ws["workspace_id"] for ws in client.get("/workspaces").json()]
        assert SMOKE_WS_ID in ids

    def test_get_created_workspace_detail(self, client):
        """GET /workspaces/{id} → détail complet du workspace créé."""
        r = client.get(f"/workspaces/{SMOKE_WS_ID}")
        assert r.status_code == 200
        detail = r.json()
        assert detail["workspace_id"] == SMOKE_WS_ID
        assert detail["rag_enabled"] is True

    def test_link_conversation_to_workspace(self, client):
        """POST /workspaces/{id}/conversations → lie un salon."""
        r = client.post(
            f"/workspaces/{SMOKE_WS_ID}/conversations",
            json={"conversation_id": SMOKE_CONV_ID},
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "linked"
        assert SMOKE_CONV_ID in data["conversations"]

    def test_linked_conversation_visible_in_detail(self, client):
        """Après liaison, le salon apparaît dans le détail du workspace."""
        detail = client.get(f"/workspaces/{SMOKE_WS_ID}").json()
        assert SMOKE_CONV_ID in detail["conversations"]

    def test_update_workspace_config(self, client):
        """PUT /workspaces/{id} → met à jour description et tone."""
        r = client.put(f"/workspaces/{SMOKE_WS_ID}", json={
            "description": "Description mise à jour par smoke test.",
            "tone": "casual",
        })
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "updated"
        assert set(data["updated_fields"]) >= {"description", "tone"}

    def test_update_reflected_in_detail(self, client):
        """La mise à jour est persistée et visible dans GET /workspaces/{id}."""
        detail = client.get(f"/workspaces/{SMOKE_WS_ID}").json()
        assert detail["tone"] == "casual"

    def test_unlink_conversation_from_workspace(self, client):
        """DELETE /workspaces/{id}/conversations/{conv_id} → délie le salon."""
        r = client.delete(
            f"/workspaces/{SMOKE_WS_ID}/conversations/{SMOKE_CONV_ID}"
        )
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "unlinked"
        assert SMOKE_CONV_ID not in data["conversations"]

    def test_unlinked_conversation_gone_from_detail(self, client):
        """Après déliaison, le salon n'est plus dans le workspace."""
        detail = client.get(f"/workspaces/{SMOKE_WS_ID}").json()
        assert SMOKE_CONV_ID not in detail["conversations"]

    def test_update_workspace_no_fields(self, client):
        """PUT /workspaces/{id} sans champs → warning, pas d'erreur."""
        r = client.put(f"/workspaces/{SMOKE_WS_ID}", json={})
        assert r.status_code == 200
        data = r.json()
        assert "warning" in data

    def test_link_conversation_unknown_workspace_404(self, client):
        """POST /workspaces/inexistant/conversations → 404."""
        r = client.post(
            "/workspaces/inexistant-99999/conversations",
            json={"conversation_id": "!test:localhost"},
        )
        assert r.status_code == 404

    def test_update_unknown_workspace_404(self, client):
        """PUT /workspaces/inexistant → 404."""
        r = client.put("/workspaces/inexistant-99999", json={"tone": "casual"})
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# Axe 4 — Ré-indexation
# ---------------------------------------------------------------------------

class TestReindex:
    """Test de la ré-indexation des workspaces."""

    def test_reindex_existing_workspace(self, client):
        """POST /workspaces/{id}/reindex → démarre la ré-indexation."""
        workspaces = client.get("/workspaces").json()
        if not workspaces:
            pytest.skip("Aucun workspace disponible pour ré-indexation")

        ws_id = workspaces[0]["workspace_id"]
        r = client.post(f"/workspaces/{ws_id}/reindex", timeout=120.0)
        assert r.status_code == 200
        data = r.json()
        assert data["workspace_id"] == ws_id
        assert "documents_indexed" in data
        assert isinstance(data["documents_indexed"], int)

    def test_reindex_unknown_workspace_404(self, client):
        """POST /workspaces/inexistant/reindex → 404."""
        r = client.post("/workspaces/inexistant-99999/reindex")
        assert r.status_code == 404

    def test_reindex_smoke_workspace(self, client):
        """POST /workspaces/smoke-test-ws/reindex → fonctionne (même vide)."""
        r = client.post(f"/workspaces/{SMOKE_WS_ID}/reindex", timeout=30.0)
        # 200 OK ou 404 si workspace pas encore créé (ordre d'exécution)
        assert r.status_code in (200, 404)


# ---------------------------------------------------------------------------
# Axe 5 — Robustesse (malformed requests, edge cases)
# ---------------------------------------------------------------------------

class TestRobustness:
    """Vérifie que le container ne plante pas sur des requêtes malformées."""

    def test_create_workspace_missing_required_fields(self, client):
        """POST /workspaces sans name → 422 Unprocessable Entity."""
        r = client.post("/workspaces", json={"storage_path": "/test/"})
        assert r.status_code == 422

    def test_create_workspace_missing_storage_path(self, client):
        """POST /workspaces sans storage_path → 422."""
        r = client.post("/workspaces", json={"name": "Test"})
        assert r.status_code == 422

    def test_link_conversation_missing_body(self, client):
        """POST /workspaces/{id}/conversations sans body → 422."""
        workspaces = client.get("/workspaces").json()
        if not workspaces:
            pytest.skip("Aucun workspace disponible")
        ws_id = workspaces[0]["workspace_id"]
        r = client.post(f"/workspaces/{ws_id}/conversations", json={})
        assert r.status_code == 422

    def test_get_nonexistent_workspace_returns_json(self, client):
        """404 retourne du JSON, pas du HTML."""
        r = client.get("/workspaces/definitively-not-existing")
        assert r.status_code == 404
        data = r.json()
        assert "error" in data

    def test_health_not_broken_after_operations(self, client):
        """Après toutes les opérations, /health reste ok."""
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Axe 6 — Pipeline IA : POST /ask
# ---------------------------------------------------------------------------

class TestAskEndpoint:
    """
    Tests du pipeline IA complet via POST /ask.

    Ces tests appellent réellement Albert API — ils requièrent que le container
    soit démarré avec des credentials Albert valides (ALBERT_API_KEY).
    Si /ask retourne 404 (handler non fourni à create_app), les tests sont skippés.
    """

    def _ask(self, client, message, conversation_id=None, user_id="test-user"):
        """Helper : POST /ask avec gestion du skip si endpoint absent."""
        payload = {"message": message, "user_id": user_id}
        if conversation_id:
            payload["conversation_id"] = conversation_id
        r = client.post("/ask", json=payload, timeout=60.0)
        if r.status_code == 404:
            pytest.skip("/ask non disponible (handler non injecté dans create_app)")
        return r

    def test_ask_endpoint_exists(self, client):
        """POST /ask → endpoint disponible (non 404)."""
        r = client.post("/ask", json={"message": "test"}, timeout=60.0)
        if r.status_code == 404:
            pytest.skip("/ask non disponible")
        assert r.status_code == 200

    def test_ask_returns_required_fields(self, client):
        """POST /ask → réponse contient response, conversation_id, pipeline."""
        r = self._ask(client, "Bonjour, tu fonctionnes ?")
        data = r.json()
        assert "response" in data
        assert "conversation_id" in data
        assert "pipeline" in data
        assert data["pipeline"] in ("phase1", "phase2")

    def test_ask_response_is_non_empty_string(self, client):
        """POST /ask → réponse non vide."""
        r = self._ask(client, "Bonjour !")
        assert isinstance(r.json()["response"], str)
        assert len(r.json()["response"]) > 0

    def test_ask_with_workspace_conversation_id(self, client):
        """POST /ask avec un conversation_id lié à un workspace → RAG activé."""
        # Récupère un workspace avec conversations
        workspaces = client.get("/workspaces").json()
        ws_with_conv = next(
            (ws for ws in workspaces if ws["conversations"] > 0), None
        )
        if not ws_with_conv:
            pytest.skip("Aucun workspace avec salon lié")

        ws_detail = client.get(f"/workspaces/{ws_with_conv['workspace_id']}").json()
        conv_id = ws_detail["conversations"][0]

        r = self._ask(client, "Quels documents as-tu ?", conversation_id=conv_id)
        data = r.json()
        assert data["response"]
        assert data["conversation_id"] == conv_id

    def test_ask_pipeline_phase_reflects_config(self, client):
        """Le champ pipeline correspond à la configuration du container."""
        r = self._ask(client, "Test")
        phase = r.json()["pipeline"]
        assert phase in ("phase1", "phase2")

    def test_ask_conversation_id_echoed_back(self, client):
        """Le conversation_id envoyé est retourné dans la réponse."""
        test_conv = "!echo_test_conv:localhost"
        r = self._ask(client, "Test echo", conversation_id=test_conv)
        assert r.json()["conversation_id"] == test_conv

    def test_ask_missing_message_field(self, client):
        """POST /ask sans champ message → 422 Unprocessable Entity."""
        r = client.post("/ask", json={"user_id": "test"}, timeout=30.0)
        if r.status_code == 404:
            pytest.skip("/ask non disponible")
        assert r.status_code == 422

    def test_ask_sequential_different_questions(self, client):
        """Deux questions séquentielles → deux réponses non vides."""
        r1 = self._ask(client, "Bonjour !")
        r2 = self._ask(client, "Que peux-tu faire ?")
        assert r1.json()["response"]
        assert r2.json()["response"]

    def test_ask_openapi_reflects_ask_endpoint(self, client):
        """POST /ask est documenté dans le schéma OpenAPI."""
        schema = client.get("/openapi.json").json()
        paths = schema.get("paths", {})
        if "/ask" not in paths:
            pytest.skip("/ask non disponible dans le schéma (handler non injecté)")
        assert "post" in paths["/ask"]
