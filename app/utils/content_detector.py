"""
Utilitaire pour détecter le type de contenu d'une URL.
"""

from urllib.parse import urlparse
from typing import Literal


ContentType = Literal["article", "product", "repository", "documentation", "webpage"]


class ContentDetector:
    """Détecte le type de contenu à partir d'une URL."""

    # Patterns de détection
    REPOSITORY_DOMAINS = ["github.com", "gitlab.com", "bitbucket.org"]

    DOCUMENTATION_PATTERNS = [
        "docs.", "documentation", "developer", "api",
        "reference", "manual", "guide"
    ]

    ECOMMERCE_DOMAINS = [
        "amazon", "ebay", "walmart", "alibaba",
        "shopping", "store", "shop"
    ]

    NEWS_DOMAINS = [
        "news", "blog", "article", "post",
        "medium.com", "substack.com"
    ]

    @staticmethod
    def detect(url: str) -> ContentType:
        """
        Détecte le type de contenu à partir de l'URL.

        Args:
            url: URL à analyser

        Returns:
            Type de contenu détecté
        """
        parsed_url = urlparse(url)
        domain = parsed_url.netloc.lower()
        path = parsed_url.path.lower()

        # Détection de dépôts de code
        if any(repo_domain in domain for repo_domain in ContentDetector.REPOSITORY_DOMAINS):
            return "repository"

        # Détection de documentation technique
        if any(
            docs_pattern in domain or docs_pattern in path
            for docs_pattern in ContentDetector.DOCUMENTATION_PATTERNS
        ):
            return "documentation"

        # Détection de sites e-commerce
        if any(
            shop_domain in domain
            for shop_domain in ContentDetector.ECOMMERCE_DOMAINS
        ):
            return "product"

        # Détection d'articles
        if any(
            news_domain in domain
            for news_domain in ContentDetector.NEWS_DOMAINS
        ):
            return "article"

        # Par défaut
        return "webpage"
