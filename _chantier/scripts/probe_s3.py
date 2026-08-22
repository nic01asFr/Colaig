#!/usr/bin/env python3
"""
Sonde du stockage S3 SSPCloud (MinIO) — lève H3, H4 et H5.

STATUT: COMPLET
VERSION: 2026-08-22 - v1.0
LOT: L1.1 / L1.4

Remplace `probe_webdav.py` : le stockage retenu pour le chantier est le stockage
utilisateur SSPCloud (MinIO), pas un espace WebDAV. Voir la décision correspondante
dans `DECISIONS.md`.

La latence du stockage est la cause réelle de la quasi-totalité des timeouts de la
version déployée (guards 20-25 s dans la pré-orchestration, préchargement 5 puis 50
contextes, attente 30 s des contextes, caches TTL 300 s). Aucune recherche ne remplace
une mesure.

Mesure :
  - listing non récursif (Delimiter='/') sur la racine et sur un espace
  - listing récursif complet — l'équivalent du PROPFIND Depth:infinity, celui qui
    fait exploser les timeouts
  - GET d'un objet
  - aller-retour PUT / GET / DELETE  → latence d'écriture, que Colaig subit à chaque
    mise à jour de `.colaig/`
  - découverte des marqueurs .colaig / .albert  → état de la migration
  - volumétrie par espace : objets, poids        → H5 (seuil FAISS)
  - comptage des conversations                   → H4 (jeu doré)
  - nature et durée de vie des credentials       → risque propre à Onyxia

Usage :
    export COLAIG_S3_ENDPOINT_URL=https://minio.lab.sspcloud.fr
    export COLAIG_S3_BUCKET=...
    export COLAIG_S3_ACCESS_KEY=... COLAIG_S3_SECRET_KEY=...
    export COLAIG_S3_SESSION_TOKEN=...        # si credentials temporaires STS
    export COLAIG_S3_PREFIX=                  # optionnel
    python probe_s3.py

Dans un pod Onyxia, les variables AWS_* injectées suffisent : le script les reprend
automatiquement quand les COLAIG_S3_* sont absentes.

Aucune valeur n'est inventée : ce qui n'est pas vérifié est marqué INCONNU.
"""
from __future__ import annotations

import os
import statistics
import sys
import time

TIMEOUT = 120
ECRITURE_AUTORISEE = os.environ.get("COLAIG_S3_ALLOW_WRITE", "1") != "0"


def _env(*noms: str, defaut: str = "") -> str:
    """Première variable non vide parmi `noms`."""
    for n in noms:
        v = os.environ.get(n, "")
        if v:
            return v
    return defaut


ENDPOINT = _env("COLAIG_S3_ENDPOINT_URL", "S3_ENDPOINT_URL", "AWS_S3_ENDPOINT")
BUCKET = _env("COLAIG_S3_BUCKET", "S3_BUCKET_NAME", "AWS_BUCKET_NAME")
ACCESS = _env("COLAIG_S3_ACCESS_KEY", "S3_ACCESS_KEY", "AWS_ACCESS_KEY_ID")
SECRET = _env("COLAIG_S3_SECRET_KEY", "S3_SECRET_KEY", "AWS_SECRET_ACCESS_KEY")
TOKEN = _env("COLAIG_S3_SESSION_TOKEN", "S3_SESSION_TOKEN", "AWS_SESSION_TOKEN")
PREFIX = _env("COLAIG_S3_PREFIX", "S3_PREFIX")
REGION = _env("COLAIG_S3_REGION", "S3_REGION", "AWS_DEFAULT_REGION", defaut="us-east-1")


