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
ESPACE = "/colaig-mesure-marches-publics/"

_ARTICLE = re.compile(r"\b([LRD]\d{3,4}-\d+(?:-\d+)?)\b")
_ECHANGE = re.compile(r"question=(?P<q>.+?) sources=(?P<s>\[.*\])")


def _pod() -> str:
    out = subprocess.run(
        ["kubectl", "get", "pods", "-n", NAMESPACE,
         "-l", "app.kubernetes.io/instance=colaig-test",
         "-o", "jsonpath={.items[0].metadata.name}"],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


_LECTURE_DU_STOCKAGE = """
import asyncio, json
from colaig.config import load_config
from colaig.main import create_storage
from colaig.journal_echanges import lire_echanges
async def m():
    s = create_storage(load_config())
    for e in await lire_echanges(s, '{espace}'):
        print(json.dumps({{'q': e.get('question', ''), 's': e.get('sources', []),
                           'p': [x.get('section', '') for x in e.get('passages', [])]}},
                         ensure_ascii=False))
asyncio.run(m())
"""


def _journal_du_stockage(pod: str, espace: str) -> list[tuple[str, list[str], list[str]]]:
    """Lit le journal ECRIT PAR LE POD sur le stockage de l'espace.

    Prefere au journal du conteneur, qui tourne : sur une campagne de 135 questions
    aux sources longues, `kubectl logs` n'en rend que la fin (52 sur 113 le 04/09).
    Le journal de l'espace, lui, est complet et survit au redeploiement.
    """
    out = subprocess.run(
        ["kubectl", "exec", "-n", NAMESPACE, pod, "--",
         "python", "-c", _LECTURE_DU_STOCKAGE.format(espace=espace)],
        capture_output=True, text=True, encoding="utf-8")
    echanges: list[tuple[str, list[str]]] = []
    for ligne in out.stdout.splitlines():
        ligne = ligne.strip()
        if not ligne.startswith("{"):
            continue
        try:
            d = json.loads(ligne)
        except Exception:  # noqa: BLE001
            continue
        echanges.append((d["q"], d["s"], d.get("p", [])))
    return echanges


def _journal(pod: str) -> list[tuple[str, list[str], list[str]]]:
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
        echanges.append((question, sources, []))
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
    pod = _pod()
    brut = _journal_du_stockage(pod, ESPACE)
    if not brut:
        print("[!] journal de l'espace vide — repli sur le journal du conteneur,"
              " qui ne porte qu'une fin de campagne", file=sys.stderr)
        brut = _journal(pod)
    echanges = {q: (s_, p_) for q, s_, p_ in brut}
    au_passage = any(p_ for _, (_, p_) in echanges.items())

    servis = non_servis = sans_journal = sans_passages = 0
    fichier_sans_passage = 0
    cite_sans_service = 0
    manques: list[tuple[str, list[str], bool]] = []
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
        entree = echanges.get(r["question"])
        if entree is None:
            sans_journal += 1
            continue
        sources, sections = entree
        for x in sources:
            frequence[x] = frequence.get(x, 0) + 1

        # LE PASSAGE, PAS LE FICHIER. Le decoupage etant par article, un fichier en
        # porte des dizaines : servir `094-…contenu-du-marche.md` ne sert pas
        # `R2112-14`. Tant que le journal ne portait que des noms de fichiers, cette
        # mesure surestimait le service de 21 cas sur 102 (04/09/2026).
        titres = {t[len("Article "):] if t.startswith("Article ") else t for t in sections}
        au_niveau_du_passage = bool(titres & set(attendus))

        porteurs: set[str] = set()
        for a in attendus:
            porteurs |= carte.get(a, set())
        au_niveau_du_fichier = bool(porteurs & set(sources))

        if au_passage:
            if not sections:
                # Trace ecrite AVANT que le journal porte les passages : on ne peut
                # pas la juger au meme grain que les autres, et la compter au grain
                # du fichier gonflerait « fichier servi, passage absent ».
                sans_passages += 1
                continue
            if au_niveau_du_passage:
                servis += 1
            else:
                non_servis += 1
                if au_niveau_du_fichier:
                    fichier_sans_passage += 1
                if r.get("cite_attendu"):
                    cite_sans_service += 1
                manques.append((r["id"], attendus, au_niveau_du_fichier))
        else:
            if au_niveau_du_fichier:
                servis += 1
            else:
                non_servis += 1
                manques.append((r["id"], attendus, False))

    grain = "PASSAGE" if au_passage else "fichier (journal ancien)"
    print(f"granularite                      : {grain}")
    print(f"cas positifs avec article attendu : {vus}")
    print(f"  article attendu SERVI           : {servis}")
    print(f"  article attendu NON servi       : {non_servis}")
    if fichier_sans_passage:
        print(f"    dont son FICHIER etait servi  : {fichier_sans_passage}"
              "  (la recherche s'arrete a quelques rangs)")
    if cite_sans_service:
        print(f"    dont cites quand meme         : {cite_sans_service} — memoire du modele")
    if sans_journal:
        print(f"  absent du journal du pod        : {sans_journal}")
    if sans_passages:
        print(f"  trace anterieure au champ passages : {sans_passages} — non jugees")
    print()
    print("fichiers les plus servis :")
    for nom, n in sorted(frequence.items(), key=lambda kv: -kv[1])[:6]:
        print(f"  {100 * n / max(vus - sans_journal, 1):5.1f}%  {nom}")
    proches = [m for m in manques if m[2]]
    print()
    print(f'cas manquants dont le fichier etait pourtant servi ({len(proches)}) :')
    for cid, attendus, _ in proches:
        print(f"  {cid}  attendu {','.join(attendus)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
