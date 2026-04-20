#!/usr/bin/env python3
"""
setup_mcp_token.py — Crée un token MCP pour un utilisateur Colaig.

Usage :
    python scripts/setup_mcp_token.py

Ce script :
    1. Crée le workspace personnel de l'utilisateur (idempotent)
    2. Génère un token MCP (scope global ou restreint)
    3. Persiste le token + la config MCP dans le workspace personnel
    4. Affiche le token et les instructions d'utilisation

Le token produit est utilisable immédiatement dans tout client MCP
(Claude Desktop, Cursor, Continue...) via Authorization: Bearer <token>.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Ajouter la racine du projet au path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))


# =============================================================================
# Configuration du test — à adapter selon l'environnement
# =============================================================================

USER_ID   = "@nicolas.laval:agent.tchap.gouv.fr"
TOKEN_NAME = "Claude Desktop local"
SCOPE      = "*"          # "*" = accès complet (PERSONAL) | "workspace-id" = restreint
BASE_URL   = "http://localhost:8000"
STORAGE_PATH = str(_ROOT / "data" / "storage")  # Ou C:/Users/Omen/Desktop/LAVAL/colaig-data

# Pour utiliser le storage de prod local :
# STORAGE_PATH = "C:/Users/Omen/Desktop/LAVAL/colaig-data"


# =============================================================================
# Main
# =============================================================================

async def main() -> None:
    from colaig.integrations.storage.local import LocalStorage
    from colaig.context.workspace import get_or_create_personal_workspace
    from colaig.auth.tokens import TokenManager, _personal_ws_slug

    print(f"\n{'='*60}")
    print(f"  Colaig — Création token MCP")
    print(f"{'='*60}")
    print(f"  Utilisateur : {USER_ID}")
    print(f"  Slug        : {_personal_ws_slug(USER_ID)}")
    print(f"  Scope       : {SCOPE!r}")
    print(f"  Token name  : {TOKEN_NAME!r}")
    print(f"  Storage     : {STORAGE_PATH}")
    print(f"  Base URL    : {BASE_URL}")
    print(f"{'='*60}\n")

    storage = LocalStorage(base_path=STORAGE_PATH)

    # 1. Créer (ou charger) le workspace personnel
    print("→ Initialisation du workspace personnel...")
    ws = await get_or_create_personal_workspace(storage, USER_ID)
    print(f"  ✓ Workspace : {ws.workspace_id}")
    print(f"    Chemin    : {ws.storage_path}")
    print(f"    user_ids  : {ws.user_ids}")

    # 2. Créer le token
    print(f"\n→ Création du token {TOKEN_NAME!r}...")
    tm = TokenManager(storage=storage, base_url=BASE_URL)
    raw_token = await tm.create(
        user_id=USER_ID,
        name=TOKEN_NAME,
        scope=SCOPE,
    )
    print(f"  ✓ Token créé\n")

    # 3. Vérifier la résolution immédiate
    print("→ Vérification de la résolution...")
    ctx = await tm.resolve(raw_token)
    if ctx:
        print(f"  ✓ Résolution OK : user_id={ctx.user_id!r} scope={ctx.scope!r}")
    else:
        print("  ✗ ÉCHEC résolution — vérifier les logs")
        return

    # 4. Lire la config MCP générée
    from colaig.auth.tokens import _personal_ws_slug, _safe_name, _mcp_config_path
    slug = _personal_ws_slug(USER_ID)
    sname = _safe_name(TOKEN_NAME)
    cfg_path = _mcp_config_path(slug, sname)
    cfg_full_path = Path(STORAGE_PATH) / cfg_path.lstrip("/")

    print(f"\n→ Config MCP générée : {cfg_path}")
    if cfg_full_path.exists():
        cfg = json.loads(cfg_full_path.read_text(encoding="utf-8"))
        print(f"  ✓ Fichier présent")
    else:
        print(f"  ✗ Fichier absent ({cfg_full_path})")
        return

    # 5. Afficher les résultats
    print(f"\n{'='*60}")
    print(f"  TOKEN (à conserver — affiché une seule fois)")
    print(f"{'='*60}")
    print(f"\n  {raw_token}\n")

    print(f"{'='*60}")
    print(f"  CONFIG MCP — à copier dans claude_desktop_config.json")
    print(f"{'='*60}")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))

    print(f"\n{'='*60}")
    print(f"  INSTRUCTIONS")
    print(f"{'='*60}")
    print(f"""
  1. Démarrez Colaig en local :
       docker-compose up
     ou :
       STORAGE_BACKEND=local LOCAL_STORAGE_PATH={STORAGE_PATH} python -m colaig.main

  2. Copiez la config MCP ci-dessus dans votre client :
     Claude Desktop : ~/AppData/Roaming/Claude/claude_desktop_config.json
     Cursor         : ~/.cursor/mcp.json
     Continue       : ~/.continue/config.json  (section mcp_servers)

  3. Testez avec colaig_ask :
     - Sans workspace_id → mode PERSONAL (workspace personnel + ask_workspace)
     - Avec workspace_id → mode ASSISTANT sur ce workspace

  4. Lister / révoquer les tokens via MCP :
       colaig_list_tokens()
       colaig_revoke_token("{TOKEN_NAME}")
""")

    # 6. Test de cohérence slug ↔ workspace
    print(f"  Chemin workspace personnel : {STORAGE_PATH}{ws.storage_path}")
    print(f"  Fichier token              : {STORAGE_PATH}{cfg_path.replace('mcp-configs', 'tokens').replace(sname, '*')}")
    print(f"  Fichier config MCP         : {STORAGE_PATH}{cfg_path}\n")


if __name__ == "__main__":
    asyncio.run(main())
