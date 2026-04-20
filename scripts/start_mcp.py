"""
Démarre Colaig en mode MCP-only (sans messagerie).
Charge .env.mcp et lance main().

Usage : python scripts/start_mcp.py
"""

import os
import sys
from pathlib import Path

# Charger .env.mcp depuis la racine du projet
root = Path(__file__).resolve().parent.parent
env_file = root / ".env.mcp"

if env_file.exists():
    from dotenv import load_dotenv
    load_dotenv(env_file, override=True)
    print(f"[start_mcp] config chargée depuis {env_file}")
else:
    print(f"[start_mcp] WARN: {env_file} introuvable — variables d'env utilisées telles quelles")

# Forcer MCP activé et messaging=none
os.environ.setdefault("COLAIG_MCP_ENABLED", "true")
os.environ.setdefault("MESSAGING_BACKEND", "none")

# Ajouter le projet au path Python
sys.path.insert(0, str(root))

import asyncio
from colaig.main import main

if __name__ == "__main__":
    print("[start_mcp] démarrage Colaig MCP sur http://localhost:8000/mcp")
    asyncio.run(main())
