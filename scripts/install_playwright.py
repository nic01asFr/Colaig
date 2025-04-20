#!/usr/bin/env python3
"""
Script pour installer Playwright, requis par browser-use.
Exécutez ce script après avoir installé les dépendances du projet.
"""

import os
import sys
import subprocess
import logging

# Configuration du logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("playwright-installer")

def run_command(command):
    """Exécute une commande shell et retourne le résultat."""
    logger.info(f"Exécution de la commande: {command}")
    try:
        result = subprocess.run(
            command, 
            shell=True, 
            check=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE,
            text=True
        )
        logger.info(f"Commande exécutée avec succès: {result.stdout}")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Erreur lors de l'exécution de la commande: {e.stderr}")
        return False

def install_playwright():
    """Installe Playwright et ses dépendances."""
    logger.info("Installation de Playwright...")
    
    # Vérifier si playwright-cli est déjà installé
    if run_command("playwright --version"):
        logger.info("Playwright est déjà installé.")
        return True
    
    # Installer playwright
    if not run_command("pip install playwright"):
        logger.error("Échec de l'installation de Playwright")
        return False
    
    # Installer les navigateurs nécessaires (seulement Chromium pour browser-use)
    if not run_command("playwright install --with-deps chromium"):
        logger.error("Échec de l'installation des navigateurs Playwright")
        return False
    
    # Configuration pour forcer le mode headless
    logger.info("Configuration des variables d'environnement pour Playwright")
    
    # Déterminer le fichier .env à modifier ou créer
    env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    
    # Vérifier si le fichier .env existe
    env_vars = {}
    if os.path.exists(env_file):
        try:
            with open(env_file, "r") as f:
                for line in f:
                    if "=" in line and not line.strip().startswith("#"):
                        key, value = line.strip().split("=", 1)
                        env_vars[key] = value
        except Exception as e:
            logger.warning(f"Erreur lors de la lecture du fichier .env: {str(e)}")
    
    # Ajouter ou mettre à jour les variables pour Playwright
    env_vars["PLAYWRIGHT_HEADLESS"] = "true"
    env_vars["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "0"
    
    # Écrire dans le fichier .env
    try:
        with open(env_file, "w") as f:
            for key, value in env_vars.items():
                f.write(f"{key}={value}\n")
        logger.info(f"Variables d'environnement configurées dans {env_file}")
    except Exception as e:
        logger.error(f"Erreur lors de l'écriture dans le fichier .env: {str(e)}")
    
    logger.info("Installation de Playwright terminée avec succès")
    return True

def main():
    """Fonction principale."""
    logger.info("Démarrage de l'installation de Playwright pour browser-use")
    
    # Vérifier que Python est disponible
    if not run_command("python --version"):
        logger.error("Python n'est pas disponible. Veuillez installer Python 3.10 ou supérieur.")
        sys.exit(1)
    
    # Vérifier que pip est disponible
    if not run_command("pip --version"):
        logger.error("pip n'est pas disponible. Veuillez installer pip.")
        sys.exit(1)
    
    # Installer playwright
    if install_playwright():
        logger.info("Installation terminée avec succès")
    else:
        logger.error("L'installation a échoué")
        sys.exit(1)

if __name__ == "__main__":
    main() 