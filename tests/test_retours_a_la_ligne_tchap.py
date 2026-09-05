"""
Colaig — un retour à la ligne écrit est un retour à la ligne voulu.

CE QUI A ÉTÉ VU DANS TCHAP LE 30/08/2026
------------------------------------------
Première réponse rendue avec les sources numérotées. Les appels de note étaient
corrects — « apaisés¹ », « du service² » — mais le bloc de notes est arrivé ainsi :

    ¹ AccEvtGrave Support participants septembre 2024.pdf ² Fiche métier Procédure
    relative à la mise en oeuvre de l'intervention sociale...

**Sur une seule ligne.** `_markdown_to_html` ajoute les lignes de texte les unes
derrière les autres, séparées par un `\n` — que le HTML replie en espace.

CE N'EST PAS UN DÉFAUT DE MES NOTES, C'EST UN DÉFAUT GÉNÉRAL
--------------------------------------------------------------
Toute réponse contenant deux lignes de texte consécutives les voyait fusionner. Les
titres et les listes s'en tiraient — ils produisent leurs propres balises — ce qui
explique que personne ne l'ait vu : le modèle rédige surtout en listes.

POURQUOI LE CONVERTISSEUR AVAIT RAISON, ET POURQUOI ON CHANGE QUAND MÊME
-------------------------------------------------------------------------
En CommonMark, un simple retour à la ligne EST un espace ; il faut deux espaces
terminaux ou une ligne vide pour couper. Le convertisseur respectait donc la norme.

Mais Colaig écrit dans une **messagerie**, pas dans un document. Les clients de chat
— Element, dont Tchap dérive — rendent les retours à la ligne tels quels, parce qu'un
utilisateur qui appuie sur Entrée attend une nouvelle ligne. C'est l'option `breaks`
des convertisseurs Markdown, activée par défaut dans ce contexte.

On aligne donc le rendu sur l'attente du lecteur, pas sur la norme du document.
"""

from __future__ import annotations

from colaig.messaging.matrix import _markdown_to_html


def test_deux_lignes_de_texte_restent_deux_lignes():
    """LE défaut vu dans Tchap."""
    html = _markdown_to_html("\u00b9 premier document.pdf\n\u00b2 second document.pdf")

    assert "<br" in html, (
        f"les deux notes fusionneront sur une seule ligne : {html!r}"
    )


def test_le_bloc_de_notes_reel_se_rend_sur_plusieurs_lignes():
    """Le cas exact du 30/08, avec le corps qui le precede."""
    texte = ("Il convient de faire suivre le message\u00b2.\n\n"
             "\u00b9 AccEvtGrave Support participants  septembre  2024.pdf\n"
             "\u00b2 Fiche metier Procedure relative a la mise en oeuvre.pdf")

    html = _markdown_to_html(texte)

    avant, _, apres = html.partition("\u00b9 AccEvtGrave")
    assert "<br" in apres.split("\u00b2")[0] or "<br" in apres[:200], (
        f"les deux notes ne sont pas separees : {html!r}"
    )


def test_une_ligne_seule_ne_gagne_pas_de_saut():
    """On ne suffixe pas la derniere ligne."""
    html = _markdown_to_html("Une seule ligne.")

    assert "<br" not in html, f"saut de ligne parasite : {html!r}"


def test_les_listes_ne_sont_pas_touchees():
    """Elles produisaient deja leurs propres balises — le defaut ne les atteignait pas."""
    html = _markdown_to_html("- premier\n- second")

    assert html.count("<li>") == 2
    assert "<br" not in html, (
        f"un saut de ligne s'est glisse entre deux <li> : {html!r}"
    )


def test_un_titre_suivi_de_texte_reste_propre():
    html = _markdown_to_html("## Titre\nDu texte.")

    assert "<h2>" in html
    assert not html.startswith("<br"), f"saut avant le titre : {html!r}"


def test_un_bloc_de_code_garde_ses_retours():
    """Dans un bloc de code, les retours sont deja litteraux : pas de <br> a ajouter."""
    html = _markdown_to_html("```\nligne un\nligne deux\n```")

    assert "<pre><code>" in html
    assert "<br" not in html, f"<br> injecte dans du code : {html!r}"


def test_une_ligne_vide_separe_deux_paragraphes():
    """Vu dans Tchap le 30/08/2026, sur la reponse de `!index` :

        J'ai lu 60 documents, decoupes en 1272 passages. 18 documents sont des
        copies exactes de 12 autres — je ne les cite qu'une fois :

    Les deux paragraphes, separes par une ligne vide dans le texte source, arrivaient
    colles. Le correctif precedent ne traitait que deux lignes de texte CONSECUTIVES ;
    la ligne vide, elle, produisait une entree vide que le HTML repliait aussi.

    Ce test acceptait la fusion — il exigeait `count("<br") <= 1`. Il figeait donc la
    moitie du defaut qu'il etait cense corriger.
    """
    html = _markdown_to_html("Premier bloc.\n\nSecond bloc.")

    assert "Premier bloc." in html and "Second bloc." in html
    assert html.count("<br") >= 2, (
        f"la ligne vide ne separe pas les deux paragraphes : {html!r}")


def test_une_ligne_vide_en_tete_ne_pousse_rien():
    """LA borne : pas de blanc avant le premier mot."""
    html = _markdown_to_html("\n\nPremier mot.")

    assert not html.lstrip().startswith("<br"), f"blanc en tete : {html!r}"


def test_une_ligne_vide_apres_une_liste_ne_double_pas_le_blanc():
    """`</ul>` separe deja ; y ajouter des sauts creerait un trou."""
    html = _markdown_to_html("- un\n- deux\n\nUn paragraphe.")

    assert "</ul>" in html
    assert "<br /><br />" not in html.split("</ul>")[1], (
        f"blanc surnumeraire apres la liste : {html!r}")
