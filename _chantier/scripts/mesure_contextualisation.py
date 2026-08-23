"""La contextualisation des passages par LLM aide-t-elle ?

Dernière option coûteuse du pipeline jamais mesurée
----------------------------------------------------
`COLAIG_CONTEXTUAL_CHUNKING_ENABLED` fait générer par un LLM, à l'indexation, un préfixe
d'une à deux phrases par passage — la technique dite *Contextual Retrieval*. Le coût est
**un appel par passage**, donc 699 appels pour ce corpus : payé une fois, mais à
nouveau à chaque ré-indexation.

Le témoin n'est pas rien, et c'est ce qui rend la mesure intéressante
---------------------------------------------------------------------
Le découpage par article **préfixe déjà chaque passage** du titre du document et de sa
position dans le code — « Partie législative › DEUXIÈME PARTIE › Livre Ier › Titre Ier ».
Ce préfixe est gratuit et vaut, mesuré, **3 cas sur 104** (D12).

La question n'est donc pas « le contexte aide-t-il ? » — il aide, c'est acquis — mais
**« un contexte écrit par un LLM vaut-il mieux qu'un chemin hiérarchique gratuit ? »**.

Trois variantes sont confrontées : sans préfixe du tout, avec le chemin hiérarchique
seul, et avec le chemin plus le contexte généré.
"""
from __future__ import annotations

import json
import os
import re
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
articles_du_chunk, CORPUS = _ns["articles_du_chunk"], _ns["CORPUS"]

from colaig.rag.faiss_store import FaissStore  # noqa: E402

JEU = RACINE / "tests" / "golden" / "v1.jsonl"
MESURES = RACINE / "_chantier" / "mesures"
BASE = "https://llm.lab.sspcloud.fr/api/v1"
MODELE = "qwen3-6-35b-moe"
CACHE = MESURES / "contextes-passages.json"


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


def contexte(texte: str, cle: str) -> str:
    corps = {
        "model": MODELE,
        "messages": [
            {"role": "system", "content":
                "Tu es un assistant spécialisé dans la contextualisation de documents. "
                "Ta tâche : générer un bref contexte (1-2 phrases maximum) pour un extrait, "
                "en le situant dans son document et dans son domaine. Réponds UNIQUEMENT "
                "avec ce contexte, sans introduction."},
            {"role": "user", "content":
                "Workspace : Marchés publics\nDomaine : Assistance à la rédaction de "
                "marchés publics, Code de la commande publique\n\nExtrait à "
                f"contextualiser :\n{texte[:2000]}\n\nGénère un contexte de 1-2 phrases."},
        ],
        "temperature": 0.1, "max_tokens": 200,
        "chat_template_kwargs": {"enable_thinking": False},
    }
    req = urllib.request.Request(BASE + "/chat/completions",
                                 data=json.dumps(corps).encode(), method="POST")
    req.add_header("Authorization", "Bearer " + cle)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=120) as rep:
        return (json.loads(rep.read().decode())["choices"][0]["message"].get("content") or "").strip()


def sans_prefixe(chunk_texte: str) -> str:
    """Le corps de l'article seul, sans titre de document ni chemin hiérarchique."""
    lignes = chunk_texte.split("\n")
    for i, ligne in enumerate(lignes):
        if ligne.startswith("Article "):
            return "\n".join(lignes[i:])
    return chunk_texte


def main() -> int:
    cle_a, cle_s = cle_albert(), cle_ssp()
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]
    avec = [c for c in cas if c.get("articles_attendus")]
    chunks = decouper("article")
    print(f"{len(chunks)} passages · {len(avec)} cas\n")

    # Le cache évite de repayer les 699 appels à chaque exécution du banc.
    if CACHE.exists():
        contextes = json.loads(CACHE.read_text(encoding="utf-8"))
        print(f"contextes relus du cache ({len(contextes)})")
    else:
        contextes = {}
    manquants = [c for c in chunks if c.section not in contextes]
    if manquants:
        t0 = time.monotonic()
        for i, ch in enumerate(manquants, 1):
            contextes[ch.section] = contexte(ch.text, cle_s)
            if i % 50 == 0:
                print(f"  contextes {i}/{len(manquants)}", file=sys.stderr)
        CACHE.write_text(json.dumps(contextes, ensure_ascii=False), encoding="utf-8")
        print(f"coût : {(time.monotonic() - t0) / len(manquants):.2f} s par passage "
              f"× {len(chunks)} = {(time.monotonic() - t0) / 60:.0f} min\n")

    vq = embed([c["question"] for c in avec], cle_a)

    def evaluer(nom, textes):
        store = FaissStore()
        store.add(embed(textes, cle_a), chunks)
        complets, echecs = 0, []
        for c, v in zip(avec, vq):
            attendus = set(c["articles_attendus"])
            trouves: set[str] = set()
            for r in store.search(v, k=6):
                trouves |= articles_du_chunk(r.chunk.text)
            if attendus <= trouves:
                complets += 1
            else:
                echecs.append(c["id"])
        print(f"  {nom:34} {complets:3}/{len(avec)}  ({100 * complets / len(avec):.0f} %)")
        return set(echecs)

    e1 = evaluer("sans préfixe", [sans_prefixe(c.text) for c in chunks])
    e2 = evaluer("chemin hiérarchique (production)", [c.text for c in chunks])
    e3 = evaluer("chemin + contexte LLM",
                 [contextes.get(c.section, "") + "\n\n" + c.text for c in chunks])

    print("\nCas basculés par rapport au chemin hiérarchique seul :")
    print(f"  sans préfixe          gagnés {sorted(e2 - e1)[:6]}  perdus {sorted(e1 - e2)[:6]}")
    print(f"  chemin + contexte LLM gagnés {sorted(e2 - e3)[:6]}  perdus {sorted(e3 - e2)[:6]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
