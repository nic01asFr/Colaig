"""HyDE aide-t-il, et si oui quand ?

La logique, généralisée
------------------------
Le vérificateur de fidélité a été branché sur un **signal mesuré** plutôt que sur une
intuition (D26). La même logique s'applique aux autres options coûteuses du pipeline,
en deux temps qu'il ne faut pas confondre :

1. **Est-ce que ça aide ?** Sans quoi il n'y a rien à déclencher.
2. **Quand est-ce que ça aide ?** Seulement si la réponse à (1) est oui.

`COLAIG_HYDE_ENABLED` existe dans `config.py` depuis l'origine et n'a jamais franchi
l'étape 1. C'est l'option dont le coût est **par requête** — un appel LLM avant chaque
recherche — donc celle où un déclencheur aurait le plus de valeur.

Ce qui est mesuré
-----------------
Le retriever combine `(1-w) x embedding(question) + w x embedding(réponse hypothétique)`,
puis normalise. Trois poids sont éprouvés, contre le témoin sans HyDE, sur la même
métrique que la référence : **tous les articles attendus remontent-ils**.

Le poids compte : à `w = 1` on cherche avec la réponse imaginée du modèle et l'on a
remplacé la question de l'utilisateur par une hypothèse. Mesurer un seul poids ne dirait
donc pas si HyDE aide, mais si *ce* réglage aide.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(RACINE))

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
articles_du_chunk = _ns["articles_du_chunk"]

from colaig.rag.faiss_store import FaissStore  # noqa: E402

JEU = RACINE / "tests" / "golden" / "v1.jsonl"
BASE = "https://llm.lab.sspcloud.fr/api/v1"
MODELE = "qwen3-6-35b-moe"
POIDS = (0.3, 0.5, 0.7)


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


def reponse_hypothetique(question: str, cle: str) -> str:
    """Ce que le modèle imaginerait comme réponse, sans avoir vu le corpus.

    Raisonnement coupé, comme partout ailleurs depuis D18 : ici il n'apporterait rien
    qu'une latence, l'objet étant d'obtenir un texte *plausible*, pas *juste*.
    """
    corps = {
        "model": MODELE,
        "messages": [
            {"role": "system",
             "content": "Tu es un expert documentaire. Génère une réponse concise et factuelle."},
            {"role": "user",
             "content": f"Question : {question}\n\nRéponse hypothétique en deux phrases :"},
        ],
        "temperature": 0.1, "max_tokens": 300,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(BASE + "/chat/completions",
                                 data=json.dumps(corps).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + cle)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as rep:
        return json.loads(rep.read().decode())["choices"][0]["message"].get("content") or ""


def combiner(vq: list[float], vh: list[float], poids: float) -> list[float]:
    melange = [(1 - poids) * a + poids * b for a, b in zip(vq, vh)]
    norme = sum(x * x for x in melange) ** 0.5
    return [x / norme for x in melange] if norme else vq


def main() -> int:
    cle_a, cle_s = cle_albert(), cle_ssp()
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]
    avec = [c for c in cas if c.get("articles_attendus")]

    chunks = decouper("article")
    store = FaissStore()
    store.add(embed([c.text for c in chunks], cle_a), chunks)
    vq = embed([c["question"] for c in avec], cle_a)

    t0 = time.monotonic()
    hypotheses = []
    for i, c in enumerate(avec, 1):
        hypotheses.append(reponse_hypothetique(c["question"], cle_s))
        if i % 20 == 0:
            print(f"  hypothèses {i}/{len(avec)}", file=sys.stderr)
    duree_hyde = (time.monotonic() - t0) / len(avec)
    vh = embed(hypotheses, cle_a)

    def evaluer(nom, vecteurs):
        complets = 0
        echecs = []
        for c, v in zip(avec, vecteurs):
            attendus = set(c["articles_attendus"])
            trouves: set[str] = set()
            for r in store.search(v, k=6):
                trouves |= articles_du_chunk(r.chunk.text)
            if attendus <= trouves:
                complets += 1
            else:
                echecs.append(c["id"])
        print(f"  {nom:22} {complets:3}/{len(avec)}  ({100 * complets / len(avec):.0f} %)")
        return complets, set(echecs)

    print(f"\n{len(avec)} cas · coût HyDE : {duree_hyde:.2f} s par question\n")
    base, echecs_base = evaluer("témoin (sans HyDE)", vq)
    resultats = {}
    for p in POIDS:
        melanges = [combiner(a, b, p) for a, b in zip(vq, vh)]
        resultats[p] = evaluer(f"HyDE w={p}", melanges)

    # Ce que HyDE change vraiment, cas par cas : les gains nets et les pertes nettes.
    print("\nCas basculés par rapport au témoin :")
    for p, (n, echecs) in resultats.items():
        gagnes = sorted(echecs_base - echecs)
        perdus = sorted(echecs - echecs_base)
        print(f"  w={p}  gagnés {len(gagnes)} {gagnes[:6]}  ·  perdus {len(perdus)} {perdus[:6]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
