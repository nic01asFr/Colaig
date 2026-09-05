"""
Ce que les trois corrections de notation changent, mesuré avant de décider.

STATUT: COMPLET
VERSION: 2026-08-28 - v1.0
LOT: L1.5 (correction de la notation)

Pourquoi mesurer AVANT d'appliquer
------------------------------------
Chacune des trois corrections déplace une valeur de référence fondée sur dix-sept
observations. Les appliquer d'abord et regarder ensuite reviendrait à découvrir leur
effet à travers une porte rouge — c'est exactement ce qui a coûté la nuit du 27/08.

Les réponses sont archivées et la recherche est reproductible : tout se recompte sans
un seul appel de génération.

Les trois corrections
----------------------
**1. Reconnaître les références CCAG et d'annexe.** Onze cas sur 113 attendent
« CCAG Travaux 4 » ou « Annexe 2 — Seuils de procédure — texte 1 », que l'extracteur ne
peut pas produire : il ne connaît que `L/R/D` + chiffres. Le modèle répond
« CCAG Travaux, Article 4.1 » — juste, et compté faux.

La reconnaissance se fait DANS LA NOTATION, jamais dans l'extracteur : élargir
`articles_cites` à « Article 4 » créerait des faux positifs partout, y compris sur la
détection de fantômes.

**2. `cite_attendu_complet`, en plus et non à la place.** Onze cas attendent plusieurs
articles et la notation accepte l'un d'eux. `mp-002` dit pourtant « la réponse exige
l'article législatif ET l'article réglementaire ».

Le jeu doré n'est PAS modifié : ce serait encoder mon interprétation de onze
justifications. L'indicateur strict est ajouté À CÔTÉ, et l'arbitrage de le promouvoir
reste humain.

**3. `montants()` porté sur `lire_nombre`.** L'ancien motif voyait 4 % des grandeurs du
corpus — 89 % sont écrites en lettres.

Usage
-----
    set -a; . ./.env; set +a
    python _chantier/scripts/impact_corrections.py
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(RACINE))
sys.path.insert(0, str(RACINE / "_chantier" / "scripts"))
MESURES = RACINE / "_chantier" / "mesures"

SRC = (RACINE / "_chantier" / "scripts" / "reference_l15.py").read_text(encoding="utf-8")
_ns: dict = {"__name__": "gen",
             "__file__": str(RACINE / "_chantier" / "scripts" / "reference_l15.py")}
sys.argv = ["gen", "article"]
exec(compile(SRC.replace("raise SystemExit(main())", "pass"), "reference_l15.py", "exec"),
     _ns)
decouper, embed, cle_albert, FaissStore = (_ns["decouper"], _ns["embed"],
                                           _ns["cle_albert"], _ns["FaissStore"])

from nombres import montants as montants_neuf  # noqa: E402

from colaig.rag.verification_citations import articles_cites  # noqa: E402

K = 10
MONTANTS_ANCIEN = re.compile(r"\b\d{1,3}(?:[  \xa0]\d{3})+\b")


def montants_ancien(texte: str) -> set[str]:
    return {m.replace(" ", " ").replace("\xa0", " ")
            for m in MONTANTS_ANCIEN.findall(texte)}


def _normalise(m: set[str]) -> set[str]:
    """Compare les deux motifs sur le même terrain : des entiers sans séparateur."""
    return {re.sub(r"[^\d]", "", x) for x in m if re.sub(r"[^\d]", "", x)}


def cite_reference_libre(texte: str, ref: str) -> bool:
    """La réponse cite-t-elle une référence NON codifiée — CCAG, annexe ?

    « CCAG Travaux 4 »  → le document ET « article 4 » (ou 4.1, 4.2…).
    « … — texte 1 »     → le document seul : ces pseudo-articles expliquent COMMENT
                          choisir un CCAG et ne portent pas de numéro citable. Le
                          projet voisin les exclut de son corpus pour cette raison ;
                          citer le document EST alors la citation.

    La frontière de mot après le numéro évite de confondre l'article 4 et l'article 41.
    """
    ref = ref.strip()
    bas = texte.lower()
    pseudo = re.match(r"^(.*?)\s*[—–-]\s*texte\s+\d+$", ref, re.I)
    if pseudo:
        doc = pseudo.group(1).strip()
        return doc.lower() in bas
    m = re.match(r"^(.*?)\s+(\d+)$", ref)
    if not m:
        return ref.lower() in bas
    doc, num = m.group(1).strip(), m.group(2)
    doc = re.sub(r"\s*[—–-]\s*$", "", doc).split("—")[0].strip()
    if doc.lower() not in bas:
        return False
    return bool(re.search(rf"\barticles?\s+{num}(?![\d])", texte, re.I))


def cite(texte: str, attendus: list[str], codifie: dict[str, bool]) -> tuple[bool, bool]:
    """Rend (au moins un attendu cité, TOUS les attendus cités)."""
    trouves = articles_cites(texte)
    ok = [a in trouves if codifie[a] else cite_reference_libre(texte, a)
          for a in attendus]
    return any(ok), all(ok)


def main() -> int:
    cas = [json.loads(l) for l
           in (RACINE / "tests" / "golden" / "v1.jsonl").read_text(encoding="utf-8").splitlines()
           if l.strip()]
    positifs = [c for c in cas if c.get("articles_attendus")]
    codifie = {a: bool(articles_cites(f"article {a}") & {a})
               for c in positifs for a in c["articles_attendus"]}
    libres = sum(1 for c in positifs
                 if not any(codifie[a] for a in c["articles_attendus"]))
    print(f"{len(positifs)} cas positifs · {libres} à référence NON codifiée",
          file=sys.stderr)

    cle = cle_albert()
    chunks = decouper("article")
    store = FaissStore()
    store.add(embed([c.text for c in chunks], cle), chunks)
    vq = embed([c["question"] for c in positifs], cle)
    fournis_a, fournis_n = {}, {}
    for c, v in zip(positifs, vq):
        passages = [r.chunk.text for r in store.search(v, k=K)]
        fournis_a[c["id"]] = _normalise(set().union(*[montants_ancien(p) for p in passages]))
        fournis_n[c["id"]] = _normalise(set().union(*[montants_neuf(p) for p in passages]))

    lignes = []
    for arch in sorted(MESURES.glob("dispersion-durci-*.json")):
        rep = {r["id"]: r for r in json.loads(arch.read_text(encoding="utf-8"))}
        n = anc = ccag = complet = m_anc = m_neuf = 0
        for c in positifs:
            t = (rep.get(c["id"], {}).get("reponses") or [""])[0]
            if not t:
                continue
            n += 1
            trouves = articles_cites(t)
            if set(c["articles_attendus"]) & trouves:
                anc += 1
            un, tous = cite(t, c["articles_attendus"], codifie)
            ccag += un
            complet += tous
            q = c["question"]
            if _normalise(montants_ancien(t)) - fournis_a[c["id"]] - _normalise(montants_ancien(q)):
                m_anc += 1
            if _normalise(montants_neuf(t)) - fournis_n[c["id"]] - _normalise(montants_neuf(q)):
                m_neuf += 1
        lignes.append((arch.name, n, anc, ccag, complet, m_anc, m_neuf))

    moy = lambda i: statistics.mean(x[i] for x in lignes)  # noqa: E731
    n = moy(1)
    print(f"\n{len(lignes)} tirages · {n:.0f} cas positifs\n")
    print(f"{'indicateur':34} {'actuel':>9} {'corrigé':>9} {'écart':>9}")
    print(f"{'cite_attendu (CCAG reconnus)':34} {moy(2)/n:9.3f} {moy(3)/n:9.3f} "
          f"{(moy(3)-moy(2))/n:+9.3f}")
    print(f"{'cite_attendu_complet (nouveau)':34} {'—':>9} {moy(4)/n:9.3f} "
          f"{(moy(4)-moy(2))/n:+9.3f}")
    print(f"{'montants inventés (par exéc.)':34} {moy(5):9.2f} {moy(6):9.2f} "
          f"{moy(6)-moy(5):+9.2f}")

    (MESURES / "impact-corrections.json").write_text(
        json.dumps({"tirages": len(lignes), "cas": n,
                    "cite_attendu_actuel": round(moy(2) / n, 4),
                    "cite_attendu_ccag": round(moy(3) / n, 4),
                    "cite_attendu_complet": round(moy(4) / n, 4),
                    "montants_ancien": round(moy(5), 3),
                    "montants_neuf": round(moy(6), 3)},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
