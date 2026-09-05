"""
Un article identifié ne se coupe pas — c'est l'unité qu'on cherche à retrouver.

CE QUI A ÉTÉ MESURÉ SUR LE SERVICE, le 04/09/2026
--------------------------------------------------
    fenêtre, sans prompt    refus  5/22   cite l'attendu 53/113
    fenêtre, avec prompt    refus 20/22   cite l'attendu 52/113
    auto,    avec prompt    refus 19/22   cite l'attendu 63/113
    article pur (harnais)   refus 22/22   cite l'attendu 97/113

Le découpage par article a fait gagner 11 points — mais 34 séparent encore le
service du harnais. La différence tient à une seule chose : **176 des 1021 articles
du corpus (17 %) dépassent `MAX_CHUNK_SIZE` et sont redécoupés en morceaux**, là où
le harnais garde un article entier par chunk.

Quand un article est coupé en trois, la recherche remonte un fragment plutôt que
l'article, et le fragment retenu n'est pas forcément celui qui porte la réponse.
Cela explique le gain partiel : 83 % des articles passent entiers et gagnent les 11
points, les 17 % restants continuent de perdre.

POURQUOI LE PLAFOND RESTE AILLEURS
-----------------------------------
`MAX_CHUNK_SIZE` existe pour éviter des passages ingérables, et il garde tout son
sens sur du texte libre — un chapitre de rapport n'a pas de raison d'être servi
d'un bloc. Mais un article de loi est une unité de sens ET de citation : coupé, il
perd la propriété même qu'on exploite.

Le plus long du corpus, `CCAG Travaux 19`, fait 5143 caractères. À `k=5`, cinq
articles de cette taille font ~25 000 caractères — largement dans la fenêtre du
modèle.
"""
from __future__ import annotations

from colaig.rag.chunker import MAX_CHUNK_SIZE, Chunker

LONG = "Texte de l'article, répété pour dépasser le plafond. " * 60   # ~3100 car.
DOC_ARTICLES = f"""# CCAG Travaux

## Article CCAG Travaux 19

{LONG}

## Article CCAG Travaux 20

Un article court, qui tient sans peine sous le plafond.
"""

DOC_LIBRE = f"""# Rapport annuel

## Contexte

{LONG}

## Suites

Un paragraphe court.
"""


def _chunks(contenu: str, strategie: str):
    return Chunker(chunk_size=800, chunk_overlap=100,
                   strategie=strategie).chunk_document(
        content=contenu, source_path="doc.md", doc_type="md")


def test_un_article_plus_long_que_le_plafond_reste_entier():
    """Le cas des 17 % : sans cela, l'article part en morceaux et se perd."""
    chunks = _chunks(DOC_ARTICLES, "article")
    art19 = [c for c in chunks if c.section == "Article CCAG Travaux 19"]
    assert len(art19) == 1, (
        f"l'article a ete decoupe en {len(art19)} morceaux — la recherche remontera "
        f"un fragment plutot que l'article")
    assert len(art19[0].text) > MAX_CHUNK_SIZE


def test_les_articles_courts_ne_sont_pas_fusionnes():
    """Un article court reste un chunk : fusionner deux articles brouille la citation."""
    chunks = _chunks(DOC_ARTICLES, "article")
    sections = [c.section for c in chunks]
    assert sections.count("Article CCAG Travaux 20") == 1


def test_un_texte_sans_article_reste_decoupe():
    """L'autre moitie : le plafond garde tout son sens hors des articles.

    Sans cette borne, un chapitre de rapport de cinquante pages partirait d'un bloc
    dans le prompt.
    """
    chunks = _chunks(DOC_LIBRE, "fenetre")
    assert all(len(c.text) <= MAX_CHUNK_SIZE for c in chunks)
    assert len(chunks) > 1
