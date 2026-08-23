"""
Référence de mesure — lot L1.5.

STATUT: COMPLET
VERSION: 2026-08-23 - v1.0
LOT: L1.5

Établit le **rapport de référence** contre lequel toute modification du pipeline se
jugera. Produit `docs/baseline-AAAAMMJJ.md`.

Ce qui est mesuré, et dans quel ordre
--------------------------------------
**Palier 1 — déterministe, sans juge.** La récupération. L'article attendu figure-t-il
dans les passages remontés, et à quel rang ? Deux exécutions donnent le même chiffre.
C'est le socle : si la récupération échoue, aucune génération ne rattrape — le document
n'est pas là.

**Palier 2 — jugé, donc variable.** La génération : la réponse cite-t-elle l'article ?
refuse-t-elle sur un cas négatif ? Ces chiffres dépendent d'un modèle et **varient d'une
exécution à l'autre**. Ils sont rapportés séparément et marqués comme tels. En faire le
socle d'une référence reviendrait à reproduire le « ça a l'air mieux » que ce chantier
combat.

Composants réels
----------------
`Chunker` et `FaissStore` de Colaig, paramètres de `config.py` (800/100). Embeddings
`bge-m3` — 1024 dimensions, défaut retenu en D10. Une réimplémentation mesurerait autre
chose que ce qui tourne.
"""
from __future__ import annotations

import json
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RACINE))

from colaig.rag.chunker import Chunker  # noqa: E402
from colaig.rag.faiss_store import FaissStore  # noqa: E402

CORPUS = RACINE / "tests" / "golden" / "corpus-marches-publics"
JEU = RACINE / "tests" / "golden" / "v1.jsonl"

MODELE_EMBED = "BAAI/bge-m3"
DIMENSION = 1024
BASE_ALBERT = "https://albert.api.etalab.gouv.fr/v1"
K = 6  # max_results de la configuration de l'espace

# Stratégie de découpage, choisie par argument. `article` respecte la frontière
# d'article ; `chunker` est le découpage en vigueur (800/100), qui sert de témoin.
# La référence du 23/08 a diagnostiqué deux échecs sur trois comme des défauts de
# granularité : le bon document remontait, pas le bon passage. C'est une hypothèse,
# et elle se mesure contre le témoin — elle ne se décrète pas.
STRATEGIE = sys.argv[1] if len(sys.argv) > 1 else "chunker"


def cle_albert() -> str:
    for ligne in open(RACINE.parent / "colaig-v3" / ".env", encoding="utf-8"):
        if ligne.strip().startswith("ALBERT_API_KEY="):
            return ligne.split("=", 1)[1].strip()
    raise SystemExit("clé Albert introuvable")


def embed(textes: list[str], cle: str, lot: int = 32) -> list[list[float]]:
    vecteurs: list[list[float]] = []
    for i in range(0, len(textes), lot):
        charge = json.dumps({"model": MODELE_EMBED, "input": textes[i:i + lot]}).encode()
        req = urllib.request.Request(BASE_ALBERT + "/embeddings", data=charge, method="POST")
        req.add_header("Authorization", "Bearer " + cle)
        req.add_header("Content-Type", "application/json")
        for essai in range(3):
            try:
                with urllib.request.urlopen(req, timeout=180) as rep:
                    donnees = json.loads(rep.read().decode())["data"]
                vecteurs.extend(e["embedding"] for e in sorted(donnees, key=lambda e: e["index"]))
                break
            except urllib.error.HTTPError as e:
                if essai == 2:
                    raise SystemExit(f"embeddings en échec : {e.code} {e.read().decode()[:160]}")
                time.sleep(2 * (essai + 1))
        print(f"  embeddings {min(i+lot, len(textes))}/{len(textes)}", end="\r", file=sys.stderr)
    print(file=sys.stderr)
    return vecteurs


_LIVRE_IER: dict[str, bool] = {}


def _du_livre_ier(nom_fichier: str) -> bool:
    """Le document appartient-il a la deuxieme partie, livre Ier ?"""
    if nom_fichier not in _LIVRE_IER:
        contenu = (CORPUS / nom_fichier).read_text(encoding="utf-8")
        entete = contenu.split("---", 1)[0]
        position = ""
        for ligne in entete.splitlines():
            if ligne.startswith("> ") and "›" in ligne:
                position = ligne
        _LIVRE_IER[nom_fichier] = (
            "DEUXI" in position and "Livre Ier" in position
            and "Livre II" not in position.replace("Livre Ier", "")
        )
    return _LIVRE_IER[nom_fichier]


