"""Construit le corpus de référence « rédaction de marchés publics ».

Source : `AgentPublic/legi` sur Hugging Face — législation française consolidée,
structurée, sous **Licence Ouverte 2.0**. Partition `legi_code_de_la_commande_publique`.

Choix de granularité
--------------------
Un document = une **unité de travail du rédacteur**, pas un article isolé ni un livre
entier. Le découpage suit la hiérarchie du code (Partie / Livre / Titre / Chapitre /
Section) et descend d'un niveau tant qu'un groupe dépasse `MAX_ARTICLES`.

Un article seul se cite mais ne s'explique pas : « le délai est de 35 jours » n'a de sens
qu'avec les exceptions du même chapitre. Un Titre entier de 159 articles, à l'inverse,
noie la réponse. Entre les deux, le chapitre est l'unité que consulte quelqu'un qui rédige.

Ce qui est retenu
-----------------
**Les articles en vigueur à une date de référence explicite.** Un assistant de rédaction
qui cite un article abrogé est pire qu'un assistant muet — mais un assistant à qui l'on
cache l'article applicable ne vaut pas mieux.

La première version filtrait sur `status = 'VIGUEUR'`. C'était faux, et silencieusement :

- `VIGUEUR_DIFF` désigne une version **entrée en vigueur à effet différé**. Au 23/08/2026,
  26 articles du code sont dans cet état — leur texte applicable était écarté.
- `ABROGE_DIFF` désigne une abrogation **à effet différé**. L'article reste applicable
  jusqu'à sa date d'effet ; 18 l'étaient encore et étaient pourtant écartés.

Le cas décisif est `R2152-7`, qui définit les **critères d'attribution** — la question la
plus centrale pour quelqu'un qui rédige. Il existe en deux versions : l'ancienne abrogée
au 21/08/2026, la nouvelle en vigueur depuis cette même date. Le filtre par statut
écartait les deux, et le corpus ne pouvait donc pas répondre sur les critères
d'attribution, alors que d'autres articles du corpus y renvoient explicitement.

La règle est désormais temporelle : `start_date <= DATE_REFERENCE < end_date`, en
excluant `MODIFIE_MORT_NE` — des modifications qui n'ont jamais pris effet. Résultat :
1806 articles au lieu de 1762, **44 ajoutés, aucun retiré**.

`DATE_REFERENCE` est épinglée, comme le sont l'instantané et la révision : un corpus dont
le périmètre dépend du jour où on l'exécute n'est pas une référence.

Recollage des fragments
-----------------------
La source découpe les articles longs en `chunk_index` successifs. La première version ne
gardait que `chunk_index = 1` : **53 articles étaient tronqués en pleine phrase**, et ce
sont les plus longs, donc les plus substantiels. Mesuré sur `L2511-7`, le fragment 1
s'arrêtait sur « au moins 80 % de son chiffre » et le fragment 2 reprenait sur
« d'affaires » — la coupe avait mangé une espace, sans recouvrement. Les fragments sont
donc recollés par une espace simple, dans l'ordre de `chunk_index`.
"""
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

from huggingface_hub import hf_hub_download
import duckdb

MAX_ARTICLES = 40
SORTIE = Path(r"C:\Users\Omen\AppData\Local\Temp\corpus-marches-publics")
# **Instantané daté et révision épinglés.** `legi-latest` bouge : un corpus de
# référence qui bouge n'est pas une référence, et un jeu doré écrit contre lui
# deviendrait faux sans prévenir. C'est exactement le mode de dérive que L1.5 doit
# empêcher.
SNAPSHOT = "legi-20260801"
REVISION = "67be48b3d4a8df343d7dc6597b88bb896d02236e"
# Date à laquelle l'applicabilité des articles est appréciée. Épinglée pour la même
# raison que l'instantané : un corpus dont le périmètre change selon le jour de son
# exécution ne peut pas servir de référence de mesure.
DATE_REFERENCE = "2026-08-23"

