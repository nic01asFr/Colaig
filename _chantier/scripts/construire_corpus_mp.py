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
**Uniquement les articles en `VIGUEUR`.** Les 773 articles `MODIFIE` et les 19 `ABROGE`
sont écartés : un assistant de rédaction qui cite un article abrogé est pire qu'un
assistant muet. C'est aussi ce qui rend le corpus **figé et rejouable** — condition d'un
jeu doré qui mesure quelque chose.
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
FICHIER_HF = f"data/{SNAPSHOT}/legi_code_de_la_commande_publique/legi_code_de_la_commande_publique_part_0.parquet"


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


def main():
    chemin = hf_hub_download(
        repo_id="AgentPublic/legi", repo_type="dataset",
        filename=FICHIER_HF, revision=REVISION,
    )
    con = duckdb.connect()
    lignes = con.execute(
        f"""
        SELECT doc_id, number, subtitles, full_title, text, start_date
        FROM {chemin!r}
        WHERE status = 'VIGUEUR' AND chunk_index = 1
        ORDER BY subtitles, number
        """
    ).fetchall()
    articles = [
        {"doc_id": d, "number": n, "subtitles": s, "full_title": ft, "text": t, "date": dt}
        for d, n, s, ft, t, dt in lignes
    ]
    print(f"articles en vigueur : {len(articles)}")

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
            "*Source : Code de la commande publique, version consolidée (LEGI, DILA). "
            "Licence Ouverte 2.0. Seuls les articles en vigueur figurent ici — "
            "aucun article modifié ni abrogé.*",
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
        "Seuls les articles au statut `VIGUEUR` sont retenus. Les articles `MODIFIE`, "
        "`ABROGE`, `PERIME` et `TRANSFERE` sont écartés : **un assistant qui cite un "
        "article abrogé est pire qu'un assistant muet.** C'est aussi ce qui rend ce "
        "corpus figé, donc rejouable — condition d'un jeu doré qui mesure quelque chose.\n\n"
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
