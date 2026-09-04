"""Le fichier portant l'article attendu a-t-il ete SERVI ?

POURQUOI CE SCRIPT
------------------
`mesure_du_pod.py` note ce que le modele a CITE. Quand il ne cite pas l'article
attendu, deux causes tres differentes se confondent :

- le passage lui a ete servi et il ne s'en est pas saisi — defaut de redaction ;
- le passage ne lui a jamais ete servi — defaut de recherche.

Seule la seconde se corrige dans le retriever. Elle se lit dans le journal du pod :
`handlers` ecrit `sources=[...]` a chaque echange, pour le coeur comme pour le
pipeline agent.

CE QU'IL LIT, ET CE QU'IL NE RECONSTITUE PAS
----------------------------------------------
Les sources viennent du POD — journal de l'instance mesuree, pas d'une recherche
rejouee ici. Seule la carte « quel fichier porte quel article » est locale : c'est
le corpus qui a ete televerse, et il ne change pas.

    python _chantier/scripts/sources_servies_par_le_pod.py <fichier-de-mesure.json>
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
CORPUS = RACINE / "tests" / "golden" / "corpus-marches-publics"
JEU = RACINE / "tests" / "golden" / "v1.jsonl"
NAMESPACE = "user-nic01asfr"

_ARTICLE = re.compile(r"\b([LRD]\d{3,4}-\d+(?:-\d+)?)\b")
_ECHANGE = re.compile(r"question=(?P<q>.+?) sources=(?P<s>\[.*\])")


def _pod() -> str:
    out = subprocess.run(
        ["kubectl", "get", "pods", "-n", NAMESPACE,
         "-l", "app.kubernetes.io/instance=colaig-test",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


def _journal(pod: str) -> list[tuple[str, list[str]]]:
    """Rend [(question, sources)] dans l'ordre du journal."""
    out = subprocess.run(["kubectl", "logs", pod, "-n", NAMESPACE],
                         capture_output=True, text=True, encoding="utf-8", check=True)
    echanges: list[tuple[str, list[str]]] = []
    for ligne in out.stdout.splitlines():
        if "échange workspace=" not in ligne:
            continue
        try:
            event = json.loads(ligne)["event"]
        except Exception:  # noqa: BLE001
            continue
        m = _ECHANGE.search(event)
        if not m:
            continue
        # `question=` et `sources=` sont ecrits par %r : ce sont des litteraux Python.
        question = _litteral(m.group("q"))
        sources = [_litteral(x.strip()) for x in m.group("s")[1:-1].split(",") if x.strip()]
        echanges.append((question, sources))
    return echanges


def _litteral(texte: str) -> str:
    import ast
    try:
        return ast.literal_eval(texte)
    except Exception:  # noqa: BLE001
        return texte.strip("\"'")


def _carte_des_articles() -> dict[str, set[str]]:
    """article -> fichiers du corpus qui le portent."""
    carte: dict[str, set[str]] = {}
    for f in sorted(CORPUS.glob("*.md")):
        texte = f.read_text(encoding="utf-8", errors="ignore")
        for art in set(_ARTICLE.findall(texte)):
            carte.setdefault(art, set()).add(f.name)
    return carte


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    mesure = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    cas = {c["id"]: c for c in
           (json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip())}
    carte = _carte_des_articles()
    echanges = dict(_journal(_pod()))

    servis = non_servis = sans_journal = 0
    cite_sans_service = 0
    manques: list[tuple[str, list[str], list[str]]] = []
    frequence: dict[str, int] = {}
    vus = 0

    for r in mesure:
        if r.get("negatif"):
            continue
        c = cas.get(r["id"], {})
        attendus = list(c.get("articles_attendus") or [])
        if not attendus:
            continue
        vus += 1
        sources = echanges.get(r["question"])
        if sources is None:
            sans_journal += 1
            continue
        for s in sources:
            frequence[s] = frequence.get(s, 0) + 1
        porteurs: set[str] = set()
        for a in attendus:
            porteurs |= carte.get(a, set())
        if porteurs & set(sources):
            servis += 1
        else:
            non_servis += 1
            if r.get("cite_attendu"):
                cite_sans_service += 1
            manques.append((r["id"], attendus, sorted(porteurs)))

    print(f"cas positifs avec article attendu : {vus}")
    print(f"  fichier porteur SERVI           : {servis}")
    print(f"  fichier porteur NON servi       : {non_servis}")
    if cite_sans_service:
        print(f"    (dont cites quand meme        : {cite_sans_service} — memoire du modele)")
    if sans_journal:
        print(f"  absent du journal du pod        : {sans_journal}")
    if vus - sans_journal:
        moyenne = sum(frequence.values()) / (vus - sans_journal)
        print(f"  sources servies par question    : {moyenne:.1f} en moyenne")
    print("\nfichiers les plus servis :")
    for nom, n in sorted(frequence.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {100 * n / max(vus - sans_journal, 1):5.1f}%  {nom}")
    print(f"\ncas dont le porteur n'est pas servi ({len(manques)}) :")
    for cid, attendus, porteurs in manques:
        print(f"  {cid}  attendu {','.join(attendus)}  porte par {', '.join(porteurs) or '(aucun fichier)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
