"""
Patch pour browser-use afin de forcer le mode headless dans Playwright.
Ce patch modifie le comportement du navigateur pour s'assurer qu'il démarre toujours en headless.
"""

import os
import logging
import importlib
import sys

# Configurer le logger
logger = logging.getLogger("albert-tchap")

try:
    # Vérifier si browser-use est installé
    importlib.import_module('browser_use')
    
    # Importer les modules de browser-use
    from browser_use.browser import Browser as OriginalBrowser
    from browser_use.agent.agent import Agent as OriginalAgent
    
    # Log l'application du patch
    logger.info("Application du patch de browser-use pour forcer le mode headless")
    
    # Patch pour la classe Browser de browser-use
    class HeadlessBrowserPatch(OriginalBrowser):
        """
        Version patchée de Browser qui force le navigateur à démarrer en mode headless.
        """
        
        async def setup(self):
            """Surcharge de la méthode setup pour forcer le mode headless."""
            # S'assurer que les variables d'environnement sont correctement définies
            os.environ["PLAYWRIGHT_HEADLESS"] = "true"
            
            # Vérifier si nous sommes dans un environnement Docker avec Xvfb configuré
            if "DISPLAY" not in os.environ or os.environ.get("DISPLAY") == "":
                # Si pas de DISPLAY défini, le définir à :99 pour compatibilité Xvfb
                os.environ["DISPLAY"] = ":99"
                logger.info("[PATCH] Configuration de DISPLAY=:99 pour Xvfb")
            else:
                logger.info(f"[PATCH] Utilisation du DISPLAY existant: {os.environ.get('DISPLAY')}")
            
            logger.info("[PATCH] Configuration du navigateur en mode headless forcé")
            
            # Appeler la méthode originale
            result = await super().setup()
            
            # Vérifier si le navigateur a été créé
            if hasattr(self, 'browser_context') and self.browser_context:
                logger.info("[PATCH] Navigateur démarré avec succès en mode headless")
            else:
                logger.warning("[PATCH] Échec du démarrage du navigateur")
            
            return result
        
        async def _get_playwright_browser(self, options=None):
            """Surcharge pour forcer les options headless."""
            if options is None:
                options = {}
            
            # Forcer le mode headless
            options["headless"] = True
            options["args"] = options.get("args", []) + ["--no-sandbox"]
            
            logger.info(f"[PATCH] Lancement du navigateur avec options: {options}")
            
            return await super()._get_playwright_browser(options)
    
    # Patch pour la classe Agent de browser-use
    class HeadlessAgentPatch(OriginalAgent):
        """
        Version patchée de Agent qui utilise notre Browser patchée.
        """
        
        async def _setup_browser(self):
            """Surcharge pour utiliser notre Browser patchée."""
            logger.info("[PATCH] Configuration de l'agent avec browser headless")
            
            # Utiliser notre browser patché au lieu du Browser original
            self.browser = HeadlessBrowserPatch(
                browser_type="chromium", 
                headless=True,  # Force headless à True
                args=["--no-sandbox"]
            )
            
            # Le reste de la configuration
            return await super()._setup_browser()
    
    # Appliquer les patches en remplaçant les classes originales
    sys.modules["browser_use"].browser.Browser = HeadlessBrowserPatch
    sys.modules["browser_use"].agent.agent.Agent = HeadlessAgentPatch
    
    logger.info("Patch browser-use pour le mode headless appliqué avec succès")

except ImportError as e:
    logger.warning(f"Impossible d'appliquer le patch de browser-use: {str(e)}")
except Exception as e:
    logger.error(f"Erreur lors de l'application du patch browser-use: {str(e)}")