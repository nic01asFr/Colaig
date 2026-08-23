"""Le vérificateur de fidélité, mesuré sur les réponses réellement produites.

Ce que ce harnais tranche
-------------------------
`verification_citations` contrôle la provenance et ne peut rien dire de plus. Il reste
un mode d'échec qu'il laisse passer entièrement : **la provenance est correcte et
l'inférence déborde**. `mp-032` en est le représentant — le modèle cite `R2191-3`, qui
est bien dans les passages, et en tire une réponse que l'article ne dit pas.

Il n'y a pas de contrôle mécanique pour cela : il faut lire. Ce harnais mesure si un
modèle, cantonné à la recette de contexte la plus pauvre possible, le voit.

Le découpage, qui est le vrai choix de conception
--------------------------------------------------
Une réponse entière n'est pas une affirmation, et la vérifier d'un bloc ne donnerait
qu'un verdict global inexploitable — « partiellement étayé » ne dit pas *quelle* phrase
déborde.

Le découpage retenu : **une phrase portant une référence d'article, confrontée au
passage qui contient cet article**. C'est l'appariement le plus serré possible, et il
cible exactement le mode d'échec visé — une phrase qui invoque un article pour dire
autre chose que ce qu'il dit.

Les phrases sans référence sont écartées ici. Ce n'est pas qu'elles soient sûres : c'est
que `garde_fou_reponse` les traite déjà, en remplaçant par un refus toute réponse sans
attache. Vérifier deux fois la même chose coûterait sans rien apprendre.

Coût
----
Un appel par couple (phrase, article cité), à température nulle. `--limite` borne le
nombre de cas traités : la première exécution doit prouver le mécanisme, pas balayer.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1].parent
sys.path.insert(0, str(RACINE))

# argv capture avant ecrasement — meme piege que partout ailleurs dans ce harnais.
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
from colaig.rag.verificateur_fidelite import verifier_fidelite  # noqa: E402

JEU = RACINE / "tests" / "golden" / "v1.jsonl"
MESURES = RACINE / "_chantier" / "mesures"
BASE_SSP = "https://llm.lab.sspcloud.fr/api/v1"
MODELE = "qwen3-6-35b-moe"


class ClientSSP:
    """`LLMClientProtocol` réduit à ce dont le vérificateur a besoin.

    Le harnais de mesure n'a pas à monter toute l'injection de dépendances de
    `main.py` : il lui faut `chat()`, et rien d'autre.
    """

    def __init__(self, cle: str) -> None:
        self._cle = cle

    async def chat(self, messages, model=None, temperature=0.3, max_tokens=2048,
                   priority="user") -> str:
        import urllib.request

        # Raisonnement coupe par defaut, comme la generation (D18) : il coute un
        # facteur dix en latence. Reste a savoir s'il change les VERDICTS — c'est
        # justement ce qu'un controle doit verifier avant de s'en priver, et
        # COLAIG_VERIF_RAISONNEMENT=1 permet de le mesurer.
        corps = {"model": MODELE, "messages": messages,
                 "temperature": temperature, "max_tokens": max_tokens}
        if os.environ.get("COLAIG_VERIF_RAISONNEMENT", "0") != "1":
            corps["chat_template_kwargs"] = {"enable_thinking": False}
        charge = json.dumps(corps).encode()
        req = urllib.request.Request(BASE_SSP + "/chat/completions", data=charge, method="POST")
        req.add_header("Authorization", "Bearer " + self._cle)
        req.add_header("Content-Type", "application/json")
        boucle = asyncio.get_running_loop()
        rep = await boucle.run_in_executor(
            None, lambda: urllib.request.urlopen(req, timeout=300).read())
        return json.loads(rep.decode())["choices"][0]["message"].get("content") or ""


def cle_ssp() -> str:
    for ligne in open(RACINE / ".env", encoding="utf-8"):
        if ligne.strip().lower().startswith("sspcloud_api_key="):
            return ligne.split("=", 1)[1].strip()
    raise SystemExit("clé SSPCloud introuvable")


def phrases(texte: str) -> list[str]:
    """Découpe en phrases, en gardant celles qui avancent quelque chose.

    Le seuil de longueur écarte les fragments de liste et les titres : une phrase de
    moins de quarante caractères n'énonce pas une règle de droit.
    """
    brutes = re.split(r"(?<=[.;])\s+", " ".join((texte or "").split()))
    return [p for p in brutes if len(p) >= 40]


def passage_de(article: str, passages: list[str]) -> str | None:
    """Le passage qui contient cet article, s'il y en a un."""
    for p in passages:
        if article in articles_cites(p):
            return p
    return None


async def main() -> int:
    limite = int(_ARGS[0]) if _ARGS and _ARGS[0].isdigit() else 0
    stockees = sorted(MESURES.glob("reponses-*.json"))
    if not stockees:
        raise SystemExit("aucune réponse stockée : lancer reference_generation.py d'abord")
    fichier = stockees[-1]
    reponses = {r["id"]: r for r in json.loads(fichier.read_text(encoding="utf-8"))}
    print(f"réponses lues : {fichier.name} ({len(reponses)} cas)")

    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]
    cas = [c for c in cas if c["id"] in reponses]
    if limite:
        cas = cas[:limite]

    cle_a, cle_s = cle_albert(), cle_ssp()
    chunks = decouper("article")
    store = FaissStore()
    store.add(embed([c.text for c in chunks], cle_a), chunks)
    vq = embed([c["question"] for c in cas], cle_a)
    client = ClientSSP(cle_s)

    resultats = []
    t0 = time.monotonic()
    for c, v in zip(cas, vq):
        passages = [r.chunk.text for r in store.search(v, k=6)]
        reponse = (reponses[c["id"]]["reponses"] or [""])[0]

        couples = []
        for phrase in phrases(reponse):
            for article in sorted(articles_cites(phrase)):
                extrait = passage_de(article, passages)
                if extrait:
                    couples.append((phrase, article, extrait))

        verdicts = []
        for phrase, article, extrait in couples:
            f = await verifier_fidelite(phrase, extrait, client)
            verdicts.append({"article": article, "verdict": f.verdict,
                             "ancre": f.appui_dans_extrait, "motif": f.motif,
                             "phrase": phrase[:160]})
        resultats.append({"id": c["id"], "type": c["type"], "difficulte": c["difficulte"],
                          "verdicts": verdicts})
        pires = [v["verdict"] for v in verdicts]
        drapeau = "!" if any(p in ("ne_dit_pas_cela", "contredit") for p in pires) else " "
        print(f" {drapeau} {c['id']}  {len(verdicts)} couple(s)  {', '.join(pires) or '—'}")

    (MESURES / f"fidelite-{time.strftime('%Y%m%d')}.json").write_text(
        json.dumps(resultats, ensure_ascii=False, indent=1), encoding="utf-8")

    tous = [v for r in resultats for v in r["verdicts"]]
    compte: dict[str, int] = {}
    for v in tous:
        compte[v["verdict"]] = compte.get(v["verdict"], 0) + 1
    print(f"\n{len(tous)} couples vérifiés en {time.monotonic() - t0:.0f} s")
    for k, n in sorted(compte.items(), key=lambda x: -x[1]):
        print(f"  {k:22} {n}")
    non_ancres = sum(1 for v in tous
                     if v["verdict"] in ("etaye", "etaye_partiellement") and not v["ancre"])
    print(f"  {'appui fabriqué':22} {non_ancres}   (verdict positif sans portion verbatim)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
