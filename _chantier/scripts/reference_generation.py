"""
Palier 2 de la référence — la génération.

STATUT: COMPLET
VERSION: 2026-08-23 - v1.0
LOT: L1.5

La génération varie d'une exécution à l'autre. **Son évaluation, elle, ne doit pas.**
Tout ce qui est mesuré ici est vérifiable mécaniquement sur la réponse produite :

| indicateur | ce qu'il détecte |
|---|---|
| **citation fantôme** | un numéro d'article cité qui **n'existe pas** dans le corpus |
| **citation hors contexte** | un article qui existe mais n'était **pas** dans les passages fournis — le modèle a puisé dans sa mémoire, pas dans le corpus |
| **montant inventé** | une somme en euros absente des passages fournis |
| **refus** | sur cas négatif, la réponse dit-elle que l'information manque ? |
| **citation attendue** | l'article attendu est-il cité ? |

Les trois premiers sont les seuls qui comptent vraiment. Sur un corpus juridique, un
article inventé ou un seuil fabriqué produit une procédure irrégulière, et **rien dans
la réponse ne le signale** : elle est fluide, plausible, et fausse.

Variance
--------
Les cas négatifs — les plus coûteux — sont rejoués **trois fois**. Un refus obtenu une
fois ne prouve pas un comportement ; c'est la constance qui compte. Le reste est mesuré
une fois, et c'est écrit.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(RACINE))

# Capturer l'argument AVANT d'écraser sys.argv pour le module importé.
#
# Sans cette ligne, `VARIANTE` lisait le « article » posé plus bas pour la stratégie de
# découpage, et la variante demandée n'était jamais appliquée. Une exécution entière
# a ainsi produit un réplicat du témoin sous le nom de la variante — le rapport était
# faux sans qu'aucune erreur ne le signale.
_VARIANTE_DEMANDEE = sys.argv[1] if len(sys.argv) > 1 else "temoin"

SRC = (RACINE / "_chantier" / "scripts" / "reference_l15.py").read_text(encoding="utf-8")
_ns: dict = {"__name__": "gen", "__file__": str(RACINE / "_chantier" / "scripts" / "reference_l15.py")}
sys.argv = ["gen", "article"]
exec(compile(SRC.replace("raise SystemExit(main())", "pass"), "reference_l15.py", "exec"), _ns)
decouper, embed, cle_albert = _ns["decouper"], _ns["embed"], _ns["cle_albert"]
articles_du_chunk, FaissStore, CORPUS = _ns["articles_du_chunk"], _ns["FaissStore"], _ns["CORPUS"]

JEU = RACINE / "tests" / "golden" / "v1.jsonl"
CONFIG = RACINE / "tests" / "golden" / "corpus-marches-publics-config.yaml"

# Cible de production (D3). Mesurer sur autre chose mesurerait autre chose.
BASE_SSP = "https://llm.lab.sspcloud.fr/api/v1"
MODELE = "qwen3-6-35b-moe"
# Profondeur de recherche. Réglable par `COLAIG_REF_K` pour arbitrer un compromis qui
# ne se tranche pas au seul niveau de la recherche : le banc des leviers donne 88/103
# cas complets à k=6 et **95/103 à k=15**, mais chaque passage supplémentaire entre
# dans le prompt de génération. Ce qu'on gagne en rappel, on le paie en contexte, en
# latence, et peut-être en refus — plus de passages, c'est plus d'occasions de trouver
# quelque chose de plausible à dire sur une question sans réponse.
K = int(os.environ.get("COLAIG_REF_K", "6"))

# Raisonnement du modele, desactivable par COLAIG_REF_RAISONNEMENT=0.
#
# Sonde du 23/08/2026 sur cinq cas reellement tronques :
#
#   temoin 4000        2/5 coupees   1281 car.   20,3 s
#   max_tokens 8000    0/5 coupees   2339 car.   20,8 s
#   sans raisonnement  0/5 coupees   1202 car.    2,2 s
#   reasoning_effort   3/5 coupees   1196 car.   20,8 s
#
# `reasoning_effort: low` est SILENCIEUSEMENT IGNORE par l'endpoint : 16 373
# caracteres de raisonnement malgre lui. Un reglage accepte sans effet est pire
# qu'un reglage refuse — on croit l'avoir applique.
#
# `enable_thinking: false` supprime la troncature ET divise la latence par neuf.
# Reste a savoir si la reponse vaut autant : c'est ce que le jeu dore mesure.
RAISONNEMENT = os.environ.get("COLAIG_REF_RAISONNEMENT", "1") != "0"

# Perimetre du corpus. « article » = tout le code ; « article-livre1 » = la deuxieme
# partie, livre Ier, soit le regime des marches publics ORDINAIRES.
#
# Mesure du 23/08/2026 : 108 citations sur 469 — 23 % — portent sur un article hors de
# ce regime, presque toutes du livre defense-securite, dont les articles sont des
# jumeaux textuels aux seuils differents. Aucun garde-fou ne peut l'attraper : ces
# articles etaient dans les passages, donc la provenance est correcte. C'est une
# reponse FIDELE QUI CITE LE MAUVAIS DROIT.
#
# Ce drapeau sert a mesurer si restreindre le perimetre supprime le defaut, avant de
# decider de refiger le corpus — ce qui invaliderait la reference une fois de plus.
PERIMETRE = os.environ.get("COLAIG_REF_PERIMETRE", "article")
REPETITIONS_NEGATIFS = 3

# Variante de consigne, choisie par argument.
#
# `temoin`  : le prompt système de l'espace, tel quel.
# `durci`   : le même, plus un protocole de refus explicite et l'interdiction de citer
#             un article absent des passages.
#
# La piste du filtrage par score a été **écartée avant d'être testée** : sur la
# référence, cinq des huit cas négatifs scorent au-dessus du plus faible cas positif
# (médianes 0,623 contre 0,681). Un seuil écarterait de vrais résultats sans écarter
# les pièges. Mesurer ce qu'on sait déjà faux coûte une heure pour rien.
VARIANTE = _VARIANTE_DEMANDEE

DURCISSEMENT = """

