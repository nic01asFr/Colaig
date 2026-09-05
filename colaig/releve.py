"""
Relevé de ce que Colaig a fait — l'instrument de la porte 1.

POURQUOI CE SCRIPT EXISTE
---------------------------
Le franchissement de la porte 1 demande *« une semaine de dogfooding sur un pod de
test, relevé des 👍👎 et incidents »*. Sans instrument de lecture, cette semaine produit
un dossier de fichiers JSON qu'il faut ouvrir un par un.

CE QUI A CHANGÉ LE 30/08/2026 AU SOIR
---------------------------------------
La première version ne lisait que les 👍👎. Le taux de retour mesuré est de **17 %** —
un geste sur six réponses — et l'utilisateur a dit qu'il ne tiendrait pas le protocole.

Les pouces n'étaient qu'un **proxy** pour « la réponse était-elle bonne ». Colaig
consigne désormais chaque échange sur le stockage : question, réponse, sources,
confiance, temps. C'est plus riche qu'un pouce et cela ne demande rien à personne.

Le relevé lit donc les **deux** sources. Un 👍 garde sa valeur — il dit ce qu'un humain
a pensé, et rien ne le déduit — mais il n'a plus le monopole de l'observation.

Ce script rend :

1. **le taux de retour** — combien de réponses ont reçu un geste. C'est la première
   chose que la semaine doit mesurer : un dogfooding sans gestes ne mesure rien ;
2. **chaque 👎 en entier** — question, réponse, sources, confiance. C'est ce qui permet
   de dire *pourquoi* le pouce est baissé, à froid, sans revenir dans un salon chiffré ;
3. **la confiance moyenne par geste** — si les 👎 tombent sur des réponses à confiance
   basse, le signal de confiance vaut ; sinon il ne vaut rien, et c'est utile à savoir.

Les retours antérieurs au 30/08/2026 ne portent ni réponse ni sources : le champ
n'existait pas. Ils sont comptés, et signalés comme incomplets plutôt que rendus comme
des réponses vides.

OÙ CE MODULE VIT, ET POURQUOI
-------------------------------
Dans `colaig/`, pas dans `_chantier/scripts/`. C'est un outil d'EXPLOITATION : il doit
être disponible là où Colaig tourne, avec les identifiants de l'instance. L'image ne
copie que `colaig/`, `config/` et `tests/` — un script de chantier n'y est pas, et un
instrument qu'on doit copier à la main avant chaque usage n'est pas un instrument.

    kubectl exec -n <ns> <pod> -- python -m colaig.releve [espace]

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


async def relever_echanges(storage, espace: str) -> None:
    """Ce que Colaig a fait, sans qu'on lui ait rien demandé."""
    from colaig.journal_echanges import lire_echanges

    echanges = await lire_echanges(storage, espace)
    if not echanges:
        print("  aucun échange consigné — le journal date du 30/08/2026 au soir ;")
        print("  les échanges antérieurs ne vivaient que dans le pod.")
        return

    confiances = [e["confiance"] for e in echanges
                  if isinstance(e.get("confiance"), (int, float))]
    sans_source = [e for e in echanges if not e.get("sources")]
    temps = sorted(e.get("temps_ms", 0) for e in echanges)

    print(f"  {len(echanges)} échange(s) consigné(s)")
    if confiances:
        confiances_triees = sorted(confiances)
        med = confiances_triees[len(confiances_triees) // 2]
        basses = [c for c in confiances if c < 0.6]
        print(f"    confiance médiane      : {med:.2f}")
        print(f"    sous 0,60              : {len(basses)} "
              f"({len(basses) * 100 // len(confiances)} %)")
    print(f"    sans aucune source     : {len(sans_source)} "
          f"({len(sans_source) * 100 // len(echanges)} %)")
    if temps:
        print(f"    temps médian           : {temps[len(temps) // 2]} ms")

    faibles = sorted((e for e in echanges
                      if isinstance(e.get("confiance"), (int, float))
                      and e["confiance"] < 0.6),
                     key=lambda e: e["confiance"])
    if faibles:
        print(f"\n  les {min(len(faibles), 5)} échanges les moins assurés :")
        for e in faibles[:5]:
            print(f"    {e['confiance']:.2f}  {_abrege(e.get('question', ''), 90)}")
            print(f"          → {_abrege(e.get('reponse', ''), 90)}")


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
        print()
        print("  ── ce que Colaig a fait ─────────────────────────────────")
        await relever_echanges(storage, espace)


if __name__ == "__main__":
    asyncio.run(main())
