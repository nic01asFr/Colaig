"""
Tests du formatage Markdown/HTML des messages Matrix.

Principe : le LLM génère du Markdown structuré ; le pipeline le convertit
fidèlement en HTML sans détruire la structure intentionnelle (régression
historique : l'ancien pipeline transformait les listes numérotées en puces).
"""
import markdown as md

from app.matrix_bot.markdown_formatter import preprocess_markdown, fix_html_lists


def _render(text: str) -> str:
    text = preprocess_markdown(text)
    html = md.markdown(text, extensions=["fenced_code"])
    return fix_html_lists(html)


def test_numbered_list_preserved():
    """Une vraie liste numérotée reste un <ol> (régression historique)."""
    html = _render("1. Premier\n2. Second\n3. Troisième")
    assert "<ol>" in html
    assert html.count("<li>") == 3
    assert "Premier" in html and "Troisième" in html


def test_orphan_number_removed():
    """Un numéro seul sur une ligne (artefact Mistral) est supprimé."""
    out = preprocess_markdown("1. Texte\n3.\n2. Autre")
    assert "\n3.\n" not in out
    assert "Texte" in out and "Autre" in out


def test_sublist_indent_normalized():
    """Une sous-liste indentée de 3 espaces est portée à 4 (exigence du parser)."""
    out = preprocess_markdown("- a\n   - b")
    assert "    - b" in out


def test_empty_li_removed():
    """Les <li> vides issus de numéros orphelins sont nettoyés."""
    assert fix_html_lists("<ol><li>x</li><li>  </li></ol>") == "<ol><li>x</li></ol>"


def test_empty_list_removed():
    """Une liste devenue vide après nettoyage est supprimée."""
    assert fix_html_lists("<ul><li>  </li></ul>") == ""


def test_collapse_blank_lines():
    """Plus de deux lignes vides consécutives sont réduites à une séparation."""
    assert "\n\n\n" not in preprocess_markdown("a\n\n\n\n\nb")


def test_plain_text_untouched():
    """Un texte simple sans structure n'est pas altéré."""
    assert preprocess_markdown("Bonjour, comment puis-je aider ?") == \
        "Bonjour, comment puis-je aider ?"