---

PROTOCOLE DE RÉPONSE — à appliquer avant d'écrire.

1. Vérifie d'abord si la réponse figure **dans les passages ci-dessus**.

2. Si elle n'y figure pas, commence ta réponse par exactement :
   « Cette information ne figure pas dans les passages fournis. »
   Puis indique où la chercher. **N'avance alors aucun numéro d'article.**

3. Si elle y figure, réponds en citant les numéros d'article — et **uniquement des
   numéros qui apparaissent littéralement dans les passages ci-dessus**.

**Ne cite jamais un article que tu connais mais qui n'est pas dans les passages**, même
si tu es certain de son contenu. Une référence exacte mais non fournie est une erreur :
elle donne à l'utilisateur une confiance qu'il ne peut pas vérifier, et elle sera fausse
le jour où le texte changera sans que le corpus soit relu.

{nb} passages te sont toujours transmis, qu'ils répondent ou non. Leur présence ne prouve
pas que la réponse s'y trouve."""

# Le nombre annoncé au modèle doit suivre la profondeur réelle : lui dire « six » alors
# qu'il en reçoit quinze le renseigne faussement sur ce qu'il a sous les yeux.
DURCISSEMENT = DURCISSEMENT.format(nb=K)

MARQUEURS_REFUS = (
    "ne figure pas", "ne figurent pas", "ne contient pas", "ne permet pas",
    "pas dans ce corpus", "pas dans le corpus", "n'y sont pas", "ne se déduit",
    "je ne dispose pas", "n'est pas dans", "ne relève pas", "aucun élément",
    "hors du corpus", "n'apparaît pas",
)


def cle_ssp() -> str:
    for ligne in open(RACINE / ".env", encoding="utf-8"):
        if ligne.strip().lower().startswith("sspcloud_api_key="):
            return ligne.split("=", 1)[1].strip()
    raise SystemExit("clé SSPCloud introuvable")


def prompt_systeme() -> str:
    texte = CONFIG.read_text(encoding="utf-8")
    bloc = texte.split("system_prompt: |", 1)[1]
    lignes = []
    for ligne in bloc.splitlines()[1:]:
        if ligne.strip() and not ligne.startswith("  "):
            break
        lignes.append(ligne[2:] if ligne.startswith("  ") else ligne)
    return "\n".join(lignes).strip()


