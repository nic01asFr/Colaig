"""
Test d'intégration du client MCP Colaig contre datagouv-mcp.

Endpoint public : https://mcp.data.gouv.fr/mcp
Aucune auth requise.

Couvre :
  - Handshake initialize / notifications/initialized
  - Négociation des capacités serveur
  - tools/list (pagination)
  - tools/call : search_datasets, get_dataset_info, get_metrics
  - ping
  - Lecture des MCPToolResult (content blocks)

Usage :
    python -m pytest tests/test_mcp_datagouv.py -v
  ou directement :
    python tests/test_mcp_datagouv.py
"""
import sys
import os

# ── Correction du path AVANT tout autre import ───────────────────────────────
# Python ajoute automatiquement le dossier du script (tests/) à sys.path[0],
# ce qui fait que tests/platform/ shadow le module stdlib `platform`.
# On le retire et on ajoute la racine du projet à la place.
_tests_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_tests_dir)

# Retirer tests/ de sys.path pour éviter le shadow de platform
sys.path = [p for p in sys.path if os.path.abspath(p) != _tests_dir]
# Ajouter la racine du projet
if _root not in sys.path:
    sys.path.insert(0, _root)

# Pré-importer platform et httpx pendant que sys.path est propre
import platform  # noqa: E402 — doit être importé avant que httpx déclenche attr
import httpx     # noqa: E402 — met httpx dans sys.modules avant tout import app

import asyncio  # noqa: E402

DATAGOUV_MCP_URL = "https://mcp.data.gouv.fr/mcp"

# ─── Helpers ──────────────────────────────────────────────────────────────────

def section(title: str):
    print(f"\n{'═' * 60}")
    print(f"  {title}")
    print('═' * 60)

def ok(msg: str):
    print(f"  ✓  {msg}")

def fail(msg: str):
    print(f"  ✗  {msg}")
    raise AssertionError(msg)

def info(msg: str):
    print(f"     {msg}")


# ─── Tests ────────────────────────────────────────────────────────────────────

async def test_initialize():
    """Vérifie le handshake MCP et la négociation des capacités."""
    section("1. Initialize + ServerCapabilities")
    from app.services.mcp.client import MCPClient

    async with MCPClient(DATAGOUV_MCP_URL, server_name="datagouv") as client:
        caps = client.server_capabilities
        if caps is None:
            fail("server_capabilities est None après initialize()")

        info(f"Protocole : {caps.protocol_version}")
        info(f"Serveur   : {caps.server_name} v{caps.server_version}")
        info(f"Tools     : {caps.has_tools} (listChanged={caps.tools_list_changed})")
        info(f"Resources : {caps.has_resources}")
        info(f"Prompts   : {caps.has_prompts}")
        info(f"Logging   : {caps.has_logging}")
        if caps.instructions:
            info(f"Instructions : {caps.instructions[:80]}…")

        if not caps.has_tools:
            fail("Le serveur ne déclare pas la capacité 'tools'")

        ok(f"Initialize OK — {caps.server_name} {caps.server_version}")
    return caps


async def test_ping():
    """Vérifie que le serveur répond au ping."""
    section("2. Ping")
    from app.services.mcp.client import MCPClient

    async with MCPClient(DATAGOUV_MCP_URL, server_name="datagouv") as client:
        alive = await client.ping()
        if not alive:
            fail("Ping sans réponse")
        ok("Ping OK")


async def test_list_tools():
    """Liste les outils et vérifie leur structure."""
    section("3. tools/list")
    from app.services.mcp.client import MCPClient

    async with MCPClient(DATAGOUV_MCP_URL, server_name="datagouv") as client:
        tools = await client.list_tools()

        info(f"{len(tools)} outil(s) reçu(s)")
        for t in tools:
            schema_props = list(t.input_schema.get("properties", {}).keys())
            info(f"  • {t.qualified_name:<35} — {t.description[:60]}")
            if schema_props:
                info(f"    args : {schema_props}")

        if len(tools) == 0:
            fail("Aucun outil reçu")

        tool_names = [t.name for t in tools]
        expected = [
            "search_datasets",
            "get_dataset_info",
            "list_dataset_resources",
            "get_resource_info",
            "get_metrics",
            "search_dataservices",
            "get_dataservice_info",
            "get_dataservice_openapi_spec",
            "query_resource_data",
        ]
        missing = [n for n in expected if n not in tool_names]
        if missing:
            fail(f"Outils attendus manquants : {missing}")

        ok(f"tools/list OK — {len(tools)} outils, tous les outils attendus présents")
        return tools


async def test_call_search_datasets():
    """Appelle search_datasets et valide le résultat."""
    section("4. tools/call — search_datasets")
    from app.services.mcp.client import MCPClient

    async with MCPClient(DATAGOUV_MCP_URL, server_name="datagouv") as client:
        result = await client.call_tool(
            "search_datasets",
            {"query": "budget communes", "page_size": 3},
        )

        info(f"is_error : {result.is_error}")
        info(f"Blocs    : {len(result.contents)}")
        text = result.text
        info(f"Extrait  : {text[:200]}…")

        if result.is_error:
            fail(f"L'outil a retourné une erreur : {text}")
        if not text.strip():
            fail("Résultat vide")

        ok("search_datasets OK")
        return result


async def test_call_get_dataset_info():
    """Appelle get_dataset_info sur un jeu de données connu."""
    section("5. tools/call — get_dataset_info")
    from app.services.mcp.client import MCPClient

    # INSEE : populations légales — jeu de données stable sur data.gouv.fr
    DATASET_ID = "5369992aa3a729239d205183"

    async with MCPClient(DATAGOUV_MCP_URL, server_name="datagouv") as client:
        result = await client.call_tool(
            "get_dataset_info",
            {"dataset_id": DATASET_ID},
        )

        text = result.text
        info(f"is_error : {result.is_error}")
        info(f"Extrait  : {text[:200]}…")

        if result.is_error:
            fail(f"Erreur get_dataset_info : {text}")
        if not text.strip():
            fail("Résultat vide")

        ok("get_dataset_info OK")