def decouper(strategie: str):
    """Produit les chunks selon la stratégie demandée."""
    from colaig.models import DocumentChunk

    if strategie == "chunker":
        chunker = Chunker(chunk_size=800, chunk_overlap=100)
        chunks = []
        for fichier in sorted(CORPUS.glob("*.md")):
            chunks.extend(chunker.chunk_document(
                content=fichier.read_text(encoding="utf-8"),
                source_path=fichier.name, doc_type="md"))
        return chunks

    if strategie == "article-livre1":
        # Meme decoupage que « article », restreint au regime des MARCHES PUBLICS
        # ORDINAIRES — deuxieme partie, livre Ier.
        #
        # Mesure : ce livre ne represente que 38 % du corpus. Les 62 % restants sont le
        # livre defense-securite (23 %), les concessions, les marches de partenariat et
        # l'outre-mer. Or les 117 articles attendus par le jeu dore sont TOUS dans le
        # livre Ier : aucun cas ne mobilise le reste.
        #
        # Le probleme n'est pas seulement le bruit. Le livre defense-securite contient
        # des JUMEAUX TEXTUELS aux seuils differents — R2122-8 dit 60 000 euros,
        # R2322-14 dit 100 000 euros pour la meme regle. Une question posee sans ancrage
        # de livre est donc ambigue, et un systeme remontant le bon article du mauvais
        # livre serait compte faux sans avoir rien invente.
        tous = decouper("article")
        return [c for c in tous if _du_livre_ier(c.source_path)]

    if strategie == "markdown":
        # Le decoupage REELLEMENT en production : Chunker._chunk_markdown coupe sur
        # tout titre #{1,6}, donc un passage par article sur ce corpus — mais SANS le
        # prefixe hierarchique que la strategie « article » ajoute.
        #
        # Mesurer cet ecart, c'est savoir si la conclusion de D12 vaut pour ce qui
        # tourne, ou seulement pour ce qui a ete mesure. Un banc qui n'utilise pas le
        # code de production ne dit rien du code de production.
        chunker = Chunker(chunk_size=800, chunk_overlap=100)
        chunks = []
        for fichier in sorted(CORPUS.glob("*.md")):
            chunks.extend(chunker.chunk_document(
                content=fichier.read_text(encoding="utf-8"),
                source_path=fichier.name, doc_type="md"))
        return chunks

    if strategie != "article":
        raise SystemExit(f"stratégie inconnue : {strategie}")

    # Un chunk = un article, préfixé du titre du document et de sa position dans le
    # code. Le préfixe est essentiel : sans lui, « Les marchés sont passés en lots
    # séparés » perd le contexte qui permet de le retrouver depuis une question posée
    # en termes de procédure.
    chunks = []
    for fichier in sorted(CORPUS.glob("*.md")):
        contenu = fichier.read_text(encoding="utf-8")
        entete = contenu.split("---", 1)[0].strip()
        titre = entete.splitlines()[0].lstrip("# ").strip() if entete else fichier.stem
        position = ""
        for ligne in entete.splitlines():
            if ligne.startswith("> ") and "›" in ligne:
                position = ligne[2:].strip()
        corps = contenu.split("---", 1)[-1]
        for bloc in re.split(r"(?=^## Article )", corps, flags=re.M):
            m = re.match(r"## Article ([A-Za-z0-9\- ]+)", bloc)
            if not m:
                continue
            numero = m.group(1).strip()
            corps_article = re.sub(
                r"^## Article.*?$|^\*En vigueur.*?$", "", bloc, flags=re.M
            ).strip()
            texte = chr(10).join([titre, position, "", f"Article {numero}", "", corps_article])
            chunks.append(DocumentChunk(
                text=texte, source_path=fichier.name, source_name=fichier.name,
                section=f"Article {numero}", position=len(chunks), doc_type="md",
            ))
    return chunks


from colaig.rag.verification_citations import articles_cites as articles_du_chunk  # noqa: E402

# Cette fonction était réécrite ici, et son motif — « [LRD] suivi de quatre chiffres »,
# sans point ni espace tolérés — ne reconnaissait **aucune** référence de la forme
# « L. 2113-10 », soit 53,7 % de celles du corpus. Sa docstring annonçait pourtant
# retenir les numéros cités en corps de texte.
#
# Conséquence sur la mesure de recherche : un article attendu présent dans un passage
# sous sa seule forme pointée comptait comme non trouvé. Le score publié était donc
# **sous-estimé**, dans une proportion qu'il a fallu remesurer.
#
# Troisième copie de la même fonction dans ce chantier, troisième divergence. Toutes
# pointent désormais sur le module de production.



