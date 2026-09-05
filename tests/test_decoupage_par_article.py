"""
Colaig — un document structuré en articles se découpe par article, tout seul.

CE QUE LA MESURE A ÉTABLI, LE 01/09/2026
------------------------------------------
Le harnais de référence découpe **par article** ; la production découpe par titre
markdown. Mesuré à l'identique sur le corpus marchés publics :

    | | production `Chunker(800,100)` | référence `article` |
    | chunks                | 2388 | 1021 |
    | portant un article    |  35 % |  74 % |

**La référence ne mesurait donc pas ce qui tourne.** Toutes les campagnes de ces deux
jours décrivent une configuration que le produit n'a pas — même motif que la dérive
Helm du 30/08 : l'instrument et l'objet mesuré avaient divergé sans que personne ne
s'en aperçoive.

D12 (23/08) avait déjà tranché en faveur du découpage par article pour un corpus
structuré : **82 % de récupération complète contre 72 %, index 17 % plus petit**. La
décision était actée. Elle n'a jamais été construite.

DÉTECTER PLUTÔT QUE DÉCLARER, ET PAR DOCUMENT
------------------------------------------------
D12 en faisait un paramètre d'espace — donc une chose que l'utilisateur devait savoir
et renseigner. Il ne le fera pas : c'est lui demander ce qu'est un chunk.

Le signal se lit tout seul, et il est net :

    | | marchés publics | SST |
    | documents portant « ## Article » | 107/108 — **99 %** | 0/60 — **0 %** |
    | part des titres qui sont des articles | **0,89** | — |

Et la granularité est une propriété du **document**, pas de l'espace : un espace peut
contenir 50 PDF scannés et 10 fichiers structurés. Décider par document donne à chacun
le découpage que sa forme permet, et le cas mixte devient gratuit.

LE SEUIL EST UN CHOIX, PAS UN RÉSULTAT
----------------------------------------
0,89 contre 0,00 : toute valeur intermédiaire fonctionne, donc aucune n'est justifiée
par la mesure. Le seuil retenu est **haut** — la moitié des titres, et au moins deux
articles — parce qu'un faux positif casse le découpage d'un document qui n'a rien
demandé, tandis qu'un faux négatif rend seulement le comportement d'avant.

Le marqueur reconnu est **étroit** : `## Article `, la forme de ce corpus. Généraliser
sur un corpus unique serait du sur-mesure déguisé en règle.
"""

from __future__ import annotations

from colaig.rag.chunker import Chunker


def _chunker() -> Chunker:
    """La detection automatique, demandee explicitement.

    Elle n'est pas le defaut du produit — voir
    `test_le_defaut_reste_la_fenetre_glissante` juste dessous, qui dit pourquoi.
    """
    return Chunker(chunk_size=800, chunk_overlap=100, strategie="auto")


def test_le_defaut_reste_la_fenetre_glissante():
    """Le defaut ne doit pas changer l'indexation a l'insu de qui met a jour.

    « auto » fait passer de 94 a 98 % les passages portant une identite d'article :
    un gain reel, mais qui deplacerait l'index ET la reference de mesure. Tant que la
    campagne du pipeline n'est pas close, le defaut reste celui qui tourne.
    """
    assert Chunker(chunk_size=800, chunk_overlap=100)._strategie == "fenetre"


DOC_ARTICLES = """# CCAG Travaux — Chapitre 2

> **Position dans le Code** : CCAG Travaux › Chapitre 2

## Article R2151-1

Le pouvoir adjudicateur définit les modalités de la consultation.

## Article R2151-2

Les délais courent à compter de la publication.

## Article R2151-3

Le titulaire dispose d'un mois pour contester.
"""

DOC_ORDINAIRE = """# Guide d'accueil

## Provenance et licence

Ce document est publié sous licence ouverte.

## Documents

Liste des pièces jointes au dossier.

## Contacts

Le service se tient à disposition.
"""


def test_un_document_structure_se_decoupe_par_article():
    """LE cas visé : un chunk par article, et l'article dans la section."""
    chunks = _chunker().chunk_document(DOC_ARTICLES, "ccag.md", doc_type="md")

    sections = [c.section for c in chunks]
    assert len(chunks) == 3, f"{len(chunks)} chunks au lieu de 3 : {sections}"
    assert all("R2151-" in s for s in sections), sections


def test_chaque_chunk_porte_son_article():
    """Sans quoi le modèle n'a pas de prise et doit en fabriquer une."""
    chunks = _chunker().chunk_document(DOC_ARTICLES, "ccag.md", doc_type="md")

    for c in chunks:
        assert "R2151-" in c.text, f"l'article manque au texte servi : {c.text[:60]!r}"


def test_un_document_ordinaire_ne_bouge_pas():
    """LA borne. On n'applique la stratégie qu'à ce qui la mérite."""
    # Comparaison a perimetre egal : le comportement force « fenetre » EST le
    # comportement d'avant. Comparer a la sortie brute de `_chunk_markdown`
    # comparerait un decoupage post-traite a un decoupage qui ne l'est pas.
    avant = Chunker(chunk_size=800, chunk_overlap=100,
                    strategie="fenetre").chunk_document(
        DOC_ORDINAIRE, "guide.md", doc_type="md")

    chunks = _chunker().chunk_document(DOC_ORDINAIRE, "guide.md", doc_type="md")

    assert [c.text for c in chunks] == [c.text for c in avant], (
        "le decoupage d'un document sans article a change")


def test_un_article_isole_ne_suffit_pas():
    """Le seuil est haut : un faux positif casse un document qui n'a rien demandé."""
    doc = "# Guide\n\n## Contexte\n\ntexte\n\n## Article L1\n\ntexte\n\n## Suite\n\ntexte\n"
    chunks = _chunker().chunk_document(doc, "guide.md", doc_type="md")

    assert not all("Article" in (c.section or "") for c in chunks), (
        "un seul article sur trois titres a declenche la strategie")


def test_un_document_sans_markdown_n_est_pas_concerne():
    """Un PDF n'a pas de titres : la question ne se pose pas."""
    chunks = _chunker().chunk_document(
        "Article L2111-1 du code. " * 60, "scan.pdf", doc_type="pdf")

    assert chunks, "le decoupage par fenetre doit continuer de fonctionner"


def test_la_detection_se_dit():
    """Un choix automatique doit être observable, sinon il est silencieux.

    C'est le miroir du défaut traqué tout au long de ces deux jours : une capacité qui
    agit sans qu'on le sache. La décision reste automatique ; elle cesse d'être muette.
    """
    from colaig.rag.chunker import decoupage_par_article_pertinent

    assert decoupage_par_article_pertinent(DOC_ARTICLES) is True
    assert decoupage_par_article_pertinent(DOC_ORDINAIRE) is False


def test_un_espace_peut_forcer_la_strategie():
    """Dernier recours, quand la détection se trompe."""
    c = Chunker(chunk_size=800, chunk_overlap=100, strategie="fenetre")
    chunks = c.chunk_document(DOC_ARTICLES, "ccag.md", doc_type="md")

    assert not all("R2151-" in (x.section or "") for x in chunks), (
        "la strategie forcee n'est pas respectee")
