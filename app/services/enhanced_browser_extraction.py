"""
Module d'extraction web enrichi avec support du contenu dynamique.
Étend le module browser_extraction existant sans le modifier.
"""

from typing import Dict, Any, Optional
import asyncio

from app.matrix_bot.config import logger
from app.services.dynamic_content_extractor import extract_dynamic_web_content, DynamicContentDetector
from app.services.browser_extraction import extract_web_content as extract_web_content_original


async def extract_web_content_enhanced(url: str, config) -> Dict[str, Any]:
    """
    Version enrichie de l'extraction web qui inclut le support du contenu dynamique.
    
    Cette fonction étend l'extraction existante en ajoutant une étape de détection
    et d'extraction spécialisée pour les sites web modernes, sans modifier le
    comportement pour les sites statiques.
    
    Flux d'exécution :
    1. Détection si le site nécessite une extraction dynamique
    2. Si oui, tentative d'extraction dynamique spécialisée
    3. Si échec ou site non-dynamique, utilisation du système existant
    4. Retour du meilleur résultat obtenu
    
    Args:
        url: URL à extraire
        config: Configuration de l'application
        
    Returns:
        Dict: Contenu extrait avec métadonnées
    """
    logger.info(f"Démarrage extraction web enrichie pour {url}")
    
    # ÉTAPE 1: Vérifier si l'URL nécessite une extraction dynamique
    is_dynamic = DynamicContentDetector.is_likely_dynamic(url)
    
    dynamic_result = None
    if is_dynamic:
        logger.info(f"Site dynamique détecté: {url}")
        try:
            # ÉTAPE 2: Tentative d'extraction dynamique spécialisée
            dynamic_result = await extract_dynamic_web_content(url, timeout=45)
            
            if dynamic_result and dynamic_result.get("content") and len(dynamic_result["content"]) > 300:
                logger.info(f"Extraction dynamique réussie pour {url}: {len(dynamic_result['content'])} caractères")
                # Ajouter des métadonnées pour identifier la méthode utilisée
                dynamic_result["enhanced_extraction"] = True
                dynamic_result["original_method"] = dynamic_result.get("extraction_method", "dynamic")
                return dynamic_result
            else:
                logger.info(f"Extraction dynamique insuffisante pour {url}, fallback au système existant")
        except Exception as e:
            logger.warning(f"Erreur extraction dynamique pour {url}: {str(e)}")
    
    # ÉTAPE 3: Utiliser le système d'extraction existant (inchangé)
    logger.info(f"Utilisation du système d'extraction existant pour {url}")
    try:
        original_result = await extract_web_content_original(url, config)
        
        if original_result and original_result.get("content"):
            # Ajouter des métadonnées pour traçabilité
            original_result["enhanced_extraction"] = False
            original_result["dynamic_detection"] = is_dynamic
            logger.info(f"Extraction classique réussie pour {url}: {len(original_result['content'])} caractères")
            return original_result
        else:
            logger.warning(f"Extraction classique insuffisante pour {url}")
            
    except Exception as e:
        logger.error(f"Erreur extraction classique pour {url}: {str(e)}")
    
    # ÉTAPE 4: Dernière tentative avec le résultat dynamique s'il existe
    if dynamic_result:
        logger.info(f"Utilisation du résultat dynamique comme fallback pour {url}")
        dynamic_result["enhanced_extraction"] = True
        dynamic_result["fallback_used"] = True
        return dynamic_result
    
    # ÉTAPE 5: Échec total
    logger.error(f"Échec total de l'extraction pour {url}")
    return {
        "title": url,
        "content": f"Échec de l'extraction pour {url}",
        "url": url,
        "status": 500,
        "extraction_method": "failed",
        "enhanced_extraction": False
    }


async def get_extraction_capabilities(url: str) -> Dict[str, Any]:
    """
    Analyse les capacités d'extraction disponibles pour une URL donnée.
    
    Args:
        url: URL à analyser
        
    Returns:
        Dict: Informations sur les capacités d'extraction
    """
    capabilities = {
        "url": url,
        "is_dynamic": DynamicContentDetector.is_likely_dynamic(url),
        "recommended_method": "standard",
        "available_methods": ["standard", "optimized", "httpx_fallback"],
        "estimated_complexity": "low",
    }
    
    if capabilities["is_dynamic"]:
        capabilities["recommended_method"] = "enhanced"
        capabilities["available_methods"].insert(0, "dynamic")
        capabilities["estimated_complexity"] = "high"
        
        # Analyser plus finement le type de site dynamique
        url_lower = url.lower()
        
        if any(pattern in url_lower for pattern in ['github.com', 'gitlab.com']):
            capabilities["site_type"] = "code_repository"
            capabilities["estimated_complexity"] = "medium"
        elif any(pattern in url_lower for pattern in ['notion.so', 'app.', 'dashboard.']):
            capabilities["site_type"] = "web_application"
            capabilities["estimated_complexity"] = "high"
        elif '/#/' in url:
            capabilities["site_type"] = "single_page_application"
            capabilities["estimated_complexity"] = "high"
        else:
            capabilities["site_type"] = "dynamic_content"
    
    return capabilities


def is_enhanced_extraction_available() -> bool:
    """
    Vérifie si l'extraction enrichie est disponible.
    
    Returns:
        bool: True si l'extraction enrichie est disponible
    """
    try:
        # Vérifier la disponibilité des modules nécessaires
        from app.services.dynamic_content_extractor import DynamicContentDetector
        from playwright.async_api import async_playwright
        return True
    except ImportError as e:
        logger.warning(f"Extraction enrichie non disponible: {str(e)}")
        return False


# Fonction utilitaire pour une migration progressive
async def extract_web_content_smart(url: str, config, prefer_enhanced: bool = True) -> Dict[str, Any]:
    """
    Extraction intelligente qui choisit automatiquement la meilleure méthode.
    
    Args:
        url: URL à extraire
        config: Configuration de l'application  
        prefer_enhanced: Si True, préfère l'extraction enrichie quand disponible
        
    Returns:
        Dict: Contenu extrait
    """
    if prefer_enhanced and is_enhanced_extraction_available():
        return await extract_web_content_enhanced(url, config)
    else:
        # Fallback vers l'extraction classique
        return await extract_web_content_original(url, config) 