def repondre(systeme: str, question: str, passages: list[str], cle: str) -> tuple[str, float]:
    contexte = "\n\n---\n\n".join(passages)
    charge = json.dumps({
        "model": MODELE,
        "messages": [
            {"role": "system", "content": systeme},
            {"role": "user", "content":
                f"Passages du Code de la commande publique :\n\n{contexte}\n\n"
                f"Question : {question}"},
        ],
        "temperature": 0.1,
        # 4000, pas 2048. `qwen3-6-35b-moe` est un modèle à raisonnement : mesuré,
        # il produit 10 170 caractères de raisonnement pour 2 959 de réponse — un
        # facteur 3,4. À 900 la réponse est **vide** ; à 2048, le défaut du Protocol,
        # elle est tronquée. Voir docs/baseline-generation.
        "max_tokens": 4000,
        **({} if RAISONNEMENT else {"chat_template_kwargs": {"enable_thinking": False}}),
    }).encode()
    req = urllib.request.Request(BASE_SSP + "/chat/completions", data=charge, method="POST")
    req.add_header("Authorization", "Bearer " + cle)
    req.add_header("Content-Type", "application/json")
    t0 = time.monotonic()
    with urllib.request.urlopen(req, timeout=300) as rep:
        choix = json.loads(rep.read().decode())["choices"][0]
    contenu = choix["message"].get("content") or ""
    tronquee = choix.get("finish_reason") == "length"
    if tronquee:
        print("  ATTENTION réponse tronquée (finish_reason=length)", file=sys.stderr)
    return contenu, time.monotonic() - t0, tronquee


# La reconnaissance des références vient du **module de production**, pas d'une copie.
#
# Elle était dupliquée ici. Les deux exemplaires ont divergé une première fois — le côté
# réponse tolérait « L. 2113-10 », le côté passages non — et 53,7 % des références du
# corpus étant écrites avec un point, la mesure a conclu à tort que le modèle puisait
# dans sa mémoire. La conclusion était fausse ; la duplication l'avait rendue possible.
#
# Elle a divergé une seconde fois le 23/08/2026, quand le motif du module a été élargi
# aux articles préliminaires (L1 à L6). D'où cet import : une mesure qui n'utilise pas le
# code mesuré ne mesure pas ce qu'elle croit.
from colaig.rag.verification_citations import articles_cites  # noqa: E402



def montants(texte: str) -> set[str]:
    return {m.replace(" ", " ").replace("\xa0", " ")
            for m in re.findall(r"\b\d{1,3}(?:[   \xa0]\d{3})+\b", texte)}