async def test_call_get_metrics():
    """Appelle get_metrics sur un dataset connu."""
    section("6. tools/call — get_metrics")
    from app.services.mcp.client import MCPClient

    DATASET_ID = "5369992aa3a729239d205183"

    async with MCPClient(DATAGOUV_MCP_URL, server_name="datagouv") as client:
        result = await client.call_tool(
            "get_metrics",
            {"dataset_id": DATASET_ID},
        )

        text = result.text
        info(f"is_error : {result.is_error}")
        info(f"Extrait  : {text[:200]}…")

        if result.is_error:
            # get_metrics peut échouer selon la dispo de l'API — ne pas bloquer
            info("get_metrics a retourné is_error=True (API métriques peut être indisponible)")
        else:
            ok("get_metrics OK")


async def test_mcp_config_format():
    """Vérifie que le format mcp_servers.json est correct et parseable."""
    section("7. Format mcp_servers.json")
    import json
    from app.services.mcp.workspace_loader import MCPServerConfig

    sample = {
        "servers": [
            {
                "name": "datagouv",
                "url": "https://mcp.data.gouv.fr/mcp",
                "token": None,
                "description": "Données ouvertes françaises — data.gouv.fr",
                "enabled": True,
                "timeout": 30,
            }
        ]
    }

    servers = [
        MCPServerConfig.from_dict(s)
        for s in sample["servers"]
        if s.get("name") and s.get("url")
    ]

    if len(servers) != 1:
        fail(f"Parsing mcp_servers.json : {len(servers)} serveur(s) au lieu de 1")

    cfg = servers[0]
    assert cfg.name == "datagouv"
    assert cfg.url == "https://mcp.data.gouv.fr/mcp"
    assert cfg.enabled is True
    assert cfg.timeout == 30.0

    info(f"Config parsée : name={cfg.name}, url={cfg.url}, timeout={cfg.timeout}s")
    ok("Format mcp_servers.json OK")

    # Afficher un exemple de fichier à déployer dans un workspace
    info("\nExemple de fichier .albert/config/mcp_servers.json :")
    info(json.dumps(sample, ensure_ascii=False, indent=2))


async def test_full_registry_flow():
    """
    Teste le flux complet via MCPRegistry (comme l'orchestrateur le ferait).
    Utilise un mock WebDAV qui retourne directement la config.
    """
    section("8. Flux complet via MCPRegistry")
    from app.services.mcp.registry import MCPRegistry
    from app.services.mcp.types import MCPTool

    # Mock du service WebDAV — retourne notre config datagouv
    class MockWebDAVService:
        async def exists(self, path: str) -> bool:
            return ".albert/config/mcp_servers.json" in path

        async def download_file(self, path: str) -> bytes:
            import json
            config = {
                "servers": [{
                    "name": "datagouv",
                    "url": "https://mcp.data.gouv.fr/mcp",
                    "enabled": True,
                    "timeout": 30,
                }]
            }
            return json.dumps(config).encode()

    registry = MCPRegistry()

    # Configurer le sampling handler (mock)
    async def mock_sampling(messages, system_prompt, max_tokens, model_prefs, config=None) -> str:
        return "Réponse de test sampling"

    registry.configure(app_config=None, sampling_callback=mock_sampling)

    workspace_root = "/test-workspace"
    webdav_svc = MockWebDAVService()

    # Lister les outils via le registry
    tools = await registry.get_tools(webdav_svc, workspace_root)
    info(f"{len(tools)} outil(s) obtenu(s) via registry")

    if not tools:
        fail("Registry n'a pas retourné d'outils")

    tool_names = [t.name for t in tools]
    if "search_datasets" not in tool_names:
        fail(f"search_datasets absent des outils du registry : {tool_names[:5]}")

    # Appeler un outil via le registry
    result = await registry.call_tool(
        workspace_root,
        "datagouv__search_datasets",
        {"query": "transparence", "page_size": 2},
    )

    info(f"call_tool via registry — is_error={result.is_error}")
    info(f"Extrait : {result.text[:150]}…")

    if result.is_error:
        fail(f"call_tool registry erreur : {result.text}")

    ok(f"Registry complet OK — {len(tools)} outils, call_tool fonctionnel")

    await registry.close_all()


# ─── Runner ───────────────────────────────────────────────────────────────────

async def run_all():
    results = {}
    tests = [
        ("initialize",          test_initialize),
        ("ping",                test_ping),
        ("list_tools",          test_list_tools),
        ("search_datasets",     test_call_search_datasets),
        ("get_dataset_info",    test_call_get_dataset_info),
        ("get_metrics",         test_call_get_metrics),
        ("config_format",       test_mcp_config_format),
        ("registry_flow",       test_full_registry_flow),
    ]

    for name, fn in tests:
        try:
            await fn()
            results[name] = "OK"
        except AssertionError as e:
            results[name] = f"FAIL: {e}"
            print(f"\n  → ÉCHEC : {e}")
        except Exception as e:
            results[name] = f"ERROR: {type(e).__name__}: {e}"
            print(f"\n  → ERREUR inattendue : {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    section("Résultats")
    all_ok = True
    for name, status in results.items():
        icon = "✓" if status == "OK" else "✗"
        print(f"  {icon}  {name:<25} {status}")
        if status != "OK":
            all_ok = False

    print()
    if all_ok:
        print("  Tous les tests passent ✓")
    else:
        print("  Des tests ont échoué ✗")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(run_all())