# Perimetre : le regime des marches publics ORDINAIRES — deuxieme partie, livre Ier.
#
# Le code entier compte 1806 articles applicables, dont 38 % seulement relevent de ce
# regime. Le reste est le livre defense-securite (23 %), les concessions, les marches
# de partenariat, l'outre-mer. Aucun des 117 articles attendus par le jeu dore n'en
# sort.
#
# MESURE QUI DECIDE (23/08/2026), generation sur 124 cas :
#
#                                    corpus entier   restreint
#   citations du mauvais regime        115 (22 %)      1 (0 %)
#   citations hors contexte                    22           55
#   garde-fou rendue / annotee / rempl. 137/23/4     103/50/9
#
# Le choix est entre 115 ERREURS SILENCIEUSES et 33 AVERTISSEMENTS VISIBLES de plus.
# Une citation du mauvais regime delivre du droit faux comme s'il etait juste — le
# livre defense pose 100 000 euros la ou l'ordinaire pose 60 000 — et AUCUN garde-fou
# ne peut la voir, puisque l'article etait bien dans les passages fournis. Une citation
# hors contexte, elle, est annotee sous les yeux de l'utilisateur.
#
# On prefere le mode de defaillance que le garde-fou sait voir.
#
# Mettre PERIMETRE a None reconstruit le code entier — l'ancien corpus reste donc
# reproductible, et il est dans l'historique git.
PERIMETRE = ("DEUXIÈME PARTIE", "Livre Ier")

# Le Titre Preliminaire est retenu QUOI QU'IL ARRIVE.
#
# Il porte L1 a L6 et L3-1, qui definissent « contrat de la commande publique »,
# « marche », « marche public », « acheteur », et enoncent les PRINCIPES de la
# commande publique — liberte d'acces, egalite de traitement, transparence. Ils ne
# relevent d'aucun livre, et un filtre par livre les faisait tomber.
#
# Le test d'ancrage l'a attrape : mp-046 cite L3, et L3 avait disparu. Perdre les
# articles qui definissent l'objet meme du corpus pour restreindre son perimetre
# serait une faute grossiere — ce sont ceux qu'on cite le plus.
HORS_PERIMETRE_RETENUS = ("Titre Préliminaire",)
FICHIER_HF = f"data/{SNAPSHOT}/legi_code_de_la_commande_publique/legi_code_de_la_commande_publique_part_0.parquet"

