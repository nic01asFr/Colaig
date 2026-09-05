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
import os
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

# Le corpus de mesure. Pilotable par `COLAIG_REF_CORPUS` depuis le 28/08/2026, pour
# opposer DEUX CONDITIONS et non pour en remplacer une :
#
#   défaut   corpus restreint au régime ordinaire (2e partie, livre Ier) — 1021 sources.
#            C'est la condition de la PORTE DE NON-RÉGRESSION, celle dont les seuils
#            sont fondés sur dix-sept observations.
#
#   complet  le code entier — 2128 sources. C'est la condition qui ressemble à la
#            PRODUCTION : là, Colaig n'a pas le droit de restreindre son corpus, il
#            indexe ce que contient le dossier partagé.
#
# La restriction du périmètre était une bonne décision de mesure (D33 : 115 citations
# du mauvais régime contre 1). Elle est aussi le point où la référence s'écarte le plus
# du produit, et cet écart n'était mesuré nulle part.
CORPUS = Path(os.environ.get(
    "COLAIG_REF_CORPUS", str(RACINE / "tests" / "golden" / "corpus-marches-publics")))
JEU = RACINE / "tests" / "golden" / "v1.jsonl"

# LA PILE DE RECHERCHE MESUREE, REGLABLE PAR L ENVIRONNEMENT.
#
# Ces trois valeurs etaient codees en dur, et la reference ne pouvait donc mesurer
# QUE Albert. Or `CLAUDE.md` §3 pose que la cible de production est SSPCloud, dont
# le catalogue — releve le 30/08/2026 — ne contient AUCUN bge-m3 : son unique
# modele d embedding, `qwen3-embedding-8b`, rend 4096 dimensions.
#
# La configuration mesuree ne pouvait donc pas exister sur la cible. Le rapport le
# declarait honnetement en tete, mais une reference qui ne decrit pas le systeme
# deployable ne peut pas servir de porte (P2).
#
# LES DEFAUTS SONT INCHANGES : aucune mesure anterieure n est invalidee, et le nom
# du fichier de cache porte le modele — un changement ne peut pas etre servi depuis
# des vecteurs d une autre dimension.
MODELE_EMBED = os.environ.get("COLAIG_REF_EMBED_MODELE", "BAAI/bge-m3")
DIMENSION = int(os.environ.get("COLAIG_REF_EMBED_DIM", "1024"))
BASE_EMBED = os.environ.get("COLAIG_REF_EMBED_BASE",
                            "https://albert.api.etalab.gouv.fr/v1")
# Ancien nom, conserve pour les lecteurs du journal.
BASE_ALBERT = BASE_EMBED
# Profondeur de recherche, alignee sur la generation (D33).
#
# Elle valait 6 en dur, alors que la generation est passee a 10 : mesurer la
# recherche a une profondeur que la production n emploie pas ne dit rien de la
# production. k n est pas une constante, c est une fonction de la taille du corpus.
K = int(os.environ.get("COLAIG_REF_K", "10"))

# Stratégie de découpage, choisie par argument. `article` respecte la frontière
# d'article ; `chunker` est le découpage en vigueur (800/100), qui sert de témoin.
# La référence du 23/08 a diagnostiqué deux échecs sur trois comme des défauts de
# granularité : le bon document remontait, pas le bon passage. C'est une hypothèse,
# et elle se mesure contre le témoin — elle ne se décrète pas.
STRATEGIE = sys.argv[1] if len(sys.argv) > 1 else "chunker"


