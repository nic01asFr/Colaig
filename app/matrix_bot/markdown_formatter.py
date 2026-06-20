# SPDX-License-Identifier: MIT
"""
Utilitaires de formatage Markdown/HTML pour les messages Matrix.

Fonctions pures, sans dépendance au client Matrix, testables indépendamment.

Principe : le LLM (via le system prompt) génère du Markdown structuré.
Le pipeline ne doit PAS transformer le contenu — juste convertir fidèlement
en HTML pour Tchap. Les anciennes transformations (conversion numéros → puces,
<ol> → <p>) cassaient les listes numérotées du LLM.
"""

import re


def preprocess_markdown(text: str) -> str:
    """Prétraite le texte Markdown avant conversion HTML.

    Nettoyage ciblé des artefacts de Mistral-Small sans
    transformer la structure intentionnelle du LLM.
    """
    # 1. Supprimer les lignes ne contenant qu'un numéro isolé ("3." ou "3. ")
    #    Mistral génère parfois des numéros orphelins entre les vrais items.
    text = re.sub(r'^\d+\.\s*$', '', text, flags=re.MULTILINE)

    # 2. Supprimer les numéros orphelins en fin de ligne précédant une ligne vide.
    #    Pattern : "...texte\n4.\n\n" → "...texte\n\n"
    text = re.sub(r'\n\d+\.\s*\n', '\n', text)

    # 3. Fixer l'indentation des sous-listes : 3 espaces → 4 espaces.
    #    Le LLM (Mistral) indente les sous-items de 3 espaces mais la lib
    #    Python `markdown` en exige 4 pour les reconnaître comme imbriqués.
    text = re.sub(r'^( {3})([-*])', r'    \2', text, flags=re.MULTILINE)

    # 4. Supprimer les lignes vides consécutives (>2 → 2 max)
    text = re.sub(r'\n{3,}', '\n\n', text)

    return text


def fix_html_lists(html_content: str) -> str:
    """Post-traitement HTML minimal après conversion Markdown.

    Ne transforme PAS les listes — le LLM les génère correctement.
    Corrige uniquement les artefacts de conversion.
    """
    # Supprimer les <li> vides (artefacts de numéros isolés non nettoyés)
    html_content = re.sub(r'<li>\s*</li>', '', html_content)

    # Supprimer les <ol>/<ul> vides après nettoyage des <li> vides
    html_content = re.sub(r'<ol>\s*</ol>', '', html_content)
    html_content = re.sub(r'<ul>\s*</ul>', '', html_content)

    return html_content
