"""
Colaig — dire ce qu'on voit dans l'espace, plutôt qu'exiger qu'il soit propre.

CE QUE `!index` RÉPONDAIT
--------------------------
    État de l'index :
    - `etags.json` — 7 Ko
    - `index.faiss` — 20223 Ko
    - `metadata.pkl` — 984 Ko

Trois fichiers internes et leur taille. L'utilisateur demande ce que Colaig a compris
de ses documents ; on lui rend un listage de répertoire.

LA THÈSE
----------
Un espace réel n'est pas préparé. Le corpus SST — déposé par quelqu'un qui travaille —
contient 60 documents dont **18 sont des copies exactes** de 12 autres, deux
arborescences qui se recouvrent, et un fichier nommé `… - Copie.odt`.

On ne demande pas à l'utilisateur de nettoyer avant de poser une question. On lui dit
ce qui a été vu : *combien de documents, combien de copies, ce qui n'a pas pu être lu.*
Il décide ensuite — ou ne décide rien, et Colaig s'en arrange tout seul, puisque la
recherche ne sert plus deux fois le même passage.

LA PROPRIÉTÉ FIGÉE ICI
------------------------
Le compte rendu **décrit**, il n'exige pas. Il ne reproche rien, ne bloque rien, et il
est exact : un espace sans copie ne doit pas se voir signaler des copies, et un espace
dont tout est lisible ne doit pas porter une rubrique vide.
"""

from __future__ import annotations

import pytest

from colaig.models import DocumentChunk
from colaig.rag.compte_rendu_espace import rediger_compte_rendu


def _c(texte: str, chemin: str) -> DocumentChunk:
    return DocumentChunk(text=texte, source_path=chemin,
                         source_name=chemin.rsplit("/", 1)[-1])


def test_un_espace_ordinaire_se_decrit_en_deux_nombres():
    texte = rediger_compte_rendu(
        chunks=[_c("a", "/e/1.pdf"), _c("b", "/e/1.pdf"), _c("c", "/e/2.pdf")],
        fichiers_du_stockage=["/e/1.pdf", "/e/2.pdf"])

    assert "2 documents" in texte
    assert "3 passages" in texte


def test_les_copies_sont_nommees_et_comptees():
    """LE cas du corpus SST : 18 copies de 12 documents."""
    texte = rediger_compte_rendu(
        chunks=[_c("meme contenu", "/e/support/a.pdf"),
                _c("meme contenu", "/e/copie/a.pdf"),
                _c("autre", "/e/b.pdf")],
        fichiers_du_stockage=["/e/support/a.pdf", "/e/copie/a.pdf", "/e/b.pdf"])

    assert "1 document est une copie" in texte, texte
    assert "a.pdf" in texte
    assert "je ne le cite qu'une fois" in texte, (
        "le compte rendu doit dire ce que Colaig FAIT de la copie, pas seulement "
        "qu'elle existe"
    )


def test_un_espace_sans_copie_n_en_invente_pas():
    """LA borne. Une rubrique vide se lit comme un reproche sans objet."""
    texte = rediger_compte_rendu(
        chunks=[_c("a", "/e/1.pdf"), _c("b", "/e/2.pdf")],
        fichiers_du_stockage=["/e/1.pdf", "/e/2.pdf"])

    assert "copie" not in texte.lower(), texte


def test_ce_qui_n_a_pas_pu_etre_lu_est_dit():
    """Un fichier présent mais absent de l'index : l'utilisateur doit le savoir."""
    texte = rediger_compte_rendu(
        chunks=[_c("a", "/e/1.pdf")],
        fichiers_du_stockage=["/e/1.pdf", "/e/scan-illisible.pdf"])

    assert "scan-illisible.pdf" in texte
    assert "1 document" in texte and "pas pu être lu" in texte


def test_un_espace_entierement_lu_ne_porte_pas_la_rubrique():
    texte = rediger_compte_rendu(
        chunks=[_c("a", "/e/1.pdf")], fichiers_du_stockage=["/e/1.pdf"])

    assert "pas pu être lu" not in texte


def test_un_espace_vide_le_dit_simplement():
    texte = rediger_compte_rendu(chunks=[], fichiers_du_stockage=[])

    assert "aucun document" in texte.lower()


def test_le_compte_rendu_ne_deborde_pas():
    """Un salon n'est pas un rapport : on nomme quelques cas, pas soixante."""
    chunks, fichiers = [], []
    for i in range(40):
        chunks += [_c("identique", f"/e/dossier{i}/copie.pdf")]
        fichiers.append(f"/e/dossier{i}/copie.pdf")

    texte = rediger_compte_rendu(chunks=chunks, fichiers_du_stockage=fichiers)

    assert len(texte.splitlines()) < 15, f"compte rendu trop long :\n{texte}"
    assert "39 documents sont des copies" in texte