def _cle(nom: str, *fichiers) -> str:
    """La clé vient de l'environnement, ou à défaut d'un `.env` local.

    L'ordre compte. Les harnais lisaient **uniquement** un `.env` du poste — ce qui les
    rendait inexécutables ailleurs, et notamment en intégration continue, où la porte de
    régression aurait été inerte sans que rien ne le signale.

    L'environnement d'abord : c'est ainsi qu'un secret est fourni par une chaîne
    d'intégration. Le fichier ensuite, pour le confort du poste de développement.
    """
    depuis_env = os.environ.get(nom)
    if depuis_env:
        return depuis_env.strip()
    for fichier in fichiers:
        try:
            for ligne in open(fichier, encoding="utf-8"):
                if ligne.strip().lower().startswith(nom.lower() + "="):
                    valeur = ligne.split("=", 1)[1].strip()
                    if valeur:
                        return valeur
        except OSError:
            continue
    raise SystemExit(
        f"{nom} introuvable : ni dans l'environnement, ni dans un .env local. "
        f"En intégration continue, l'ajouter aux secrets du dépôt."
    )


def cle_albert() -> str:
    return _cle("ALBERT_API_KEY",
                RACINE.parent / "colaig-v3" / ".env", RACINE / ".env")


# Cache d'embeddings
# -------------------
# POURQUOI. Chaque tirage de la reference recalculait 1156 embeddings — 1021 articles
# du corpus et 135 questions — alors que ni le corpus ni les questions ne changent d'un
# tirage a l'autre. Seule la GENERATION est stochastique. Sur la campagne de dispersion
# du 28/08 (huit tirages), une vingtaine de minutes sur 72 y sont passees pour rien.
#
# CE QUE LE CACHE CHANGE A LA MESURE, ET C'EST A SAVOIR. Un embedding n'est PAS
# deterministe : 2,6e-04 d'ecart absolu mesure entre deux appels du meme texte (voir
# `canari_modeles.py`, dont la calibration a defait cette hypothese). Le cache retire
# donc cette variance de la mesure.
#
# C'est souhaitable ici : on veut isoler la variance de GENERATION, et le bruit
# d'embedding ne deplace pas le classement — `recherche_complets` donnait deja 0,929
# contre 0,929 entre deux replicats. Mais c'est un CHOIX, pas un effet de bord :
# COLAIG_REF_CACHE=0 le desactive pour remesurer la jambe de recherche a neuf.
#
# LA CLE PORTE LE NOM DU MODELE : un changement de modele ne peut pas etre servi depuis
# le cache. Le canari le detecterait de toute facon en amont.
CACHE_ACTIF = os.environ.get("COLAIG_REF_CACHE", "1").lower() not in ("0", "false", "no")
_CACHE_FICHIER = (RACINE / "_chantier" / "mesures"
                  / f".cache-embeddings-{MODELE_EMBED.replace('/', '_')}.npz")
_CACHE: dict | None = None


def _cle_cache(texte: str) -> str:
    import hashlib

    return hashlib.sha256((MODELE_EMBED + "\x00" + texte).encode("utf-8")).hexdigest()


def _cache_charger() -> dict:
    global _CACHE
    if _CACHE is None:
        _CACHE = {}
        if CACHE_ACTIF and _CACHE_FICHIER.exists():
            try:
                import numpy as _np

                d = _np.load(_CACHE_FICHIER)
                for k, v in zip(d["cles"], d["vecteurs"]):
                    _CACHE[str(k)] = v.tolist()
            except Exception as e:  # un cache illisible ne doit jamais bloquer
                print(f"  cache d'embeddings illisible, ignore ({e})", file=sys.stderr)
                _CACHE = {}
    return _CACHE


def _cache_ecrire() -> None:
    if not CACHE_ACTIF or not _CACHE:
        return
    import numpy as _np

    _CACHE_FICHIER.parent.mkdir(parents=True, exist_ok=True)
    # Un fichier OUVERT, pas un chemin : `savez_compressed` ajoute lui-meme « .npz »
    # a un chemin, et l'ecriture provisoire atterrissait alors a cote de sa cible.
    provisoire = _CACHE_FICHIER.with_suffix(".tmp")
    with open(provisoire, "wb") as f:
        _np.savez_compressed(f,
                             cles=_np.array(list(_CACHE.keys())),
                             vecteurs=_np.array(list(_CACHE.values()),
                                                dtype=_np.float32))
    # Remplacement atomique : une campagne interrompue ne laisse pas un cache tronque.
    provisoire.replace(_CACHE_FICHIER)


