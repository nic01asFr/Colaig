"""Quel signal prédit qu'une réponse mérite d'être vérifiée ?

Le problème
-----------
Le vérificateur de fidélité coûte environ une seconde par couple, soit **quatre
secondes ajoutées à une réponse qui en prend deux**. Le passer sur tout triplerait la
latence qu'on vient de gagner en coupant le raisonnement.

D'où la question : peut-on ne l'appeler que là où il sert ?

La contrainte qui élimine la plupart des idées
-----------------------------------------------
Le signal doit être calculable **au moment de la réponse**, à partir de la question, des
passages et du texte produit. Tout ce qui suppose de connaître la bonne réponse est
hors jeu — c'est disponible sur un jeu doré, jamais chez l'utilisateur.

Cela écarte d'emblée la difficulté déclarée du cas, qui est une étiquette de mesure et
non une propriété observable.

Ce qui est mis à l'épreuve
---------------------------
| signal | intuition |
|---|---|
| score du premier passage | un ancrage faible annonce une réponse mal fondée |
| dispersion des scores | mesurée prédictive de l'échec de recherche (D11) — l'est-elle de l'infidélité ? |
| nombre d'articles cités | plus on cite, plus on a d'occasions de mal citer |
| longueur de la réponse | une réponse longue s'éloigne davantage de ses sources |
| citations par phrase | densité d'affirmations à étayer |

Ce que ce banc conclut n'est **pas** un modèle : c'est un tri entre des signaux qui
séparent et des signaux qui ne séparent pas. Un signal qui ne sépare pas ne doit pas
servir de déclencheur, quelle que soit son évidence apparente.
"""
from __future__ import annotations

import json
import statistics
import sys
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
from colaig.rag.verification_citations import articles_cites  # noqa: E402

MESURES = RACINE / "_chantier" / "mesures"
JEU = RACINE / "tests" / "golden" / "v1.jsonl"


def separation(nom: str, sains: list[float], suspects: list[float]) -> None:
    """Affiche si le signal sépare, et de combien.

    On compare les médianes plutôt que les moyennes : quelques valeurs extrêmes
    suffiraient à faire croire à une séparation qui n'existe pas.
    """
    if not sains or not suspects:
        print(f"  {nom:26} — pas assez d'observations")
        return
    ms, mx = statistics.median(sains), statistics.median(suspects)
    ecart = (mx - ms) / ms * 100 if ms else float("inf")
    verdict = "SÉPARE" if abs(ecart) >= 20 else "ne sépare pas"
    print(f"  {nom:26} sains {ms:7.3f} · suspects {mx:7.3f} · écart {ecart:+6.0f} %  {verdict}")


def main() -> int:
    fidelite = json.loads((MESURES / "fidelite-20260823.json").read_text(encoding="utf-8"))
    stockees = sorted(MESURES.glob("reponses-*.json"))
    reponses = {r["id"]: r for r in json.loads(stockees[-1].read_text(encoding="utf-8"))}
    cas = {json.loads(l)["id"]: json.loads(l)
           for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()}

    mesures = [f for f in fidelite if f["verdicts"]]
    cle = cle_albert()
    chunks = decouper("article")
    store = FaissStore()
    store.add(embed([c.text for c in chunks], cle), chunks)
    vq = embed([cas[f["id"]]["question"] for f in mesures], cle)

    lignes = []
    for f, v in zip(mesures, vq):
        trouves = store.search(v, k=6)
        scores = [r.score for r in trouves]
        texte = (reponses[f["id"]]["reponses"] or [""])[0]
        suspect = any(x["verdict"] in ("ne_dit_pas_cela", "contredit") for x in f["verdicts"])
        lignes.append({
            "id": f["id"],
            "suspect": suspect,
            "score_1": scores[0] if scores else 0.0,
            "dispersion": (max(scores) - min(scores)) / max(scores) if scores else 0.0,
            "articles": float(len(articles_cites(texte))),
            "longueur": float(len(texte)),
            "citations_par_phrase": len(articles_cites(texte)) / max(1, texte.count(".")),
            "couples": float(len(f["verdicts"])),
        })

    sains = [x for x in lignes if not x["suspect"]]
    suspects = [x for x in lignes if x["suspect"]]
    print(f"{len(lignes)} réponses mesurées · {len(suspects)} portent au moins un verdict négatif\n")
    for signal in ("score_1", "dispersion", "articles", "longueur",
                   "citations_par_phrase", "couples"):
        separation(signal, [x[signal] for x in sains], [x[signal] for x in suspects])

    # Ce qu'un seuil sur le meilleur signal donnerait réellement.
    print("\nCe qu'un déclencheur donnerait, signal par signal :")
    for signal in ("articles", "couples", "longueur", "dispersion"):
        valeurs = sorted(x[signal] for x in lignes)
        for part in (0.3, 0.5):
            seuil = valeurs[int(len(valeurs) * (1 - part))]
            declenches = [x for x in lignes if x[signal] >= seuil]
            pris = sum(1 for x in declenches if x["suspect"])
            if declenches:
                print(f"  {signal:20} seuil {seuil:7.2f} → {len(declenches):2} appels "
                      f"({100 * len(declenches) / len(lignes):3.0f} %) "
                      f"attrapent {pris}/{len(suspects)} suspects")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
