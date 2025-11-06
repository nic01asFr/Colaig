"""
Gestionnaire pour Playwright.
"""

import os
import sys
import subprocess
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# État d'initialisation global
_playwright_initialized = False
_playwright_available = False


async def ensure_playwright_installed() -> bool:
    """
    Vérifie et installe Playwright si nécessaire.

    Returns:
        True si Playwright est disponible et configuré
    """
    global _playwright_initialized, _playwright_available

    if _playwright_initialized:
        return _playwright_available

    try:
        # Configurer les variables d'environnement pour le mode headless
        os.environ["PLAYWRIGHT_HEADLESS"] = "true"
        os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "0"
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"

        # Forcer le mode headless si DISPLAY n'est pas défini
        if "DISPLAY" not in os.environ or os.environ.get("DISPLAY") == "":
            os.environ["DISPLAY"] = ""

        logger.info("Vérification de l'installation de Playwright...")

        # Vérifier si le binaire Chromium existe
        chromium_path = os.path.expanduser(
            "~/.cache/ms-playwright/chromium-1161/chrome-linux/chrome"
        )

        if os.path.exists(chromium_path):
            logger.info(f"Binaire Playwright trouvé: {chromium_path}")
            _playwright_initialized = True

            # Tester le lancement de Playwright
            try:
                test_cmd = """
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    browser.close()
print('OK')
"""
                result = subprocess.run(
                    [sys.executable, "-c", test_cmd],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=os.environ.copy(),
                    timeout=30
                )

                if result.returncode == 0 and "OK" in result.stdout:
                    logger.info("Playwright fonctionne correctement")
                    _playwright_available = True
                else:
                    logger.warning(
                        f"Playwright installé mais ne fonctionne pas correctement: "
                        f"{result.stderr}"
                    )

            except Exception as e:
                logger.warning(f"Erreur lors du test de Playwright: {e}")

        else:
            # Installer Playwright
            logger.info("Installation de Playwright...")

            install_cmd = [sys.executable, "-m", "playwright", "install", "chromium"]

            result = subprocess.run(
                install_cmd,
                capture_output=True,
                text=True,
                check=False,
                env=os.environ.copy(),
                timeout=300
            )

            if result.returncode == 0:
                logger.info("Playwright installé avec succès")
                _playwright_initialized = True
                _playwright_available = True
            else:
                logger.error(f"Échec de l'installation de Playwright: {result.stderr}")

    except Exception as e:
        logger.error(f"Erreur lors de la configuration de Playwright: {e}")
        _playwright_initialized = True
        _playwright_available = False

    return _playwright_available


def is_playwright_available() -> bool:
    """
    Vérifie si Playwright est disponible.

    Returns:
        True si Playwright est disponible
    """
    return _playwright_available
