"""
Templates de prompts pour l'extraction de contenu web.
"""

from typing import Dict


class PromptTemplates:
    """Collection de templates de prompts pour différents types de contenu."""

    # Template pour extraction générale
    GENERAL = """Visite l'URL {url} et extrait le contenu principal du site.
Ignore les menus, publicités, en-têtes, pieds de page et éléments de navigation.
Concentre-toi uniquement sur le contenu principal et pertinent.
Retourne le contenu sous forme de texte brut sans formatage.
Inclus le titre principal dans ta réponse.

Format de réponse attendu:
TITRE: [titre de la page]
CONTENU: [contenu principal extrait]
"""

    # Template pour articles
    ARTICLE = """Visite l'URL {url} et analyse-la comme un article.
Extrait les informations suivantes:

1. Le titre de l'article
2. L'auteur (si disponible)
3. La date de publication (si disponible)
4. Le contenu principal de l'article (corps du texte)
5. Un bref résumé

Ignore les éléments suivants:
- Menus de navigation
- Publicités
- Commentaires
- Articles recommandés
- En-têtes et pieds de page

Format de réponse attendu:
TITRE: [titre de l'article]
AUTEUR: [auteur ou "Non disponible"]
DATE: [date ou "Non disponible"]
RÉSUMÉ: [résumé en 2-3 phrases]
CONTENU: [texte complet de l'article]
"""

    # Template pour produits e-commerce
    PRODUCT = """Visite l'URL {url} et analyse-la comme une page de produit.
Extrait les informations suivantes:

1. Nom du produit
2. Prix (si disponible)
3. Description du produit
4. Caractéristiques principales
5. Disponibilité (si mentionnée)

Format de réponse attendu:
TITRE: [nom du produit]
PRIX: [prix ou "Non disponible"]
DESCRIPTION: [description du produit]
CARACTÉRISTIQUES: [liste des caractéristiques principales]
DISPONIBILITÉ: [en stock / rupture / Non disponible]
"""

    # Template pour dépôts de code
    REPOSITORY = """Visite l'URL {url} et analyse-la comme un dépôt de code.
Extrait les informations suivantes:

1. Nom du projet
2. Description du projet
3. Langage(s) de programmation utilisé(s)
4. Nombre d'étoiles / forks (si disponible)
5. Contenu du README principal
6. Technologies et frameworks utilisés

Format de réponse attendu:
TITRE: [nom du projet]
DESCRIPTION: [description courte]
LANGAGES: [langages de programmation]
STATISTIQUES: [étoiles, forks, etc.]
README: [contenu principal du README]
TECHNOLOGIES: [liste des technologies]
"""

    # Template pour documentation
    DOCUMENTATION = """Visite l'URL {url} et analyse-la comme une page de documentation technique.
Extrait les informations suivantes:

1. Titre de la page de documentation
2. Section / catégorie
3. Contenu principal de la documentation
4. Exemples de code (si présents)
5. Notes importantes ou avertissements

Concentre-toi sur le contenu technique et éducatif.
Ignore les menus de navigation et éléments secondaires.

Format de réponse attendu:
TITRE: [titre de la documentation]
SECTION: [section ou catégorie]
CONTENU: [contenu technique complet]
EXEMPLES: [exemples de code s'il y en a]
NOTES: [notes importantes ou avertissements]
"""

    # Template pour Légifrance (spécifique au contexte français)
    LEGIFRANCE = """Visite l'URL {url} qui est un texte juridique sur Légifrance.

Étapes à suivre:
1. Attends que la page soit complètement chargée (environ 5 secondes)
2. Recherche le contenu principal qui contient le texte juridique
   - Généralement dans un élément avec id='content' ou class='corpsTexte'
3. Extrait d'abord le titre du texte juridique
4. Puis extrait le contenu du texte juridique (articles, sections, etc.)
5. Ignore les menus, publicités, en-têtes, pieds de page et les éléments secondaires
6. Assure-toi de capturer l'intégralité du texte juridique

Format de réponse attendu:
TITRE: [titre du texte juridique]
CONTENU: [texte juridique complet avec articles et sections]
"""

    @staticmethod
    def get_prompt(content_type: str, url: str, custom_prompt: str = None) -> str:
        """
        Récupère le prompt approprié pour le type de contenu.

        Args:
            content_type: Type de contenu ("general", "article", "product", etc.)
            url: URL à extraire
            custom_prompt: Prompt personnalisé (optionnel, prioritaire)

        Returns:
            Prompt formaté avec l'URL
        """
        # Si un prompt personnalisé est fourni, l'utiliser
        if custom_prompt:
            return custom_prompt.format(url=url) if "{url}" in custom_prompt else custom_prompt

        # Cas spécial pour Légifrance
        if "legifrance.gouv.fr" in url.lower():
            return PromptTemplates.LEGIFRANCE.format(url=url)

        # Sélectionner le template selon le type
        templates: Dict[str, str] = {
            "general": PromptTemplates.GENERAL,
            "webpage": PromptTemplates.GENERAL,
            "article": PromptTemplates.ARTICLE,
            "product": PromptTemplates.PRODUCT,
            "repository": PromptTemplates.REPOSITORY,
            "documentation": PromptTemplates.DOCUMENTATION,
        }

        template = templates.get(content_type, PromptTemplates.GENERAL)
        return template.format(url=url)
