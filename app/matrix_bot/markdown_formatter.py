# SPDX-License-Identifier: MIT
"""
Utilitaires de formatage Markdown/HTML pour les messages Matrix.

Fonctions pures, sans dépendance au client Matrix, testables indépendamment.
"""

import re


def preprocess_markdown(text: str) -> str:
    """
    Prétraite le texte Markdown avant conversion HTML.

    - Met en gras les titres de la forme "Mot :" en début de paragraphe.
    - Convertit en puces les numéros isolés qui ne font pas partie d'une vraie liste.
    """
    lines = text.split('\n')
    processed_lines = []

    title_pattern = r'^([A-Za-z]+\s?[A-Za-z]*)\s?:'
    number_start_pattern = r'^(\d+)\.\s'

    for i, line in enumerate(lines):
        if re.match(title_pattern, line):
            if i == 0 or lines[i - 1].strip() == "":
                if not line.startswith('**') and not line.endswith('**'):
                    line = f"**{line}**"

        if re.match(number_start_pattern, line):
            if i > 0:
                prev_line = lines[i - 1].strip()
                if not prev_line.endswith(':') and prev_line != "":
                    line = re.sub(number_start_pattern, '- ', line)

        processed_lines.append(line)

    return '\n'.join(processed_lines)


def fix_html_lists(html_content: str) -> str:
    """
    Post-traitement HTML après conversion Markdown.

    Corrige les listes numérotées (<ol>) générées à tort par le parser Markdown
    dans des contextes où ce sont en réalité des titres ou des phrases continues.
    """
    # Cas 1 : <ol><li>Titre :</li></ol> → <p><strong>Titre :</strong></p>
    html_content = re.sub(
        r'<ol>\s*<li>([^<:]+):</li>',
        r'<p><strong>\1:</strong></p>',
        html_content,
    )

    # Cas 2 : liste à un seul élément phrase complète → paragraphe
    simplified = re.sub(
        r'<ol>\s*<li>([A-Z][^<.]+\.)</li>\s*</ol>',
        r'<p>\1</p>',
        html_content,
    )
    if simplified != html_content:
        html_content = simplified

    # Cas 3 : listes décrivant caractéristiques/types → puces
    bulleted = re.sub(
        r'<ol>\s*<li>([^<]*?(?:prix|classe|type|référenc|fonction|caractéristique)[^<]*?)</li>',
        r'<ul><li>\1</li>',
        html_content,
    )
    if bulleted != html_content:
        html_content = bulleted.replace('</ol>', '</ul>')

    # Cas 4 : <ol> qui suit un titre en gras avec ":" → <ul>
    html_content = re.sub(
        r'<p><strong>([^<:]+):</strong></p>\s*<ol>',
        r'<p><strong>\1:</strong></p><ul>',
        html_content,
    )
    if '<ul>' in html_content and '</ol>' in html_content:
        ul_open = html_content.count('<ul>')
        ul_close = html_content.count('</ul>')
        if ul_open > ul_close:
            to_replace = min(ul_open - ul_close, html_content.count('</ol>'))
            for _ in range(to_replace):
                html_content = html_content.replace('</ol>', '</ul>', 1)

    return html_content
