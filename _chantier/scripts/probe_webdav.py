#!/usr/bin/env python3
"""
Sonde WebDAV — lève H3, H4 et H5.

STATUT: COMPLET
VERSION: 2026-08-22 - v1.0
LOT: L1.1 / L1.4

La latence WebDAV est la cause réelle de la quasi-totalité des timeouts de la version
déployée (guards 20-25 s dans la pré-orchestration, préchargement 5 puis 50 contextes,
attente 30 s des contextes, caches TTL 300 s). Aucune recherche ne remplace une mesure.

Mesure :
  - PROPFIND Depth:1 sur la racine et sur un espace
  - PROPFIND Depth:infinity (le coûteux, celui qui fait exploser les timeouts)
  - GET d'un fichier
  - découverte des marqueurs .colaig / .albert  → état de la migration
  - volumétrie par espace : documents, poids     → H5 (seuil FAISS)
  - comptage des conversations                    → H4 (jeu doré)

Usage :
    export COLAIG_WEBDAV_URL=https://.../remote.php/dav/files/xxx
    export COLAIG_WEBDAV_USER=... COLAIG_WEBDAV_PASSWORD=...
    export COLAIG_WEBDAV_ROOT=/           # optionnel
    python probe_webdav.py
"""
from __future__ import annotations

import base64
import os
import statistics
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

URL = os.environ.get("COLAIG_WEBDAV_URL", "").rstrip("/")
USER = os.environ.get("COLAIG_WEBDAV_USER", "")
PWD = os.environ.get("COLAIG_WEBDAV_PASSWORD", "")
ROOT = os.environ.get("COLAIG_WEBDAV_ROOT", "/")
TIMEOUT = 120

PROPFIND_BODY = (
    '<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
    "<d:resourcetype/><d:getcontentlength/><d:getlastmodified/><d:getetag/>"
    "</d:prop></d:propfind>"
).encode()


def _auth() -> str:
    return "Basic " + base64.b64encode(f"{USER}:{PWD}".encode()).decode()


def propfind(path: str, depth: str = "1") -> tuple[int, str, float]:
    url = f"{URL}{path if path.startswith('/') else '/' + path}"
    req = urllib.request.Request(url, data=PROPFIND_BODY, method="PROPFIND")
    req.add_header("Authorization", _auth())
    req.add_header("Depth", depth)
    req.add_header("Content-Type", "application/xml")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.status, r.read().decode("utf-8", "replace"), time.monotonic() - t0
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")[:500], time.monotonic() - t0
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}", time.monotonic() - t0


def entries(xml: str) -> list[tuple[str, bool, int]]:
    """→ [(href, is_dir, taille)]"""
    out: list[tuple[str, bool, int]] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return out
    ns = {"d": "DAV:"}
    for resp in root.findall("d:response", ns):
        href_el = resp.find("d:href", ns)
        if href_el is None or not href_el.text:
            continue
        is_dir = resp.find(".//d:resourcetype/d:collection", ns) is not None
        size_el = resp.find(".//d:getcontentlength", ns)
        size = int(size_el.text) if (size_el is not None and size_el.text) else 0
        out.append((href_el.text, is_dir, size))
    return out


def bench(path: str, depth: str, n: int = 3) -> tuple[float, int, str]:
    """n mesures → (médiane, nb_entrées, statut)"""
    times, count, status = [], 0, "?"
    for _ in range(n):
        st, body, dt = propfind(path, depth)
        times.append(dt)
        status = str(st)
        if st in (207, 200):
            count = len(entries(body))
    return statistics.median(times), count, status


