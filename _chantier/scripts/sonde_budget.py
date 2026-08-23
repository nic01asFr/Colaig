"""Quel réglage supprime la troncature au moindre coût ?

Le problème mesuré
------------------
27 à 33 % des réponses sont coupées à `max_tokens=4000`. `qwen3-6-35b-moe` est un
modèle à raisonnement : le raisonnement et la réponse puisent au **même** budget.

Relever le plafond est la réponse évidente, et c'est la plus chère — chaque token de
raisonnement supplémentaire est payé en latence, sur un budget de tour déjà mesuré à
15 s de médiane pour un objectif de 10 s. La question n'est donc pas « combien
faut-il ? » mais **« peut-on ne pas dépenser ? »**.

Ce que cette sonde compare
---------------------------
Sur les cas qui ont réellement été tronqués, quatre régimes :

| régime | ce qu'on cherche à savoir |
|---|---|
| `4000` | le témoin, tel que la référence a été mesurée |
| `8000` | la réponse évidente : suffit-elle, et à quel prix en latence ? |
| `sans_raisonnement` | `chat_template_kwargs: enable_thinking=false`, convention Qwen3 sous vLLM |
| `effort_bas` | `reasoning_effort: low`, convention OpenAI — l'endpoint l'accepte-t-il ? |

Les deux derniers sont des paris : l'endpoint peut ignorer ces champs sans le dire.
C'est précisément pour cela qu'on mesure au lieu de configurer.

Ce qui est relevé pour chacun : `finish_reason`, la longueur de la réponse, celle du
raisonnement s'il est rendu à part, et la durée. Une réponse plus courte n'est pas un
gain si elle est plus pauvre — la vérification de fond reste le jeu doré.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(RACINE))

_ARGS = list(sys.argv[1:])

_ns: dict = {
    "__name__": "_harnais",
    "__file__": str(RACINE / "_chantier" / "scripts" / "reference_l15.py"),
}
sys.argv = ["gen", "article"]
exec(  # noqa: S102
    compile((RACINE / "_chantier" / "scripts" / "reference_l15.py").read_text(encoding="utf-8")
            .replace("raise SystemExit(main())", "pass"), "reference_l15.py", "exec"),
    _ns, _ns,
)
decouper, embed, cle_albert = _ns["decouper"], _ns["embed"], _ns["cle_albert"]

from colaig.rag.faiss_store import FaissStore  # noqa: E402

JEU = RACINE / "tests" / "golden" / "v1.jsonl"
CONFIG = RACINE / "tests" / "golden" / "corpus-marches-publics-config.yaml"
MESURES = RACINE / "_chantier" / "mesures"
BASE = "https://llm.lab.sspcloud.fr/api/v1"
MODELE = "qwen3-6-35b-moe"

REGIMES = {
    "temoin_4000": {"max_tokens": 4000},
    "large_8000": {"max_tokens": 8000},
    "sans_raisonnement": {"max_tokens": 4000,
                          "chat_template_kwargs": {"enable_thinking": False}},
    "effort_bas": {"max_tokens": 4000, "reasoning_effort": "low"},
}


def cle_ssp() -> str:
    """Clé SSPCloud : l'environnement d'abord, un `.env` local ensuite.

    Huitième exemplaire de cette fonction dans le chantier. Toutes lisaient
    **uniquement** un fichier local, ce qui rendait les harnais inexécutables en
    intégration continue — la porte de régression aurait été inerte sans que rien ne le
    signale.
    """
    depuis_env = os.environ.get("SSPCLOUD_API_KEY")
    if depuis_env:
        return depuis_env.strip()
    for fichier in (RACINE / ".env", RACINE.parent / "colaig-v3" / ".env"):
        try:
            for ligne in open(fichier, encoding="utf-8"):
                if ligne.strip().lower().startswith("sspcloud_api_key="):
                    valeur = ligne.split("=", 1)[1].strip()
                    if valeur:
                        return valeur
        except OSError:
            continue
    raise SystemExit(
        "SSPCLOUD_API_KEY introuvable : ni dans l'environnement, ni dans un .env local. "
        "En intégration continue, l'ajouter aux secrets du dépôt."
    )


def prompt_systeme() -> str:
    texte = CONFIG.read_text(encoding="utf-8")
    bloc = texte.split("system_prompt: |", 1)[1]
    lignes = []
    for ligne in bloc.splitlines()[1:]:
        if ligne.strip() and not ligne.startswith("  "):
            break
        lignes.append(ligne[2:] if ligne.startswith("  ") else ligne)
    return "\n".join(lignes).strip()


def appeler(systeme: str, question: str, passages: list[str], cle: str,
            reglages: dict) -> dict:
    contexte = "\n\n---\n\n".join(passages)
    charge = {
        "model": MODELE,
        "messages": [
            {"role": "system", "content": systeme},
            {"role": "user", "content":
                f"Passages du Code de la commande publique :\n\n{contexte}\n\n"
                f"Question : {question}"},
        ],
        "temperature": 0.1,
        **reglages,
    }
    req = urllib.request.Request(BASE + "/chat/completions",
                                 data=json.dumps(charge).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + cle)
    req.add_header("Content-Type", "application/json")
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=300) as rep:
            brut = json.loads(rep.read().decode())
    except urllib.error.HTTPError as e:
        return {"erreur": f"HTTP {e.code} — {e.read().decode()[:160]}",
                "duree": time.monotonic() - t0}
    choix = brut["choices"][0]
    msg = choix.get("message", {})
    return {
        "fin": choix.get("finish_reason"),
        "reponse": len(msg.get("content") or ""),
        "raisonnement": len(msg.get("reasoning_content") or ""),
        "duree": time.monotonic() - t0,
        "usage": brut.get("usage", {}).get("completion_tokens"),
    }


def main() -> int:
    # Les cas dont la réponse stockée a été coupée : mesurer sur ceux qui posent
    # problème, pas sur une moyenne où ils se diluent.
    stock = json.loads((MESURES / "reponses-durci-k6-20260823.json").read_text(encoding="utf-8"))
    coupes = [r["id"] for r in stock
              if not (r["reponses"] or [""])[0].rstrip().endswith((".", "!", "?", ":", "»", ")"))]
    combien = int(_ARGS[0]) if _ARGS and _ARGS[0].isdigit() else 6
    cibles = coupes[:combien]
    print(f"{len(coupes)} cas coupés dans la référence ; sonde sur {len(cibles)} : "
          f"{', '.join(cibles)}\n")

    cas = {c["id"]: c for c in
           (json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip())}
    cle_a, cle_s = cle_albert(), cle_ssp()
    chunks = decouper("article")
    store = FaissStore()
    store.add(embed([c.text for c in chunks], cle_a), chunks)
    systeme = prompt_systeme()
    vq = dict(zip(cibles, embed([cas[i]["question"] for i in cibles], cle_a)))

    resultats: dict[str, list] = {r: [] for r in REGIMES}
    for identifiant in cibles:
        passages = [r.chunk.text for r in store.search(vq[identifiant], k=6)]
        for nom, reglages in REGIMES.items():
            r = appeler(systeme, cas[identifiant]["question"], passages, cle_s, reglages)
            resultats[nom].append(r)
            if "erreur" in r:
                print(f"  {identifiant:8} {nom:20} {r['erreur']}")
            else:
                print(f"  {identifiant:8} {nom:20} fin={r['fin']:9} "
                      f"reponse={r['reponse']:5} raisonnement={r['raisonnement']:5} "
                      f"tokens={r['usage']} {r['duree']:.1f}s")

    print("\n=== synthèse ===")
    for nom, rs in resultats.items():
        ok = [r for r in rs if "erreur" not in r]
        if not ok:
            print(f"{nom:20} REFUSÉ par l'endpoint")
            continue
        coupees = sum(1 for r in ok if r["fin"] == "length")
        med = sorted(r["duree"] for r in ok)[len(ok) // 2]
        moy_rep = sum(r["reponse"] for r in ok) // len(ok)
        print(f"{nom:20} coupées {coupees}/{len(ok)}  "
              f"réponse moyenne {moy_rep} car.  médiane {med:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