def main() -> int:
    cle_a, cle_s = cle_albert(), cle_ssp()
    systeme = prompt_systeme()
    cas = [json.loads(l) for l in JEU.read_text(encoding="utf-8").splitlines() if l.strip()]

    chunks = decouper(PERIMETRE)
    articles_existants: set[str] = set()
    for f in CORPUS.glob("*.md"):
        articles_existants |= set(re.findall(r"^## Article ([A-Za-z0-9\- ]+)$",
                                             f.read_text(encoding="utf-8"), re.M))
    articles_existants = {a.strip() for a in articles_existants}
    print(f"{len(chunks)} chunks, {len(articles_existants)} articles, {len(cas)} cas", file=sys.stderr)

    store = FaissStore(dimension=1024)
    store.add(embed([c.text for c in chunks], cle_a), chunks)
    vq = embed([c["question"] for c in cas], cle_a)

    resultats = []
    latences = []
    for i, (c, v) in enumerate(zip(cas, vq), 1):
        trouves = store.search(v, k=K)
        passages = [r.chunk.text for r in trouves]
        fournis: set[str] = set()
        for p in passages:
            fournis |= articles_cites(p)
        montants_fournis = set().union(*(montants(p) for p in passages)) if passages else set()

        essais = REPETITIONS_NEGATIFS if c.get("attendu_refus") else 1
        observations = []
        for _ in range(essais):
            try:
                reponse, duree, tronquee = repondre(systeme, c["question"], passages, cle_s)
            except Exception as e:  # noqa: BLE001
                print(f"  {c['id']} : appel en échec ({type(e).__name__})", file=sys.stderr)
                continue
            latences.append(duree)
            cites = articles_cites(reponse)
            observations.append({
                "reponse": reponse,
                # Une réponse coupée ne peut pas être jugée sur son refus : mp-044 a
                # été tronquée à « « Cette » — neuf caractères — c'est-à-dire au
                # milieu de la formule de refus elle-même. La compter comme un
                # non-refus fabrique un échec qui n'existe pas.
                "tronquee": tronquee,
                # Une référence **présente dans les passages fournis** n'est jamais un
                # fantôme, quel que soit le code dont elle relève.
                #
                # Mesuré le 23/08/2026 : la métrique signalait `L5132-4` et `L5213-13`
                # comme inventés. Ce sont des articles du **code du travail**, cités mot
                # pour mot à l'intérieur de `L2113-13` et `L2113-12`, qui étaient dans
                # les passages. Le modèle relayait correctement un renvoi ; la mesure
                # l'accusait d'inventer. Sur trois fantômes annoncés, **un seul** était
                # réel — `L2161-1`, là où le code écrit `R2161-1`, un L à la place d'un R.
                #
                # Le corpus est un seul code ; les articles qu'il cite ne le sont pas.
                # Confondre « absent de ce corpus » et « inexistant » fabrique des
                # anomalies, et une métrique qui crie au loup finit ignorée.
                "fantomes": sorted(cites - articles_existants - fournis),
                "hors_contexte": sorted((cites & articles_existants) - fournis),
                "montants_inventes": sorted(montants(reponse) - montants_fournis
                                            - montants(c["question"])),
                "refus": any(m in reponse.lower() for m in MARQUEURS_REFUS),
                "cite_attendus": bool(set(c.get("articles_attendus", [])) & cites),
            })
        print(f"  {i}/{len(cas)} {c['id']}", end="\r", file=sys.stderr)
        resultats.append({"cas": c, "obs": observations})
    print(file=sys.stderr)
    return rapport(resultats, latences)