def main() -> int:
    if not (URL and USER and PWD):
        print("ERREUR : COLAIG_WEBDAV_URL / _USER / _PASSWORD requis. H3 non levée.",
              file=sys.stderr)
        return 2

    print(f"# Sonde WebDAV — {time.strftime('%Y-%m-%d %H:%M')}\n")
    print(f"Cible : `{URL}` · racine `{ROOT}`\n")

    # ── Latence ───────────────────────────────────────────────────────────────
    print("## 1. Latence (médiane sur 3 mesures)\n")
    print("| opération | médiane | entrées | statut |\n|---|---|---|---|")
    med1, n1, st1 = bench(ROOT, "1")
    print(f"| PROPFIND Depth:1 racine | {med1*1000:.0f} ms | {n1} | {st1} |")

    spaces: list[str] = []
    st, body, _ = propfind(ROOT, "1")
    if st in (207, 200):
        base_href = None
        for href, is_dir, _sz in entries(body):
            if base_href is None:
                base_href = href
                continue
            if is_dir:
                spaces.append(href)

    target = spaces[0] if spaces else ROOT
    rel = "/" + target.split("/dav/files/", 1)[-1].split("/", 1)[-1] if "/dav/files/" in target else target
    med_ns, n_ns, st_ns = bench(rel, "1")
    print(f"| PROPFIND Depth:1 espace | {med_ns*1000:.0f} ms | {n_ns} | {st_ns} |")

    t0 = time.monotonic()
    st_inf, body_inf, _ = propfind(rel, "infinity")
    dt_inf = time.monotonic() - t0
    n_inf = len(entries(body_inf)) if st_inf in (207, 200) else 0
    print(f"| PROPFIND Depth:infinity espace | {dt_inf*1000:.0f} ms | {n_inf} | {st_inf} |")
    print()

    # ── Marqueurs ─────────────────────────────────────────────────────────────
    print("## 2. Marqueurs — état de la migration `.albert` → `.colaig`\n")
    print("| espace | .albert | .colaig | conversations |\n|---|---|---|---|")
    total_conv = 0
    for sp in spaces[:20]:
        r = "/" + sp.split("/dav/files/", 1)[-1].split("/", 1)[-1] if "/dav/files/" in sp else sp
        has_alb = propfind(r.rstrip("/") + "/.albert", "0")[0] in (207, 200)
        has_col = propfind(r.rstrip("/") + "/.colaig", "0")[0] in (207, 200)
        nconv = 0
        for marker in (".colaig", ".albert"):
            stc, bodyc, _ = propfind(r.rstrip("/") + f"/{marker}/conversations", "1")
            if stc in (207, 200):
                nconv = max(nconv, max(0, len(entries(bodyc)) - 1))
        total_conv += nconv
        print(f"| `{r}` | {'✅' if has_alb else '—'} | {'✅' if has_col else '—'} | {nconv} |")
    print()

    # ── Volumétrie ────────────────────────────────────────────────────────────
    ents = entries(body_inf) if st_inf in (207, 200) else []
    files = [(h, s) for h, d, s in ents if not d]
    total_mb = sum(s for _, s in files) / 1e6
    print("## 3. Volumétrie (espace échantillon)\n")
    print(f"- fichiers : **{len(files)}**\n- poids total : **{total_mb:.1f} Mo**\n")

    # ── Verdict ───────────────────────────────────────────────────────────────
    print("## Verdict\n")
    print(f"- **H3** (latence) : Depth:1 médiane {med_ns*1000:.0f} ms, "
          f"Depth:infinity {dt_inf*1000:.0f} ms.")
    print("  - < 300 ms en Depth:1 → l'architecture de cache actuelle suffit.")
    print("  - > 1 s → il faut un index local persistant, pas seulement un cache TTL.")
    print("  - Depth:infinity > 10 s → **l'interdire dans le code** et n'indexer qu'en")
    print("    incrémental par etags.")
    print(f"- **H4** (jeu doré) : ~{total_conv} conversations trouvées "
          f"(cible ≥ 200 cas).")
    print(f"- **H5** (seuil FAISS) : {len(files)} fichiers sur l'espace échantillon. "
          "IndexFlatIP reste raisonnable en dessous de ~100 000 chunks.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
