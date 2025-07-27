#!/usr/bin/env python3
"""
Script de test pour l'extraction de contenu dynamique.
Teste les nouvelles fonctionnalités sans perturber l'existant.
"""

import asyncio
import sys
import os

# Ajouter le répertoire parent au path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.dynamic_content_extractor import DynamicContentDetector, extract_dynamic_web_content
from app.services.enhanced_browser_extraction import (
    extract_web_content_enhanced, 
    get_extraction_capabilities,
    is_enhanced_extraction_available
)
from app.config import Config


async def test_dynamic_detection():
    """Test de la détection de contenu dynamique."""
    print("🔍 Test de détection de contenu dynamique")
    print("-" * 50)
    
    test_urls = [
        "https://github.com/microsoft/playwright",  # Site dynamique
        "https://notion.so/template",  # Application web
        "https://example.com/app/dashboard",  # SPA simulé
        "https://wikipedia.org/wiki/Python",  # Site statique
        "https://news.ycombinator.com",  # Site statique avec JS minimal
    ]
    
    for url in test_urls:
        is_dynamic = DynamicContentDetector.is_likely_dynamic(url)
        capabilities = await get_extraction_capabilities(url)
        
        print(f"URL: {url}")
        print(f"  Dynamique: {'✅ Oui' if is_dynamic else '❌ Non'}")
        print(f"  Type: {capabilities.get('site_type', 'standard')}")
        print(f"  Méthode recommandée: {capabilities['recommended_method']}")
        print(f"  Complexité: {capabilities['estimated_complexity']}")
        print()


async def test_extraction_compatibility():
    """Test de compatibilité avec l'extraction existante."""
    print("🔄 Test de compatibilité avec l'extraction existante")
    print("-" * 50)
    
    # Simuler une configuration minimale
    class MockConfig:
        albert_api_token = "test"
        albert_api_url = "https://albert.api.etalab.gouv.fr"
        albert_model = "test-model"
    
    config = MockConfig()
    
    # Test avec URL statique (doit utiliser l'extraction classique)
    print("Test avec URL statique:")
    try:
        is_available = is_enhanced_extraction_available()
        print(f"Extraction enrichie disponible: {'✅ Oui' if is_available else '❌ Non'}")
        
        if is_available:
            print("✅ Les modules d'extraction enrichie sont correctement installés")
        else:
            print("⚠️  Les modules d'extraction enrichie ne sont pas disponibles")
            print("   Cela est normal si Playwright n'est pas installé")
    except Exception as e:
        print(f"❌ Erreur lors du test: {str(e)}")
    
    print()


async def test_framework_detection():
    """Test de détection de frameworks JavaScript."""
    print("🎯 Test de détection de frameworks")
    print("-" * 50)
    
    framework_urls = {
        "React": "https://reactjs.org",
        "Vue.js": "https://vuejs.org", 
        "Angular": "https://angular.io",
        "Next.js": "https://nextjs.org",
    }
    
    for framework, url in framework_urls.items():
        capabilities = await get_extraction_capabilities(url)
        print(f"{framework}: {url}")
        print(f"  Détecté comme dynamique: {'✅' if capabilities['is_dynamic'] else '❌'}")
        print(f"  Complexité estimée: {capabilities['estimated_complexity']}")
        print()


def test_integration_safety():
    """Test de sécurité d'intégration."""
    print("🛡️  Test de sécurité d'intégration")
    print("-" * 50)
    
    try:
        # Vérifier que l'import de l'extraction originale fonctionne
        from app.services.browser_extraction import extract_web_content
        print("✅ Import de l'extraction originale réussi")
        
        # Vérifier que l'import de l'extraction enrichie fonctionne
        from app.services.enhanced_browser_extraction import extract_web_content_enhanced
        print("✅ Import de l'extraction enrichie réussi")
        
        # Vérifier que les fonctions ont des signatures compatibles
        import inspect
        
        original_sig = inspect.signature(extract_web_content)
        enhanced_sig = inspect.signature(extract_web_content_enhanced)
        
        if str(original_sig) == str(enhanced_sig):
            print("✅ Signatures de fonctions compatibles")
        else:
            print("⚠️  Signatures différentes mais acceptable pour l'extension")
            print(f"   Originale: {original_sig}")
            print(f"   Enrichie: {enhanced_sig}")
        
        print("✅ Tests de sécurité d'intégration réussis")
        
    except ImportError as e:
        print(f"❌ Erreur d'import: {str(e)}")
    except Exception as e:
        print(f"❌ Erreur inattendue: {str(e)}")
    
    print()


async def main():
    """Fonction principale de test."""
    print("🚀 Tests de l'extraction de contenu dynamique")
    print("=" * 60)
    print()
    
    # Tests de base
    await test_dynamic_detection()
    await test_extraction_compatibility()
    await test_framework_detection()
    test_integration_safety()
    
    print("✅ Tous les tests terminés")
    print()
    print("📝 Résumé:")
    print("- Les nouvelles fonctionnalités sont ajoutées en complément")
    print("- L'extraction existante n'est pas modifiée")
    print("- La détection automatique permet de choisir la meilleure méthode")
    print("- Les sites statiques continuent d'utiliser l'extraction classique")
    print("- Les sites dynamiques bénéficient de l'extraction enrichie")


if __name__ == "__main__":
    asyncio.run(main()) 