def rapport(resultats, latences) -> int:
    import statistics

    positifs = [r for r in resultats if not r["cas"].get("attendu_refus") and r["obs"]]
    negatifs = [r for r in resultats if r["cas"].get("attendu_refus") and r["obs"]]

    def compte(sous_ensemble, cle_obs):
        return sum(1 for r in sous_ensemble if any(o[cle_obs] for o in r["obs"]))

    def obs_jugeables(r):
        """Observations exploitables pour le refus : les tronquées ne le sont pas."""
        return [o for o in r["obs"] if not o.get("tronquee")]

    fantomes = compte(resultats, "fantomes")
    hors_ctx = compte(resultats, "hors_contexte")
    inventes = compte(resultats, "montants_inventes")
    cite_ok = compte(positifs, "cite_attendus")
    negatifs_jugeables = [r for r in negatifs if obs_jugeables(r)]
    tronques = sum(1 for r in negatifs for o in r["obs"] if o.get("tronquee"))
    refus_tjs = sum(1 for r in negatifs_jugeables if all(o["refus"] for o in obs_jugeables(r)))
    refus_parf = sum(1 for r in negatifs_jugeables if any(o["refus"] for o in obs_jugeables(r))
                     and not all(o["refus"] for o in obs_jugeables(r)))
    refus_jam = sum(1 for r in negatifs_jugeables if not any(o["refus"] for o in obs_jugeables(r)))

    L = [
        "# Palier génération — première mesure",
        "",
        f"**{time.strftime('%d/%m/%Y')}.** Complète `baseline-{time.strftime('%Y%m%d')}.md`.",
        "",
        f"Variante de consigne : **{VARIANTE}**. Profondeur de recherche : **k={K}**.",
        "",
        f"Montage : découpage par article (D12), `bge-m3` 1024 dim, k={K}, génération par",
        f"**`{MODELE}` sur SSPCloud** — la cible de production (D3). Prompt système : celui",
        "de l'espace, mot pour mot.",
        "",
        "La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie",
        "sur le texte produit, sans juge.",
        "",
        "## Ce qui compte le plus : le modèle invente-t-il ?",
        "",
        "| | cas concernés |",
        "|---|---|",
        f"| **Citation fantôme** — article cité inexistant dans le corpus | **{fantomes}/{len(resultats)}** |",
        f"| **Citation hors contexte** — article réel, absent des passages fournis | **{hors_ctx}/{len(resultats)}** |",
        f"| **Montant inventé** — somme absente des passages | **{inventes}/{len(resultats)}** |",
        "",
        "Une citation fantôme est le pire résultat possible : elle est indétectable pour qui",
        "ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.",
        "",
        f"## Le refus sur cas négatif — {len(negatifs)} cas, {REPETITIONS_NEGATIFS} exécutions chacun",
        "",
        "| | cas |",
        "|---|---|",
        f"| Refuse **à chaque fois** | **{refus_tjs}/{len(negatifs_jugeables)}** |",
        f"| Refuse *parfois* | {refus_parf}/{len(negatifs_jugeables)} |",
        f"| Ne refuse **jamais** | **{refus_jam}/{len(negatifs_jugeables)}** |",
        f"| *Observations écartées car tronquées* | *{tronques}* |",
        "",
        "Le refus intermittent est presque aussi problématique que l'absence de refus : on ne",
        "peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.",
        "",
        "## Citation de l'article attendu",
        "",
        f"**{cite_ok}/{len(positifs)}** cas positifs citent au moins un article attendu.",
        "",
        f"Latence de génération : médiane **{statistics.median(latences):.1f} s** "
        f"(min {min(latences):.1f}, max {max(latences):.1f}) sur {len(latences)} appels.",
        "",
        "## Détail des anomalies",
        "",
    ]
    anomalies = False
    for r in resultats:
        c = r["cas"]
        for j, o in enumerate(r["obs"], 1):
            if o["fantomes"] or o["hors_contexte"] or o["montants_inventes"]:
                anomalies = True
                suffixe = f" (exécution {j})" if len(r["obs"]) > 1 else ""
                L.append(f"- **{c['id']}**{suffixe} — "
                         + " · ".join(filter(None, [
                             f"fantômes : {', '.join(o['fantomes'])}" if o["fantomes"] else "",
                             f"hors contexte : {', '.join(o['hors_contexte'])}" if o["hors_contexte"] else "",
                             f"montants : {', '.join(o['montants_inventes'])}" if o["montants_inventes"] else "",
                         ])))
    if not anomalies:
        L.append("*Aucune.*")
    L += ["", "## Rejouer", "", "```bash",
          "python _chantier/scripts/reference_generation.py", "```"]

    # Le nom du rapport porte la profondeur, sinon deux executions de la meme
    # variante a des k differents s ecrasent en silence — ce qui est arrive le
    # 23/08/2026 : la passe k=15 a efface celle de k=6, rapport et reponses.
    suffixe = (("" if VARIANTE == "temoin" else f"-{VARIANTE}") + f"-k{K}"
               + ("" if RAISONNEMENT else "-sansraisonnement")
               + ("" if PERIMETRE == "article" else "-livre1"))
    sortie = RACINE / "docs" / f"baseline-generation-{time.strftime('%Y%m%d')}{suffixe}.md"
    # Les réponses sont conservées : auditer un chiffre ne doit pas exiger de tout
    # relancer. C'est ce qui a manqué pour vérifier la liste de marqueurs de refus.
    brut = RACINE / "_chantier" / "mesures" / f"reponses-{VARIANTE}-k{K}{'' if RAISONNEMENT else '-sansraisonnement'}{'' if PERIMETRE == 'article' else '-livre1'}-{time.strftime('%Y%m%d')}.json"
    brut.parent.mkdir(exist_ok=True)
    import json as _json
    brut.write_text(_json.dumps(
        [{"id": r["cas"]["id"], "negatif": bool(r["cas"].get("attendu_refus")),
          "question": r["cas"]["question"],
          "reponses": [o.get("reponse", "") for o in r["obs"]]} for r in resultats],
        ensure_ascii=False, indent=1), encoding="utf-8")
    sortie.write_text("\n".join(L) + "\n", encoding="utf-8")

    print(f"\nfantômes {fantomes} · hors contexte {hors_ctx} · montants inventés {inventes}")
    print(f"refus toujours {refus_tjs}/{len(negatifs_jugeables)} · parfois {refus_parf} "
          f"· jamais {refus_jam} · {tronques} obs. tronquées écartées")
    print(f"cite l'attendu : {cite_ok}/{len(positifs)}")
    print(f"latence médiane : {statistics.median(latences):.1f} s")
    print(f"\nrapport : {sortie}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
