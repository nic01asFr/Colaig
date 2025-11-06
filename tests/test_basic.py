"""
Tests de base pour WebExtract Service.
"""

import pytest
from app.utils.content_detector import ContentDetector
from app.utils.prompts import PromptTemplates


def test_content_detector_github():
    """Test de détection de repository GitHub."""
    url = "https://github.com/user/repo"
    result = ContentDetector.detect(url)
    assert result == "repository"


def test_content_detector_docs():
    """Test de détection de documentation."""
    url = "https://docs.example.com/api/reference"
    result = ContentDetector.detect(url)
    assert result == "documentation"


def test_content_detector_ecommerce():
    """Test de détection de site e-commerce."""
    url = "https://www.amazon.com/product/12345"
    result = ContentDetector.detect(url)
    assert result == "product"


def test_content_detector_article():
    """Test de détection d'article."""
    url = "https://blog.example.com/my-post"
    result = ContentDetector.detect(url)
    assert result == "article"


def test_content_detector_generic():
    """Test de détection générique."""
    url = "https://www.example.com"
    result = ContentDetector.detect(url)
    assert result == "webpage"


def test_prompt_templates_article():
    """Test de génération de prompt pour article."""
    url = "https://example.com/article"
    prompt = PromptTemplates.get_prompt("article", url)
    assert "TITRE:" in prompt
    assert "CONTENU:" in prompt
    assert url in prompt


def test_prompt_templates_custom():
    """Test de prompt personnalisé."""
    url = "https://example.com"
    custom = "Extrait uniquement le titre de {url}"
    prompt = PromptTemplates.get_prompt("general", url, custom)
    assert prompt == f"Extrait uniquement le titre de {url}"


def test_prompt_templates_legifrance():
    """Test de prompt spécial pour Légifrance."""
    url = "https://www.legifrance.gouv.fr/codes/article"
    prompt = PromptTemplates.get_prompt("general", url)
    assert "Légifrance" in prompt
    assert "texte juridique" in prompt


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
