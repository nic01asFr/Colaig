#!/usr/bin/env python3
"""
Colaig — test de charge simple (squelette).

Envoie N requêtes vers un endpoint avec un parallélisme donné, mesure débit et
latences (p50/p95/p99). Par défaut cible /ready (sans coût LLM).

Exemples :
    python scripts/loadtest.py --url http://localhost:8000/ready -n 500 -c 50
    python scripts/loadtest.py --url http://localhost:8000/ask --method POST \
        --json '{"message":"bonjour","conversation_id":"load","user_id":"u"}' -n 100 -c 10

ATTENTION : tester /ask ou le pipeline RAG consomme des tokens LLM réels.
"""

from __future__ import annotations

import argparse
import asyncio
import json as _json
import time

import httpx


async def _worker(client, method, url, payload, results, sem):
    async with sem:
        t0 = time.monotonic()
        ok = False
        try:
            if method == "POST":
                r = await client.post(url, json=payload, timeout=60)
            else:
                r = await client.get(url, timeout=60)
            ok = r.status_code < 400
        except Exception:
            ok = False
        results.append((time.monotonic() - t0, ok))


def _pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--method", default="GET", choices=["GET", "POST"])
    ap.add_argument("--json", default="", help="payload JSON (POST)")
    ap.add_argument("-n", "--requests", type=int, default=200)
    ap.add_argument("-c", "--concurrency", type=int, default=20)
    args = ap.parse_args()

    payload = _json.loads(args.json) if args.json else None
    sem = asyncio.Semaphore(args.concurrency)
    results: list[tuple[float, bool]] = []

    start = time.monotonic()
    async with httpx.AsyncClient() as client:
        await asyncio.gather(*[
            _worker(client, args.method, args.url, payload, results, sem)
            for _ in range(args.requests)
        ])
    elapsed = time.monotonic() - start

    lat = [r for r, ok in results]
    n_ok = sum(1 for _, ok in results if ok)
    print(f"requêtes     : {len(results)}  (ok={n_ok}, ko={len(results) - n_ok})")
    print(f"durée totale : {elapsed:.2f}s   débit : {len(results) / elapsed:.1f} req/s")
    print(f"latence ms   : p50={_pct(lat, 50) * 1000:.0f}  "
          f"p95={_pct(lat, 95) * 1000:.0f}  p99={_pct(lat, 99) * 1000:.0f}")


if __name__ == "__main__":
    asyncio.run(main())