# SOURCES ANNEXES — ce qu'il faut de plus que le code pour repondre en expert.
#
# Le code dit qu'il existe des cahiers de clauses generales et des annexes ; il ne dit
# pas ce qu'ils contiennent. Or c'est la que vit l'essentiel de ce dont un redacteur a
# besoin. Mesure du 23/08/2026 : « clauses administratives particulieres », « reglement
# de consultation », « acte d'engagement » avaient ZERO occurrence dans les 1806
# articles du code, et cinq echecs de recherche etaient hors de portee de tout reglage
# parce que la question et le corpus n'avaient aucun mot en commun.
#
# LES SIX CCAG SONT RETENUS, ET C'EST UN REVIREMENT ASSUME. Ils forment des regimes
# paralleles — l'article 20 du CCAG Travaux n'est pas celui du CCAG PI — et D24 vient
# d'ecarter le livre defense-securite pour cette raison exacte. La difference tient a
# UNE SEULE CHOSE, mais elle est decisive : les jumeaux du livre defense sont
# INVISIBLES, « R2322-14 » ressemble a « R2122-8 » et seul un expert les distingue,
# tandis qu'un article de CCAG PORTE LE NOM DE SON CAHIER. « CCAG Travaux 20 » ne peut
# pas se confondre avec « CCAG PI 20 », et un modele qui cite le mauvais cahier se voit.
#
# On ecarte ce qu'on ne peut pas voir, on garde ce qu'on peut lire.
#
# Chaque source : (nom court, partition, nb de fichiers, motif de titre, annexe seule).
# « annexe seule » ecarte les articles de l'arrete lui-meme, qui portent les MEMES
# NUMEROS que le cahier annexe — sans quoi « article 4 » rend l'application a
# Saint-Barthelemy au lieu des Pieces contractuelles.
SOURCES_ANNEXES = [
    ("CCAG Travaux", "legi_arrete", 18,
     "%clauses administratives g%n%rales des march%s publics de travaux%", True),
    ("CCAG Maîtrise d'œuvre", "legi_arrete", 18,
     "%clauses administratives g%n%rales des march%s publics de ma%trise d%uvre%", True),
    ("CCAG Fournitures et services", "legi_arrete", 18,
     "%clauses administratives g%n%rales des march%s publics de fournitures courantes%", True),
    ("CCAG Prestations intellectuelles", "legi_arrete", 18,
     "%clauses administratives g%n%rales des march%s publics de prestations intellectuelles%", True),
    ("CCAG Techniques de l'information", "legi_arrete", 18,
     "%clauses administratives g%n%rales des march%s publics de techniques de l%information%", True),
    ("CCAG Marchés industriels", "legi_arrete", 18,
     "%clauses administratives g%n%rales des march%s publics industriels%", True),
    # Annexes du code : le corpus en portait la LISTE sans le contenu, ce qui en faisait
    # la premiere source de cas negatifs — le titre visible, la reponse absente.
    ("Annexe 2 — Seuils de procédure", "legi_avis", 1,
     "%seuils de proc%dure et % la liste des autorit%s publiques centrales%", False),
    ("Annexe 7 — Profils d'acheteurs", "legi_arrete", 18,
     "%fonctionnalit%s et exigences minimales des profils d%acheteurs%", False),
    ("Annexe 12 — Signature électronique", "legi_arrete", 18,
     "%signature %lectronique des contrats de la commande publique%", False),
    ("Annexe 13 — Modèles de garantie", "legi_arrete", 18,
     "%mod%les de garantie % premi%re demande et de caution personnelle%", False),
]


def fichiers(partition: str, nombre: int) -> str:
    """URLs des parquet d'une partition, pour lecture a distance.

    La partition des arretes pese 4,7 Go en 18 fichiers. DuckDB lit le parquet distant
    par plages et ne rapatrie que ce que le filtre retient : quelques secondes contre
    un telechargement de plusieurs gigaoctets.
    """
    base = (f"https://huggingface.co/datasets/AgentPublic/legi/resolve/{REVISION}"
            f"/data/{SNAPSHOT}/{partition}/")
    return ", ".join(f"'{base}{partition}_part_{i}.parquet'" for i in range(nombre))


def ardoise(texte: str, longueur: int = 60) -> str:
    t = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t[:longueur].strip("-") or "sans-titre"


def chemin_hierarchique(subtitles: str) -> list[str]:
    return [p.strip() for p in (subtitles or "").split(" - ") if p.strip()]


def grouper(articles):
    """Regroupe par le chemin le plus profond qui garde les groupes sous MAX_ARTICLES."""
    groupes = defaultdict(list)
    for art in articles:
        groupes[tuple(chemin_hierarchique(art["subtitles"])[:4])].append(art)

    stable = False
    profondeur = 4
    while not stable and profondeur < 8:
        stable = True
        profondeur += 1
        nouveaux = defaultdict(list)
        for cle, arts in groupes.items():
            if len(arts) <= MAX_ARTICLES:
                nouveaux[cle].extend(arts)
                continue
            stable = False
            for art in arts:
                nouveaux[tuple(chemin_hierarchique(art["subtitles"])[:profondeur])].append(art)
        groupes = nouveaux
    return groupes