def embed(textes: list[str], cle: str, lot: int = 32) -> list[list[float]]:
    if not CACHE_ACTIF:
        return _embed_distant(textes, cle, lot)
    cache = _cache_charger()
    # Dedoublonnage : le corpus contient des passages identiques, et les demander deux
    # fois coute deux fois.
    manquants = list(dict.fromkeys(t for t in textes if _cle_cache(t) not in cache))
    connus = len(textes) - sum(1 for t in textes if _cle_cache(t) not in cache)
    print(f"  cache : {connus}/{len(textes)} connus, {len(manquants)} a calculer",
          file=sys.stderr)
    if manquants:
        for v, t in zip(_embed_distant(manquants, cle, lot), manquants):
            cache[_cle_cache(t)] = v
        _cache_ecrire()
    return [cache[_cle_cache(t)] for t in textes]


def _embed_distant(textes: list[str], cle: str, lot: int = 32) -> list[list[float]]:
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
    # code.
    #
    # CE COMMENTAIRE AFFIRMAIT QUE LE PRÉFIXE EST « ESSENTIEL ». C'était faux, et
    # mesuré comme tel le 23/08/2026 : en isolant la variable, **89 cas complets avec
    # le préfixe, 90 sans** (D28).
    #
    # L'erreur venait d'une comparaison mal lue. Le rapprochement « 85 contre 88 » fait
    # le même jour opposait deux STRATÉGIES DE DÉCOUPAGE — `markdown` contre `article` —
    # qui diffèrent par bien plus que le préfixe. On avait attribué au préfixe un écart
    # produit par autre chose.
    #
    # Le préfixe est conservé : il ne nuit pas, il ne coûte rien, et il rend les
    # passages lisibles pour qui les inspecte. Mais il n'est pas un levier de rappel,
    # et présenter un choix non mesuré comme une nécessité est exactement ce que ce
    # chantier cherche à ne plus faire.
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
            # Tout identifiant, accents compris : « ([A-Za-z0-9- ]+) » tronquait
            # « CCAG Travaux Préambule » en « CCAG Travaux Pr », et le passage
            # entrait dans l'index sous un nom qui n'existe nulle part. Le
            # cas doré le cherchait alors en vain, alors qu'il remontait au
            # rang 1. Quatrième copie de ce motif dans le chantier.
            m = re.match(r"## Article (.+)", bloc)
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


from colaig.rag.verification_citations import articles_cites  # noqa: E402

_TITRE_ARTICLE = re.compile(r"^Article (.+)$", re.M)


def articles_du_chunk(texte: str) -> set[str]:
    """Articles d'un passage : ceux de son EN-TÊTE, et ceux cités dans son corps.

    L'en-tête est indispensable, et son omission a faussé une mesure entière. Le
    reconnaisseur de `verification_citations` ne lit que les numéros du Code —
    « L2113-10 », « R. 2122-8 ». Il ne voit donc **aucun** article du CCAG, dont les
    en-têtes portent « Article CCAG Travaux 4 ».

    Mesuré : les six cas dorés ajoutés sur le CCAG étaient tous comptés en échec, avec
    des scores de 0,62 à 0,72 et le bon passage effectivement remonté. L'instrument
    déclarait absent ce qu'il ne savait pas nommer.

    Les deux sources sont réunies : un passage porte l'article dont il est le texte, et
    ceux auxquels ce texte renvoie.
    """
    trouves = articles_cites(texte)
    for m in _TITRE_ARTICLE.finditer(texte or ""):
        trouves.add(m.group(1).strip())
    return trouves

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
