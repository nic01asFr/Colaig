"""
Relevé des retours d'un espace — l'instrument de la porte 1.

POURQUOI CE SCRIPT EXISTE
---------------------------
Le franchissement de la porte 1 demande *« une semaine de dogfooding sur un pod de
test, relevé des 👍👎 et incidents »*. Sans instrument de lecture, cette semaine produit
un dossier de fichiers JSON qu'il faut ouvrir un par un.

Ce script rend trois choses, et rien d'autre :

1. **le taux de retour** — combien de réponses ont reçu un geste. C'est la première
   chose que la semaine doit mesurer : un dogfooding sans gestes ne mesure rien ;
2. **chaque 👎 en entier** — question, réponse, sources, confiance. C'est ce qui permet
   de dire *pourquoi* le pouce est baissé, à froid, sans revenir dans un salon chiffré ;
3. **la confiance moyenne par geste** — si les 👎 tombent sur des réponses à confiance
   basse, le signal de confiance vaut ; sinon il ne vaut rien, et c'est utile à savoir.

Les retours antérieurs au 30/08/2026 ne portent ni réponse ni sources : le champ
n'existait pas. Ils sont comptés, et signalés comme incomplets plutôt que rendus comme
des réponses vides.

USAGE
-----
    kubectl exec -n <ns> <pod> -- python /app/_chantier/scripts/relever_retours.py [espace]

Sans argument, relève tous les espaces déclarés.
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys


async def _espaces_connus(storage, config) -> list[str]:
    from colaig.context.resolver import ContextResolver
    resolver = ContextResolver(storage, config)
    await resolver.load_workspaces()
    return [w.storage_path for w in resolver.workspaces if w.storage_path]


async def _tours_repondus(storage, espace: str) -> int:
    """Combien de réponses ont été émises dans cet espace, tous salons confondus."""
    from colaig import paths
    total = 0
    try:
        fichiers = await storage.list_files(paths.conversations_dir(espace))
    except Exception:
        return 0
    for f in fichiers:
        chemin = getattr(f, "path", "") or ""
        if not chemin.endswith(".json") or "_trame" in chemin:
            continue
        try:
            messages = json.loads(await storage.download(chemin))
        except Exception:
            continue
        if isinstance(messages, dict):
            messages = messages.get("messages", [])
        total += sum(1 for m in messages if isinstance(m, dict)
                     and m.get("role") == "assistant")
    return total


def _abrege(texte: str, n: int = 220) -> str:
    texte = " ".join((texte or "").split())
    return texte if len(texte) <= n else texte[:n] + "…"


async def relever(storage, espace: str) -> None:
    from colaig.messaging.retours import lire_retours

    retours = await lire_retours(storage, espace)
    repondus = await _tours_repondus(storage, espace)

    print(f"\n{'=' * 78}\n{espace}\n{'=' * 78}")
    if not retours:
        print("  aucun retour enregistré.")
        if repondus:
            print(f"  {repondus} réponses émises, 0 geste — taux de retour : 0 %")
        return

    par_emoji: dict[str, list[dict]] = {}
    for r in retours:
        par_emoji.setdefault(r.get("emoji", "?"), []).append(r)

    taux = f"{len(retours) * 100 / repondus:.0f} %" if repondus else "inconnu"
    print(f"  {len(retours)} geste(s) sur {repondus or '?'} réponse(s) — "
          f"taux de retour : {taux}")

    print("\n  par geste :")
    for emoji, lot in sorted(par_emoji.items(), key=lambda kv: -len(kv[1])):
        confiances = [r["confiance"] for r in lot
                      if isinstance(r.get("confiance"), (int, float))]
        moyenne = (f"confiance moyenne {statistics.mean(confiances):.2f}"
                   if confiances else "confiance non enregistrée")
        print(f"    {emoji}  {len(lot):3d}   {moyenne}")

    negatifs = par_emoji.get("\U0001F44E", [])
    if not negatifs:
        print("\n  aucun 👎 — rien à instruire.")
        return

    print(f"\n  les {len(negatifs)} 👎, en entier :")
    for i, r in enumerate(negatifs, 1):
        print(f"\n  ── {i} ─────────────────────────────────────────────────")
        print(f"     question  : {_abrege(r.get('question', ''))}")
        reponse = r.get("reponse")
        if reponse is None:
            print("     réponse   : (retour antérieur au 30/08/2026 — champ absent)")
        else:
            print(f"     réponse   : {_abrege(reponse)}")
            print(f"     sources   : {', '.join(r.get('sources') or []) or '(aucune)'}")
            c = r.get("confiance")
            print(f"     confiance : {c if c is not None else '(non enregistrée)'}")


async def main() -> None:
    from colaig.config import load_config
    from colaig.main import create_storage

    config = load_config()
    storage = create_storage(config)

    espaces = sys.argv[1:] or await _espaces_connus(storage, config)
    if not espaces:
        print("aucun espace déclaré.")
        return
    for espace in espaces:
        await relever(storage, espace)


if __name__ == "__main__":
    asyncio.run(main())