def main() -> int:
    cle = cle_albert()
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]
    print(f"jeu doré : {len(cas)} cas", file=sys.stderr)

    # ── Indexation ──────────────────────────────────────────────────────────
    chunks = decouper(STRATEGIE)
    print(f"stratégie : {STRATEGIE} — {len(chunks)} chunks", file=sys.stderr)

    t0 = time.monotonic()
    vecteurs = embed([c.text for c in chunks], cle)
    duree_embed = time.monotonic() - t0

    store = FaissStore(dimension=DIMENSION)
    t0 = time.monotonic()
    store.add(vecteurs, chunks)
    duree_index = time.monotonic() - t0

    # ── Palier 1 : récupération ─────────────────────────────────────────────
    questions = [c["question"] for c in cas]
    t0 = time.monotonic()
    vq = embed(questions, cle)
    duree_q = (time.monotonic() - t0) / len(questions)

    resultats = []
    latences = []
    for c, v in zip(cas, vq):
        t0 = time.monotonic()
        trouves = store.search(v, k=K)
        latences.append(time.monotonic() - t0)
        articles_vus: list[set[str]] = [articles_du_chunk(r.chunk.text) for r in trouves]
        union = set().union(*articles_vus) if articles_vus else set()

        attendus = set(c.get("articles_attendus", []))
        rang = None
        for i, vus in enumerate(articles_vus, 1):
            if attendus & vus:
                rang = i
                break
        resultats.append({
            "id": c["id"],
            "type": c["type"],
            "negatif": bool(c.get("attendu_refus")),
            "attendus": sorted(attendus),
            "couverts": sorted(attendus & union),
            "manquants": sorted(attendus - union),
            "rang_premier": rang,
            "sources": [r.chunk.source_path for r in trouves][:3],
            "score_max": round(max((r.score for r in trouves), default=0.0), 4),
        })

    return rapport(cas, resultats, chunks, latences, duree_embed, duree_index, duree_q)