def articles_annexes(con):
    """Les articles des sources annexes, lus a distance et etiquetes par leur source.

    Chaque numero est PREFIXE du nom court de sa source. Dans un corpus qui porte le
    code et six cahiers, « Article 20 » ne dit pas de quel texte il s'agit — et c'est
    exactement le genre de confusion qui produit du droit faux presente comme juste.

    La regle d'applicabilite est la meme que pour le code : `start_date <= DATE_REFERENCE
    < end_date`. Les avis et arretes existent en versions successives, toutes au statut
    VIGUEUR ; sans cette regle on en empilerait quatre generations.
    """
    con.execute("INSTALL httpfs; LOAD httpfs;")
    tous = []
    for nom, partition, nombre, motif, annexe_seule in SOURCES_ANNEXES:
        condition_annexe = "AND subtitles ILIKE 'Annexe%'" if annexe_seule else ""
        lignes = con.execute(
            f"""
            SELECT doc_id,
                   any_value(number)     AS number,
                   any_value(subtitles)  AS subtitles,
                   any_value(full_title) AS full_title,
                   string_agg(text, ' ' ORDER BY chunk_index) AS text,
                   any_value(start_date) AS start_date
            FROM read_parquet([{fichiers(partition, nombre)}])
            WHERE full_title ILIKE '{motif}'
              AND start_date <= '{DATE_REFERENCE}'
              AND end_date   >  '{DATE_REFERENCE}'
              AND status <> 'MODIFIE_MORT_NE'
              {condition_annexe}
            GROUP BY doc_id
            ORDER BY any_value(subtitles), any_value(number), any_value(start_date)
            """
        ).fetchall()

        # UNE SEULE VERSION PAR NUMERO : la plus recente applicable.
        #
        # La regle temporelle ne suffit pas pour ces textes. LEGI NE FERME PAS les
        # versions precedentes d'un avis ou d'un arrete : mesure sur l'avis relatif aux
        # seuils, CINQ versions portent `status = VIGUEUR` et `end_date = 2999-01-01`,
        # de 2018 a 2026. Toutes passent le filtre de date.
        #
        # Le corpus servait donc les seuils de 2018 — 144 000 euros — a cote des
        # actuels, sans que rien ne les distingue. Un assistant qui cite un seuil perime
        # comme s'il etait en vigueur produit exactement la procedure irreguliere que ce
        # corpus existe pour eviter.
        #
        # Les lignes etant triees par date croissante, la derniere ecrase les
        # precedentes.
        derniere: dict[str, tuple] = {}
        for ligne in lignes:
            # Les lignes SANS numero d'une meme source sont des versions du meme
            # texte, pas des articles distincts : l'avis sur les seuils en compte cinq,
            # de 2018 a 2026. Les distinguer par doc_id les conserverait toutes, ce qui
            # est exactement ce qu'on cherche a eviter.
            derniere[ligne[1] or "__texte_integral__"] = ligne
        avant = len(lignes)
        lignes = list(derniere.values())
        if avant != len(lignes):
            print(f"    {avant - len(lignes)} version(s) anterieure(s) ecartee(s)")
        # Les articles SANS numero recoivent un ordinal. L'avis sur les seuils en
        # compte cinq, tous sans numero : sous un meme en-tete « Annexe 2 — texte »
        # ils s'ecrasaient dans l'index, et quatre disparaissaient en silence.
        sans_numero = 0
        vues: dict[str, int] = {}
        articles = []
        for d, n, sub, ft, t, dt in lignes:
            if not n:
                sans_numero += 1
                etiquette = f"{nom} — texte {sans_numero}"
            else:
                etiquette = f"{nom} {n}"
            # Deux articles d'une meme source peuvent porter le meme numero — l'arrete
            # sur les modeles de garantie en a deux nommes « 2 ». Sans suffixe, le
            # second ecrase le premier dans l'index, et un article disparait sans que
            # rien ne le signale.
            vues[etiquette] = vues.get(etiquette, 0) + 1
            if vues[etiquette] > 1:
                etiquette = f"{etiquette} bis" if vues[etiquette] == 2 else f"{etiquette} ({vues[etiquette]})"
            articles.append(
            {"doc_id": d,
             "number": etiquette,
             # Le chemin hierarchique est refait sous le nom de la source : sans cela,
             # les chapitres des six cahiers se melangeraient entre eux et avec les
             # titres du code dans les memes documents.
             "subtitles": f"{nom} - " + (sub or "Texte").replace("Annexe - ", ""),
             "full_title": ft, "text": t, "date": dt})
        print(f"  {nom:36} {len(articles):3} articles")
        tous += articles
    print(f"sources annexes : {len(tous)} articles")
    return tous


