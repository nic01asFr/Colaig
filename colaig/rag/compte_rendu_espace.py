"""
Colaig — dire ce qu'on voit dans un espace, plutot qu'exiger qu'il soit propre.

CE QUE `!index` REPONDAIT
--------------------------
    Etat de l'index :
    - `etags.json` — 7 Ko
    - `index.faiss` — 20223 Ko
    - `metadata.pkl` — 984 Ko

Trois fichiers internes et leur taille. L'utilisateur demande ce que Colaig a compris
de ses documents ; on lui rendait un listage de repertoire.

LA THESE
----------
Un espace reel n'est pas prepare. Le corpus SST — depose par quelqu'un qui travaille —
contient 60 documents dont 18 sont des copies exactes de 12 autres, deux arborescences
qui se recouvrent, et un fichier nomme « … - Copie.odt ».

On ne demande pas a l'utilisateur de nettoyer avant de poser une question. On lui DIT
ce qui a ete vu. Il decide ensuite — ou ne decide rien, et Colaig s'en arrange seul,
puisque la recherche ne sert plus deux fois le meme passage
(`retriever._deduplique_les_passages`).

Le compte rendu DECRIT, il n'exige pas : pas de rubrique vide, pas de reproche, et rien
qui ne soit exact.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

# Un salon n'est pas un rapport. Au-dela, on compte sans nommer.
_MAX_NOMMES = 4


def _empreinte(textes: list[str]) -> str:
    return hashlib.sha256("".join(textes).encode("utf-8")).hexdigest()[:16]


def _nom(chemin: str) -> str:
    return chemin.rsplit("/", 1)[-1]


def rediger_compte_rendu(chunks: list, fichiers_du_stockage: list[str]) -> str:
    """Decrit un espace tel que Colaig l'a lu.

    Args:
        chunks: les passages actifs de l'index (`DocumentChunk`).
        fichiers_du_stockage: les chemins des documents presents dans l'espace.
    """
    par_document: dict[str, list[str]] = defaultdict(list)
    for ch in chunks:
        par_document[getattr(ch, "source_path", "")].append(getattr(ch, "text", "") or "")

    if not par_document:
        return ("Aucun document indexé dans cet espace pour l'instant. "
                "Déposez des fichiers, je les lirai au prochain passage.")

    lignes = [f"J'ai lu **{len(par_document)} documents**, "
              f"découpés en **{sum(len(t) for t in par_document.values())} passages**."]

    # ── Les copies ────────────────────────────────────────────────────────────
    groupes: dict[str, list[str]] = defaultdict(list)
    for chemin, textes in par_document.items():
        groupes[_empreinte(textes)].append(chemin)
    doublons = {k: sorted(v) for k, v in groupes.items() if len(v) > 1}

    if doublons:
        copies = sum(len(v) - 1 for v in doublons.values())
        lignes.append("")
        if copies == 1:
            lignes.append("1 document est une copie exacte d'un autre "
                          "— je ne le cite qu'une fois :")
        else:
            lignes.append(f"{copies} documents sont des copies exactes de "
                          f"{len(doublons)} autres — je ne les cite qu'une fois :")
        for chemin_s in list(doublons.values())[:_MAX_NOMMES]:
            lignes.append(f"- « {_nom(chemin_s[0])} », en {len(chemin_s)} exemplaires")
        if len(doublons) > _MAX_NOMMES:
            lignes.append(f"- …et {len(doublons) - _MAX_NOMMES} autres")

    # ── Ce qui n'a pas pu etre lu ─────────────────────────────────────────────
    absents = [f for f in fichiers_du_stockage if f not in par_document]
    if absents:
        s = "s" if len(absents) > 1 else ""
        lignes.append("")
        lignes.append(f"{len(absents)} document{s} n'{'ont' if s else 'a'} "
                      f"pas pu être lu{s} :")
        for chemin in absents[:_MAX_NOMMES]:
            lignes.append(f"- « {_nom(chemin)} »")
        if len(absents) > _MAX_NOMMES:
            lignes.append(f"- …et {len(absents) - _MAX_NOMMES} autres")

    return "\n".join(lignes)