def _client():
    """Client boto3, ou None avec un message explicite."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        print("ERREUR : boto3 requis. `pip install boto3`. H3 non levée.", file=sys.stderr)
        return None

    url = ENDPOINT
    if url and not url.startswith("http"):
        url = "https://" + url
    return boto3.client(
        "s3",
        endpoint_url=url or None,
        aws_access_key_id=ACCESS or None,
        aws_secret_access_key=SECRET or None,
        aws_session_token=TOKEN or None,
        region_name=REGION,
        config=Config(connect_timeout=TIMEOUT, read_timeout=TIMEOUT, retries={"max_attempts": 1}),
    )


def lister(s3, prefixe: str, recursif: bool) -> tuple[list, list, float, str]:
    """→ (objets, prefixes_communs, durée_s, statut)"""
    params = {"Bucket": BUCKET, "Prefix": prefixe}
    if not recursif:
        params["Delimiter"] = "/"
    objets: list = []
    prefixes: list = []
    t0 = time.monotonic()
    try:
        jeton = None
        while True:
            if jeton:
                params["ContinuationToken"] = jeton
            r = s3.list_objects_v2(**params)
            objets.extend(r.get("Contents", []))
            prefixes.extend(p["Prefix"] for p in r.get("CommonPrefixes", []))
            if not r.get("IsTruncated"):
                break
            jeton = r.get("NextContinuationToken")
        return objets, prefixes, time.monotonic() - t0, "200"
    except Exception as e:  # noqa: BLE001
        return [], [], time.monotonic() - t0, f"{type(e).__name__}: {str(e)[:120]}"


def bench_liste(s3, prefixe: str, recursif: bool, n: int = 3) -> tuple[float, int, str]:
    """n mesures → (médiane, nb_entrées, statut)"""
    durees, total, statut = [], 0, "?"
    for _ in range(n):
        objets, prefixes, dt, st = lister(s3, prefixe, recursif)
        durees.append(dt)
        statut = st
        total = len(objets) + len(prefixes)
    return statistics.median(durees), total, statut


def existe(s3, cle: str) -> bool:
    try:
        s3.head_object(Bucket=BUCKET, Key=cle)
        return True
    except Exception:  # noqa: BLE001
        objets, prefixes, _, _ = lister(s3, cle.rstrip("/") + "/", recursif=False)
        return bool(objets or prefixes)


def main() -> int:
    if not BUCKET:
        print("ERREUR : COLAIG_S3_BUCKET requis. H3 non levée.", file=sys.stderr)
        return 2
    s3 = _client()
    if s3 is None:
        return 2

    print(f"# Sonde stockage S3 SSPCloud — {time.strftime('%Y-%m-%d %H:%M')}\n")
    print(f"Endpoint : `{ENDPOINT or '<défaut AWS>'}` · bucket `{BUCKET}` · "
          f"préfixe `{PREFIX or '/'}`\n")

    # ── Credentials : nature et durée de vie ─────────────────────────────────
    print("## 0. Credentials\n")
    if TOKEN:
        print("- Type : **temporaires (STS)** — un `session_token` est présent.")
        print("- ⚠️ **Les jetons STS Onyxia ont été mesurés refusés en moins de 9 heures.**")
        print("  Une instance au long cours tombera en panne d'authentification sans")
        print("  qu'aucune ligne de code n'ait changé. Voir `HYPOTHESES.md`.")
    else:
        print("- Type : **permanentes** (aucun `session_token`).")
    print()

    # ── Latence ──────────────────────────────────────────────────────────────
    print("## 1. Latence (médiane sur 3 mesures)\n")
    print("| opération | médiane | entrées | statut |\n|---|---|---|---|")

    racine = PREFIX.rstrip("/") + "/" if PREFIX else ""
    med1, n1, st1 = bench_liste(s3, racine, recursif=False)
    print(f"| LIST non récursif racine | {med1*1000:.0f} ms | {n1} | {st1} |")

    _, espaces, _, _ = lister(s3, racine, recursif=False)
    cible = espaces[0] if espaces else racine
    med_e, n_e, st_e = bench_liste(s3, cible, recursif=False)
    print(f"| LIST non récursif espace | {med_e*1000:.0f} ms | {n_e} | {st_e} |")

    objets_rec, _, dt_rec, st_rec = lister(s3, cible, recursif=True)
    print(f"| LIST récursif espace | {dt_rec*1000:.0f} ms | {len(objets_rec)} | {st_rec} |")

    if objets_rec:
        cle = objets_rec[0]["Key"]
        t0 = time.monotonic()
        try:
            s3.get_object(Bucket=BUCKET, Key=cle)["Body"].read()
            st_get = "200"
        except Exception as e:  # noqa: BLE001
            st_get = f"{type(e).__name__}"
        print(f"| GET d'un objet | {(time.monotonic()-t0)*1000:.0f} ms | 1 | {st_get} |")
    print()

    # ── Aller-retour en écriture ─────────────────────────────────────────────
    print("## 2. Écriture — aller-retour PUT / GET / DELETE\n")
    if not ECRITURE_AUTORISEE:
        print("Écriture désactivée (`COLAIG_S3_ALLOW_WRITE=0`). **INCONNU.**\n")
    else:
        cle = f"{racine}.colaig-probe/aller-retour-{int(time.time())}.txt"
        charge = b"sonde colaig - StorageProtocol"
        try:
            t0 = time.monotonic(); s3.put_object(Bucket=BUCKET, Key=cle, Body=charge)
            t_put = time.monotonic() - t0
            t0 = time.monotonic(); lu = s3.get_object(Bucket=BUCKET, Key=cle)["Body"].read()
            t_get = time.monotonic() - t0
            t0 = time.monotonic(); s3.delete_object(Bucket=BUCKET, Key=cle)
            t_del = time.monotonic() - t0
            print(f"- PUT : **{t_put*1000:.0f} ms**")
            print(f"- GET : **{t_get*1000:.0f} ms** (contenu identique : {lu == charge})")
            print(f"- DELETE : **{t_del*1000:.0f} ms**\n")
        except Exception as e:  # noqa: BLE001
            print(f"- **ÉCHEC** : `{type(e).__name__}: {str(e)[:200]}`\n")

    # ── Marqueurs ────────────────────────────────────────────────────────────
    print("## 3. Marqueurs — état de la migration `.albert` → `.colaig`\n")
    print("| espace | .albert | .colaig | conversations |\n|---|---|---|---|")
    total_conv = 0
    for esp in espaces[:20]:
        a = existe(s3, esp + ".albert/")
        c = existe(s3, esp + ".colaig/")
        n_conv = 0
        for marqueur in (".colaig", ".albert"):
            objets, _, _, _ = lister(s3, f"{esp}{marqueur}/conversations/", recursif=True)
            n_conv = max(n_conv, len([o for o in objets if o["Key"].endswith(".json")]))
        total_conv += n_conv
        print(f"| `{esp}` | {'✅' if a else '—'} | {'✅' if c else '—'} | {n_conv} |")
    if not espaces:
        print("| _(aucun préfixe de premier niveau)_ | — | — | 0 |")
    print()

    # ── Volumétrie ───────────────────────────────────────────────────────────
    poids_mo = sum(o.get("Size", 0) for o in objets_rec) / 1e6
    print("## 4. Volumétrie (espace échantillon)\n")
    print(f"- objets : **{len(objets_rec)}**\n- poids total : **{poids_mo:.1f} Mo**\n")

    # ── Verdict ──────────────────────────────────────────────────────────────
    print("## Verdict\n")
    print(f"- **H3** (latence) : LIST non récursif médiane {med_e*1000:.0f} ms, "
          f"LIST récursif {dt_rec*1000:.0f} ms.")
    print("  - < 300 ms en non récursif → l'architecture de cache actuelle suffit.")
    print("  - > 1 s → il faut un index local persistant, pas seulement un cache TTL.")
    print("  - LIST récursif > 10 s → **l'interdire dans le code** et n'indexer qu'en")
    print("    incrémental par ETags.")
    print(f"- **H4** (jeu doré) : ~{total_conv} conversations trouvées (cible ≥ 200 cas).")
    print(f"- **H5** (seuil FAISS) : {len(objets_rec)} objets sur l'espace échantillon. "
          "IndexFlatIP reste raisonnable en dessous de ~100 000 chunks — à recalculer "
          "sur une dimension d'embedding de 4096 (16 Ko par vecteur en float32).")
    if TOKEN:
        print("- ⚠️ **Durabilité des credentials : NON RÉSOLUE.** Jetons STS temporaires.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