def rapport(cas, resultats, chunks, latences, duree_embed, duree_index, duree_q) -> int:
    avec_attendus = [r for r in resultats if r["attendus"]]
    complet = [r for r in avec_attendus if not r["manquants"]]
    partiel = [r for r in avec_attendus if r["couverts"] and r["manquants"]]
    nul = [r for r in avec_attendus if not r["couverts"]]
    rangs = [r["rang_premier"] for r in avec_attendus if r["rang_premier"]]

    jour = time.strftime("%Y%m%d")
    suffixe = "" if STRATEGIE == "chunker" else f"-{STRATEGIE}"
    sortie = Path(__file__).resolve().parent.parent.parent / "docs" / f"baseline-{jour}{suffixe}.md"

    L = []
    L.append(f"# Rapport de référence — {time.strftime('%d/%m/%Y')}")
    L.append("")
    L.append("**Lot L1.5.** Référence contre laquelle toute modification du pipeline se juge.")
    L.append("Reproductible : corpus figé et vérifié par manifeste, jeu doré versionné,")
    L.append("`_chantier/scripts/reference_l15.py`.")
    L.append("")
    L.append("## Montage")
    L.append("")
    L.append("| | |")
    L.append("|---|---|")
    L.append(f"| Corpus | {len(list(CORPUS.glob('*.md')))} documents, Code de la commande publique, articles en vigueur |")
    L.append(f"| Découpage | `Chunker(800, 100)` — paramètres de `config.py` — **{len(chunks)} chunks** |")
    L.append(f"| Embeddings | `{MODELE_EMBED}`, {DIMENSION} dimensions (défaut D10) |")
    L.append(f"| Index | `FaissStore` / `IndexFlatIP`, recherche exacte |")
    L.append(f"| k | {K} passages, valeur de `max_results` de l'espace |")
    L.append(f"| Jeu doré | {len(cas)} cas, dont {sum(1 for r in resultats if r['negatif'])} négatifs |")
    L.append("")
    L.append("## Palier 1 — récupération (déterministe)")
    L.append("")
    L.append("Deux exécutions donnent le même résultat : ni juge, ni génération. **C'est le")
    L.append("socle.** Si le passage n'est pas remonté, aucune génération ne le rattrapera.")
    L.append("")
    L.append(f"Sur les **{len(avec_attendus)} cas ayant des articles attendus** :")
    L.append("")
    L.append("| | cas | part |")
    L.append("|---|---|---|")
    L.append(f"| Tous les articles attendus remontés | **{len(complet)}** | {len(complet)/len(avec_attendus)*100:.0f} % |")
    L.append(f"| Partiellement remontés | {len(partiel)} | {len(partiel)/len(avec_attendus)*100:.0f} % |")
    L.append(f"| Aucun remonté | {len(nul)} | {len(nul)/len(avec_attendus)*100:.0f} % |")
    L.append("")
    if rangs:
        L.append(f"Rang du premier article attendu : médiane **{statistics.median(rangs):.0f}**, "
                 f"moyenne {statistics.mean(rangs):.1f}, max {max(rangs)} (sur k={K}).")
        L.append("")
    L.append(f"Latence de recherche : médiane **{statistics.median(latences)*1000:.1f} ms** "
             f"(min {min(latences)*1000:.1f}, max {max(latences)*1000:.1f}).")
    L.append(f"Embedding d'une question : **{duree_q*1000:.0f} ms** en moyenne.")
    L.append("")
    L.append("### Détail par cas")
    L.append("")
    L.append("| cas | type | attendus | remontés | rang | score max |")
    L.append("|---|---|---|---|---|---|")
    for r in resultats:
        if not r["attendus"]:
            L.append(f"| {r['id']} | {r['type']} (négatif) | — | — | — | {r['score_max']} |")
            continue
        etat = "✅" if not r["manquants"] else ("⚠️" if r["couverts"] else "❌")
        L.append(
            f"| {r['id']} | {r['type']} | {', '.join(r['attendus'])} | "
            f"{etat} {', '.join(r['couverts']) or '—'} | "
            f"{r['rang_premier'] or '—'} | {r['score_max']} |"
        )
    L.append("")
    if nul:
        L.append("### Échecs de récupération — à examiner en priorité")
        L.append("")
        for r in nul:
            L.append(f"- **{r['id']}** : attendus {', '.join(r['attendus'])} — "
                     f"remontés depuis {', '.join(s[:34] for s in r['sources'])}")
        L.append("")
    L.append("## Palier 2 — génération (jugée, variable)")
    L.append("")
    L.append("**Non mesuré dans cette référence v1.** La génération dépend d'un modèle et")
    L.append("varie d'une exécution à l'autre ; l'inclure au socle reproduirait le")
    L.append("« ça a l'air mieux » que ce chantier combat. À ajouter comme palier distinct,")
    L.append("avec sa variance mesurée sur plusieurs exécutions — pas un chiffre unique.")
    L.append("")
    L.append("Ce qu'il devra mesurer, par ordre d'importance :")
    L.append("")
    L.append("1. **Le refus sur cas négatif.** Un seuil inventé produit une procédure")
    L.append("   irrégulière. C'est l'échec le plus coûteux, et il ne se voit pas.")
    L.append("2. **L'exactitude des citations** — l'article cité existe et dit ce qu'on lui fait dire.")
    L.append("3. La fidélité de la réponse aux passages remontés.")
    L.append("")
    L.append("## Coûts d'établissement")
    L.append("")
    L.append(f"- Embedding du corpus : **{duree_embed:.0f} s** pour {len(chunks)} chunks "
             f"({len(chunks)/max(duree_embed,1e-9):.0f} chunks/s).")
    L.append(f"- Construction de l'index : {duree_index*1000:.0f} ms.")
    L.append(f"- Empreinte de l'index : **{len(chunks)*DIMENSION*4/1e6:.1f} Mo** en float32.")
    L.append("")
    L.append("## Comment rejouer")
    L.append("")
    L.append("```bash")
    L.append("python _chantier/scripts/reference_l15.py")
    L.append("```")
    L.append("")
    L.append("Le corpus est vérifié par `tests/test_jeu_dore.py` contre son manifeste avant")
    L.append("toute comparaison : une référence établie sur un corpus dérivé ne vaut rien.")

    sortie.parent.mkdir(exist_ok=True)
    sortie.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"\nrapport écrit : {sortie}", file=sys.stderr)

    print(f"\nrécupération : {len(complet)}/{len(avec_attendus)} complets, "
          f"{len(partiel)} partiels, {len(nul)} nuls")
    if rangs:
        print(f"rang médian : {statistics.median(rangs):.0f} sur k={K}")
    print(f"types en échec : {Counter(r['type'] for r in nul)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
