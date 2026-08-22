#!/usr/bin/env python3
"""
Sonde du catalogue LLM — lève H1 et H2.

STATUT: COMPLET
VERSION: 2026-08-22 - v1.0
LOT: L1.3

Répond à quatre questions dont dépend toute l'architecture agent :
  1. Quels modèles sont servis ?
  2. Le chat fonctionne-t-il ?
  3. Le **tool calling** est-il supporté ? (si non, la boucle agent change de nature)
  4. Des embeddings sont-ils servis, et de quelle dimension ?
  5. Un reranker est-il disponible ? (si non → MMR seul, dette à noter)

Usage :
    export COLAIG_LLM_BASE_URL=https://llm.lab.sspcloud.fr/api
    export COLAIG_LLM_API_KEY=...
    python probe_llm.py > ../docs/llm-capabilities.md

Aucune valeur n'est inventée : ce qui n'est pas vérifié est marqué INCONNU.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("COLAIG_LLM_BASE_URL", "https://llm.lab.sspcloud.fr/api").rstrip("/")
KEY = os.environ.get("COLAIG_LLM_API_KEY", "")
TIMEOUT = 60


def call(path: str, payload: dict | None = None) -> tuple[int, dict | str, float]:
    """POST si payload, sinon GET. Retourne (status, corps, durée_s)."""
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("Content-Type", "application/json")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read().decode("utf-8", "replace")
            dt = time.monotonic() - t0
            try:
                return r.status, json.loads(body), dt
            except json.JSONDecodeError:
                return r.status, body[:2000], dt
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:2000], time.monotonic() - t0
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}", time.monotonic() - t0


def main() -> int:
    if not KEY:
        print("ERREUR : COLAIG_LLM_API_KEY absent. H1 et H2 restent non levées.",
              file=sys.stderr)
        return 2

    print(f"# Catalogue LLM — sonde du {time.strftime('%Y-%m-%d %H:%M')}\n")
    print(f"Endpoint : `{BASE}`\n")

    # ── 1. Modèles ────────────────────────────────────────────────────────────
    st, body, dt = call("/models")
    print(f"## 1. Modèles — `GET /models` → {st} ({dt:.2f}s)\n")
    models: list[str] = []
    if st == 200 and isinstance(body, dict):
        for m in body.get("data", body.get("models", [])):
            mid = m.get("id") if isinstance(m, dict) else str(m)
            if mid:
                models.append(mid)
        print("| modèle |\n|---|")
        for m in models:
            print(f"| `{m}` |")
    else:
        print(f"```\n{body}\n```")
    print()

    chat_model = os.environ.get("COLAIG_LLM_MODEL") or (models[0] if models else "")
    embed_model = os.environ.get("COLAIG_EMBED_MODEL") or next(
        (m for m in models if "embed" in m.lower()), ""
    )
    rerank_model = next((m for m in models if "rerank" in m.lower()), "")

    # ── 2. Chat ───────────────────────────────────────────────────────────────
    print(f"## 2. Chat — modèle `{chat_model}`\n")
    st, body, dt = call("/chat/completions", {
        "model": chat_model,
        "messages": [{"role": "user", "content": "Réponds exactement : OK"}],
        "max_tokens": 10, "temperature": 0,
    })
    ok_chat = st == 200
    print(f"`POST /chat/completions` → {st} ({dt:.2f}s) — **{'OK' if ok_chat else 'ÉCHEC'}**\n")
    if not ok_chat:
        print(f"```\n{body}\n```\n")

    # ── 3. Tool calling — LA question qui décide de l'architecture ────────────
    print("## 3. Tool calling ⚠️\n")
    st, body, dt = call("/chat/completions", {
        "model": chat_model,
        "messages": [{"role": "user",
                      "content": "Cherche les documents sur les marchés publics."}],
        "tools": [{
            "type": "function",
            "function": {
                "name": "search_documents",
                "description": "Recherche sémantique dans les documents indexés.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
        }],
        "tool_choice": "auto", "max_tokens": 200, "temperature": 0,
    })
    tool_ok = False
    if st == 200 and isinstance(body, dict):
        msg = (body.get("choices") or [{}])[0].get("message", {})
        tool_ok = bool(msg.get("tool_calls"))
        print(f"`tools=[...]` → {st} ({dt:.2f}s) — "
              f"tool_calls : **{'PRÉSENT' if tool_ok else 'ABSENT'}**\n")
        print(f"```json\n{json.dumps(msg, ensure_ascii=False, indent=2)[:1200]}\n```\n")
    else:
        print(f"→ {st} ({dt:.2f}s)\n```\n{body}\n```\n")

    print("> **Si tool_calls est ABSENT :** la boucle agent ne peut pas fonctionner en\n"
          "> mode natif. Replis possibles, par ordre de préférence : (a) un autre modèle\n"
          "> du catalogue, (b) Albert API en provider dédié pour l'orchestration,\n"
          "> (c) parsing d'un JSON structuré imposé par prompt — dégradé, à éviter.\n"
          "> **Arrêter et arbitrer avant d'écrire du code.**\n")

    # ── 4. Embeddings ─────────────────────────────────────────────────────────
    print(f"## 4. Embeddings — modèle `{embed_model or 'INCONNU'}`\n")
    if embed_model:
        st, body, dt = call("/embeddings", {"model": embed_model, "input": ["test"]})
        if st == 200 and isinstance(body, dict):
            vec = (body.get("data") or [{}])[0].get("embedding", [])
            print(f"→ {st} ({dt:.2f}s) — **dimension = {len(vec)}**\n")
        else:
            print(f"→ {st} ({dt:.2f}s)\n```\n{body}\n```\n")
    else:
        print("Aucun modèle d'embedding identifié dans le catalogue. **INCONNU.**\n")

    # ── 5. Reranker (H2) ──────────────────────────────────────────────────────
    print("## 5. Reranker (H2)\n")
    if rerank_model:
        st, body, dt = call("/rerank", {
            "model": rerank_model,
            "query": "marchés publics",
            "documents": ["procédure de passation", "recette de cuisine"],
        })
        print(f"`POST /rerank` modèle `{rerank_model}` → {st} ({dt:.2f}s)\n")
        print(f"```\n{json.dumps(body, ensure_ascii=False)[:800] if isinstance(body, dict) else body}\n```\n")
    else:
        print("Aucun modèle de reranking dans le catalogue.\n\n"
              "> **H2 non levée par le catalogue.** Vérifier côté Albert API. Si aucun\n"
              "> reranker n'est disponible : le pipeline perd le gain le plus documenté\n"
              "> des benchmarks 2026. Repli = MMR seul. **À inscrire comme dette dans\n"
              "> AVANCEMENT.md, pas à découvrir au lot L4.1.**\n")

    # ── Verdict ───────────────────────────────────────────────────────────────
    print("## Verdict\n")
    print(f"- **H1** (chat + tool calling) : "
          f"{'✅ levée' if (ok_chat and tool_ok) else '❌ NON levée'}")
    print(f"- **H2** (reranker) : {'✅ levée' if rerank_model else '❌ non levée'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
