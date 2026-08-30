"""
Colaig — deux noms qu'un lecteur ne distingue pas doivent se comparer égaux.

RELEVE EN PRODUCTION, LE 30/08/2026
------------------------------------
Sur une question reelle, dans le fil Tchap :

    citation_checker: 1 citation(s) sans source correspondante:
      ['AccEvtGrave Support participants septembre 2024.pdf']
    (sources fournies: ['AccEvtGrave Support participants  septembre  2024.pdf', ...])

Les deux chaines designent **le meme document**. La source porte des espaces
doubles ; le modele, qui redige, les a ecrits simples. Aucun lecteur ne voit la
difference — `_norm` la voyait, et la citation a ete comptee comme fantome.

CE QUE CELA COUTAIT
---------------------
`audit_and_adjust` retranche 30 % de confiance sur une citation non sourcee. La
reponse mesuree est tombee a **confiance=0.51** en citant correctement ses deux
documents. Un seuil d'affichage place au-dessus l'aurait fait taire.

LA PROPRIETE FIGEE ICI
------------------------
La comparaison porte sur **ce qu'un lecteur voit**, pas sur les octets. Deux
differences sont invisibles a l'oeil et doivent l'etre au comparateur :

1. **les suites d'espaces** — « a  b » et « a b » se lisent pareil ;
2. **la composition Unicode** — « evenement » en NFD (e + accent combinant) et en
   NFC (e accentue precompose) s'affichent a l'identique. Un depot alimente depuis
   macOS produit du NFD, un depot Windows du NFC : le meme corpus contient les deux.

CE QUI NE DOIT PAS ETRE NORMALISE
-----------------------------------
La casse l'est deja et c'est un choix assume. Mais ni la ponctuation, ni les
chiffres, ni les mots : `test_deux_documents_distincts_le_restent` borne le lot.
Normaliser plus large echangerait un faux positif contre un faux negatif — et le
faux negatif est le pire des deux, puisqu'il fait passer une citation inventee.
"""

from __future__ import annotations

import unicodedata

from colaig.security.citation_checker import audit_and_adjust, check_citations


# ─────────────────────────────────────────────────────────────────────────────
# Le cas releve en production
# ─────────────────────────────────────────────────────────────────────────────


SOURCE_REELLE = "AccEvtGrave Support participants  septembre  2024.pdf"
CITEE_REELLE = "AccEvtGrave Support participants septembre 2024.pdf"


def test_le_cas_du_30_08_2026():
    """La reponse citait juste ; le verificateur disait le contraire."""
    resultat = check_citations(
        f"Apres un evenement grave, un debriefing est organise [{CITEE_REELLE}].",
        [SOURCE_REELLE, "debriefing.pdf"],
    )

    assert resultat["ungrounded"] == [], (
        "la citation est comptee fantome alors qu'elle designe une source fournie : "
        f"{resultat['ungrounded']}"
    )
    assert resultat["all_grounded"] is True


def test_la_confiance_n_est_plus_amputee():
    """Ce que le defaut coutait vraiment : 30 % sur une reponse correcte."""
    confiance = audit_and_adjust(f"Voir [{CITEE_REELLE}].", [SOURCE_REELLE], 0.72)

    assert confiance == 0.72, (
        f"confiance degradee a {confiance} sur une citation pourtant sourcee"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Les deux differences invisibles
# ─────────────────────────────────────────────────────────────────────────────


def test_les_suites_d_espaces_se_lisent_pareil():
    for cite, source in [
        ("rapport annuel.pdf", "rapport  annuel.pdf"),
        ("rapport  annuel.pdf", "rapport annuel.pdf"),
        ("a b c.pdf", "a   b    c.pdf"),
        ("fiche\tmetier.pdf", "fiche metier.pdf"),
    ]:
        r = check_citations(f"Voir [{cite}].", [source])
        assert r["ungrounded"] == [], f"« {cite} » vs « {source} » : {r['ungrounded']}"


def test_les_deux_compositions_unicode_se_lisent_pareil():
    """Un corpus alimente depuis macOS et Windows contient les deux formes."""
    # Un accent REEL : sans lui, NFC et NFD sont la meme chaine et le test ne
    # prouve rien. C'est le piege que ce test a d'abord contenu.
    brut = "événement climatique.pdf"
    nfc = unicodedata.normalize("NFC", brut)
    nfd = unicodedata.normalize("NFD", brut)
    assert nfc != nfd, "sans difference de composition, le test est vide"

    r = check_citations(f"Voir [{nfc}].", [nfd])
    assert r["ungrounded"] == [], "NFC cite, NFD fourni : compte comme fantome"

    r = check_citations(f"Voir [{nfd}].", [nfc])
    assert r["ungrounded"] == [], "NFD cite, NFC fourni : compte comme fantome"


def test_un_nom_reel_du_corpus_avec_accents_et_espaces():
    """Le corpus deploye contient ce chemin, espaces doubles et accents compris."""
    source = ("/colaig-mesure-sst/fiche  signalement Dirmed/evenement climatique/"
              "Fiche metier Procedure relative a la mise en oeuvre.pdf")
    cite = "Fiche metier Procedure relative a la mise en oeuvre.pdf"

    r = check_citations(f"Voir [{cite}].", [source])
    assert r["ungrounded"] == []


# ─────────────────────────────────────────────────────────────────────────────
# La borne : ce que la normalisation ne doit PAS effacer
# ─────────────────────────────────────────────────────────────────────────────


def test_deux_documents_distincts_le_restent():
    """Le garde-fou du lot.

    Un faux negatif est pire qu'un faux positif : il laisse passer une citation
    inventee. Ces paires doivent rester distinctes.
    """
    for cite, source in [
        ("rapport 2024.pdf", "rapport 2025.pdf"),
        ("note-a.pdf", "note-b.pdf"),
        ("bilan.pdf", "bilan.docx"),
    ]:
        r = check_citations(f"Voir [{cite}].", [source])
        assert r["ungrounded"] == [cite], (
            f"« {cite} » confondu avec « {source} » : la normalisation va trop loin"
        )


def test_un_document_invente_reste_signale():
    """Le signal utile du verificateur, celui pour lequel il existe."""
    r = check_citations("Voir [rapport-inexistant-2024.pdf].", [SOURCE_REELLE])
    assert r["ungrounded"] == ["rapport-inexistant-2024.pdf"]