def main():
    chemin = hf_hub_download(
        repo_id="AgentPublic/legi", repo_type="dataset",
        filename=FICHIER_HF, revision=REVISION,
    )
    con = duckdb.connect()
    lignes = con.execute(
        f"""
        SELECT doc_id,
               any_value(number)      AS number,
               any_value(subtitles)   AS subtitles,
               any_value(full_title)  AS full_title,
               string_agg(text, ' ' ORDER BY chunk_index) AS text,
               any_value(start_date)  AS start_date
        FROM {chemin!r}
        WHERE start_date <= '{DATE_REFERENCE}'
          AND end_date   >  '{DATE_REFERENCE}'
          AND status <> 'MODIFIE_MORT_NE'
        GROUP BY doc_id
        ORDER BY any_value(subtitles), any_value(number)
        """
    ).fetchall()
    articles = [
        {"doc_id": d, "number": n, "subtitles": s, "full_title": ft, "text": t, "date": dt}
        for d, n, s, ft, t, dt in lignes
    ]
    if PERIMETRE:
        avant = len(articles)
        articles = [
            a for a in articles
            if all(cle in (a["subtitles"] or "") for cle in PERIMETRE)
            or any(cle in (a["subtitles"] or "") for cle in HORS_PERIMETRE_RETENUS)
        ]
        print(f"périmètre {' / '.join(PERIMETRE)} : {len(articles)} articles "
              f"retenus sur {avant}")
    print(f"articles en vigueur : {len(articles)}")

    articles += articles_annexes(con)

    groupes = grouper(articles)
    print(f"documents produits  : {len(groupes)}")
    tailles = sorted(len(a) for a in groupes.values())
    print(f"articles par document : min {tailles[0]}, médiane {tailles[len(tailles)//2]}, max {tailles[-1]}")

    if SORTIE.exists():
        for f in SORTIE.rglob("*"):
            if f.is_file():
                f.unlink()
    SORTIE.mkdir(parents=True, exist_ok=True)

    total_octets = 0
    index_lignes = []
    for rang, (cle, arts) in enumerate(sorted(groupes.items()), 1):
        arts.sort(key=lambda a: (a["number"] or ""))
        titre = cle[-1] if cle else "Dispositions diverses"
        # La mention de source suit le document : annoncer « Code de la commande
        # publique » en tete d'un CCAG serait faux, et c'est le genre d'etiquette
        # qu'un lecteur croit sans verifier.
        source_annexe = next((n for n, *_ in SOURCES_ANNEXES if cle and cle[0] == n), "")
        # Deux premiers niveaux dans le nom : « partie-reglementaire » puis la matière.
        prefixe = ardoise(" ".join(cle[1:3]), 40) if len(cle) > 2 else ardoise(cle[0] if cle else "", 40)
        nom = f"{rang:03d}-{prefixe}-{ardoise(titre, 50)}.md"

        entete = [
            f"# {titre}",
            "",
            f"> **Position dans le Code de la commande publique**  ",
            f"> {' › '.join(cle)}",
            "",
            f"**{len(arts)} articles en vigueur** — "
            f"{arts[0]['number']} à {arts[-1]['number']}." if len(arts) > 1
            else f"**Article {arts[0]['number']}**, en vigueur.",
            "",
            # La mention de source suit le document : annoncer « Code de la commande
            # publique » en tete d'un CCAG serait faux, et c'est precisement le genre
            # d'etiquette erronee qu'un lecteur croit sans verifier.
            (f"*Source : {source_annexe} (LEGI, DILA). Licence Ouverte 2.0. "
             f"Texte applicable au {DATE_REFERENCE}.*"
             if source_annexe else
             "*Source : Code de la commande publique, version consolidée (LEGI, DILA). "
             "Licence Ouverte 2.0. Seuls les articles en vigueur figurent ici — "
             "aucun article modifié ni abrogé.*"),
            "",
            "---",
            "",
        ]
        corps = []
        for a in arts:
            corps.append(f"## Article {a['number']}")
            corps.append("")
            corps.append(f"*En vigueur depuis le {a['date']}.*")
            corps.append("")
            corps.append((a["text"] or "").strip())
            corps.append("")
        contenu = "\n".join(entete + corps)
        (SORTIE / nom).write_text(contenu, encoding="utf-8")
        total_octets += len(contenu.encode("utf-8"))
        index_lignes.append(f"| `{nom}` | {len(arts)} | {' › '.join(cle[1:])} |")

    (SORTIE / "000-SOMMAIRE.md").write_text(
        "# Corpus — Code de la commande publique\n\n"
        "Corpus de référence pour l'assistance à la rédaction de marchés publics.\n\n"
        f"**{len(articles)} articles en vigueur**, répartis en {len(groupes)} documents "
        "suivant la structure du code.\n\n"
        "## Provenance et licence\n\n"
        "Code de la commande publique, version consolidée, extrait du jeu de données "
        "`AgentPublic/legi` (Hugging Face), lui-même dérivé de la base **LEGI** publiée "
        "par la DILA sur data.gouv.fr.\n\n"
        "**Licence Ouverte 2.0 (Etalab)** — réutilisation libre, y compris commerciale, "
        "sous réserve de mentionner la source.\n\n"
        "## Périmètre\n\n"
        f"Sont retenus les articles **applicables au {DATE_REFERENCE}** : "
        f"`start_date <= {DATE_REFERENCE} < end_date`, à l'exclusion des modifications "
        "n'ayant jamais pris effet (`MODIFIE_MORT_NE`). Les articles abrogés ou "
        "remplacés sont donc écartés — **un assistant qui cite un article abrogé est "
        "pire qu'un assistant muet** — mais les versions à **effet différé** déjà "
        "entrées en vigueur sont retenues, ce qu'un filtre sur le seul statut `VIGUEUR` "
        "manquait.\n\n"
        "Ce filtre par statut écartait notamment `R2152-7`, qui définit les **critères "
        "d'attribution** : sa version applicable porte le statut `VIGUEUR_DIFF`. Le "
        "corpus ne pouvait donc pas répondre sur la question la plus centrale de la "
        "rédaction, alors que d'autres articles y renvoient explicitement.\n\n"
        "La date de référence est épinglée au même titre que l'instantané : un corpus "
        "dont le périmètre dépend du jour de son exécution n'est pas une référence.\n\n"
        "## Documents\n\n"
        "| fichier | articles | position dans le code |\n|---|---|---|\n"
        + "\n".join(index_lignes) + "\n",
        encoding="utf-8",
    )

    # Manifeste : empreinte de chaque document. Une régénération qui ne donne pas le
    # même manifeste signale que la source a bougé — donc que le jeu doré doit être
    # revérifié avant d'être cru.
    import hashlib

    lignes_manifeste = []
    for f in sorted(SORTIE.glob("*.md")):
        octets = f.read_bytes()
        lignes_manifeste.append(f"{hashlib.sha256(octets).hexdigest()[:16]}  {len(octets):7d}  {f.name}")
    manifeste = chr(10).join([
        f"# Manifeste du corpus — {len(lignes_manifeste)} documents",
        f"# source   : AgentPublic/legi, {SNAPSHOT}",
        f"# revision : {REVISION}",
        f"# articles : {len(articles)} en vigueur",
        "#",
        "# Une régénération qui ne reproduit pas ces empreintes signale que la source",
        "# a bougé : le jeu doré doit alors être revérifié avant d'être cru.",
        "",
    ] + lignes_manifeste) + chr(10)
    (SORTIE / "MANIFESTE.txt").write_text(manifeste, encoding="utf-8")

    print(f"octets écrits : {total_octets/1e6:.2f} Mo dans {SORTIE}")
    print(f"manifeste : {len(lignes_manifeste)} empreintes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
