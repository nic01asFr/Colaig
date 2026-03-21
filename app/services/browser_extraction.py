"""
Module pour extraire le contenu web avec la bibliothèque browser-use.
Permet d'extraire le contenu des pages web via un navigateur automatisé.
"""

import asyncio
import logging
import sys
import subprocess
import os
import httpx
import re
import traceback
from typing import Dict, Any, Optional, List, Callable, Union, Tuple

from browser_use import Agent
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage
from langchain_core.callbacks.manager import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from app.core_llm import AlbertApiClient
from pydantic import Field, PrivateAttr

from app.matrix_bot.config import logger

# Variable globale pour suivre l'état d'initialisation de Playwright
_playwright_initialized = False
_system_dependencies_ok = False
_browser_use_available = False

class BrowserExtractionError(Exception):
    """Exception levée lorsque l'extraction du contenu web échoue."""
    pass

def get_system_dependencies_command():
    """
    Détermine la commande d'installation des dépendances système selon la distribution.
    """
    # Essayer de détecter la distribution Linux
    if os.path.exists("/etc/debian_version"):
        # Debian/Ubuntu
        return (
            "apt-get update && apt-get install -y --no-install-recommends "
            "libglib2.0-0 libgobject-2.0-0 libnss3 libnssutil3 libsmime3 libnspr4 "
            "libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libgio-2.0-0 "
            "libexpat1 libx11-6 libxcomposite1 libxdamage1 libxext6 libxfixes3 "
            "libxrandr2 libgbm1 libxcb1 libxkbcommon0 libpango-1.0-0 libcairo2 "
            "libasound2 libatspi2.0-0"
        )
    elif os.path.exists("/etc/alpine-release"):
        # Alpine Linux
        return (
            "apk add --no-cache "
            "glib gobject nss nss-util smime nspr dbus atk atk-bridge cups "
            "glib-dev expat libx11 libxcomposite libxdamage libxext libxfixes "
            "libxrandr gbm xcb-util-wm pango cairo alsa-lib at-spi2-core"
        )
    elif os.path.exists("/etc/redhat-release"):
        # RedHat/CentOS/Fedora
        return (
            "yum install -y "
            "glib2 gobject-introspection nss nss-util nspr dbus atk at-spi2-atk cups "
            "libX11 libXcomposite libXdamage libXext libXfixes libXrandr mesa-libgbm "
            "libxcb libxkbcommon pango cairo alsa-lib at-spi2-core"
        )
    else:
        # Distribution inconnue - commande générique
        return "Veuillez installer les dépendances système requises pour Playwright selon votre distribution"

async def ensure_playwright_installed():
    """
    Vérifie et installe Playwright si nécessaire.
    Cette fonction est appelée au démarrage de l'application.
    Assure que Playwright est configuré pour fonctionner en mode headless.
    """
    global _playwright_initialized, _system_dependencies_ok, _browser_use_available
    
    if _playwright_initialized and _system_dependencies_ok:
        _browser_use_available = True
        return
    
    try:
        # Définir les variables d'environnement critiques pour le mode headless
        # Ces variables doivent être définies AVANT toute utilisation de Playwright
        os.environ["PLAYWRIGHT_HEADLESS"] = "true"
        os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "0"
        # Ces variables additionnelles peuvent aider dans certains environnements
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "0"  # Utilise le répertoire par défaut
        
        # Ne pas forcer DISPLAY à vide dans l'environnement Docker
        # Si nous sommes dans Docker, DISPLAY devrait rester à :99
        if "DISPLAY" not in os.environ or os.environ.get("DISPLAY") == "":
            os.environ["DISPLAY"] = ""  # Force le mode headless seulement si DISPLAY n'est pas déjà défini
        
        logger.info("Vérification de l'installation de Playwright...")
        
        # Vérifier si le répertoire des navigateurs existe
        chromium_path = os.path.expanduser("~/.cache/ms-playwright/chromium-1161/chrome-linux/chrome")
        if os.path.exists(chromium_path):
            logger.info("Binaire Playwright trouvé à: %s", chromium_path)
            _playwright_initialized = True
            
            # Essayer de lancer Playwright avec une commande minimale pour vérifier les dépendances
            try:
                # Vérifier explicitement que le mode headless est activé
                verify_cmd = (
                    "from playwright.sync_api import sync_playwright\n"
                    "import os\n"
                    "print('Variables d\\'environnement:')\n"
                    "print(f'  PLAYWRIGHT_HEADLESS = {os.environ.get(\"PLAYWRIGHT_HEADLESS\", \"non défini\")}')\n"
                    "print(f'  DISPLAY = {os.environ.get(\"DISPLAY\", \"non défini\")}')\n"
                    "with sync_playwright() as p:\n"
                    "    print('Chromium path:', p.chromium.executable_path)\n"
                    "    browser = p.chromium.launch(headless=True)\n"
                    "    print('Browser lancé avec succès')\n"
                    "    browser.close()\n"
                )
                
                logger.info("Vérification de la configuration headless de Playwright...")
                result = subprocess.run(
                    [sys.executable, "-c", verify_cmd],
                    capture_output=True,
                    text=True,
                    check=False,
                    env=os.environ.copy()  # Utiliser l'environnement avec nos variables
                )
                
                if result.returncode == 0:
                    logger.info("Les dépendances système de Playwright sont correctement installées.")
                    logger.info("Playwright fonctionne correctement en mode headless.")
                    _system_dependencies_ok = True
                    _browser_use_available = True
                else:
                    if "Host system is missing dependencies to run browsers" in result.stderr:
                        logger.warning("Dépendances système manquantes pour Playwright.")
                        install_cmd = get_system_dependencies_command()
                        logger.warning(f"Pour résoudre ce problème, exécutez: {install_cmd}")
                    elif "Looks like you launched a headed browser without having a XServer running" in result.stderr:
                        logger.warning("Erreur XServer détectée même en mode headless.")
                        logger.warning("Assurez-vous que xvfb est installé et que la commande est lancée avec xvfb-run.")
                    else:
                        logger.warning(f"Problème avec Playwright: {result.stderr}")
                    logger.debug(f"Sortie standard: {result.stdout}")
                    logger.debug(f"Sortie d'erreur: {result.stderr}")
            except Exception as e:
                logger.warning(f"Erreur lors de la vérification des dépendances système: {str(e)}")
        else:
            # Installation de Playwright
            logger.info("Installation de Playwright en cours...")
            
            # Installer Playwright avec la configuration headless
            install_cmd = [sys.executable, "-m", "playwright", "install", "chromium"]
            logger.info(f"Exécution de la commande d'installation: {' '.join(install_cmd)}")
            
            # Utiliser subprocess pour exécuter la commande en arrière-plan
            result = subprocess.run(
                install_cmd,
                capture_output=True,
                text=True,
                check=False,
                env=os.environ.copy()  # Utiliser l'environnement avec nos variables
            )
            
            if result.returncode == 0:
                logger.info("Playwright installé avec succès.")
                _playwright_initialized = True
                
                # Vérifier les dépendances système
                try:
                    # Vérifier que le mode headless fonctionne
                    verify_cmd = (
                        "from playwright.sync_api import sync_playwright\n"
                        "import os\n"
                        "print('Variables d\\'environnement après installation:')\n"
                        "print(f'  PLAYWRIGHT_HEADLESS = {os.environ.get(\"PLAYWRIGHT_HEADLESS\", \"non défini\")}')\n"
                        "print(f'  DISPLAY = {os.environ.get(\"DISPLAY\", \"non défini\")}')\n"
                        "with sync_playwright() as p:\n"
                        "    browser = p.chromium.launch(headless=True)\n"
                        "    print('Browser lancé avec succès après installation')\n"
                        "    browser.close()\n"
                    )
                    
                    logger.info("Vérification de la configuration headless après installation...")
                    test_result = subprocess.run(
                        [sys.executable, "-c", verify_cmd],
                        capture_output=True,
                        text=True,
                        check=False,
                        env=os.environ.copy()
                    )
                    
                    if test_result.returncode == 0:
                        logger.info("Les dépendances système de Playwright sont correctement installées.")
                        logger.info("Playwright fonctionne correctement en mode headless.")
                        _system_dependencies_ok = True
                        _browser_use_available = True
                    else:
                        if "Host system is missing dependencies to run browsers" in test_result.stderr:
                            logger.warning("Dépendances système manquantes pour Playwright.")
                            install_cmd = get_system_dependencies_command()
                            logger.warning(f"Pour résoudre ce problème, exécutez: {install_cmd}")
                        elif "Looks like you launched a headed browser without having a XServer running" in test_result.stderr:
                            logger.warning("Erreur XServer détectée même en mode headless.")
                            logger.warning("Assurez-vous que xvfb est installé et que la commande est lancée avec xvfb-run.")
                        else:
                            logger.warning(f"Problème avec Playwright: {test_result.stderr}")
                        logger.debug(f"Sortie standard: {test_result.stdout}")
                        logger.debug(f"Sortie d'erreur: {test_result.stderr}")
                except Exception as e:
                    logger.warning(f"Erreur lors de la vérification des dépendances système: {str(e)}")
            else:
                logger.warning(f"Problème lors de l'installation de Playwright: {result.stderr}")
                logger.warning("Les fonctionnalités d'extraction web pourraient ne pas fonctionner correctement.")
    
    except Exception as e:
        logger.error(f"Erreur lors de l'installation de Playwright: {str(e)}")
        logger.warning("Les fonctionnalités d'extraction web pourraient ne pas fonctionner correctement.")
    
    logger.info("Extraction web avec browser-use disponible: %s", _browser_use_available)

class AlbertAgentWrapper(BaseChatModel):
    """
    Wrapper pour intégrer AlbertApiClient avec browser-use.
    Cette classe adapte l'interface d'Albert pour fonctionner comme un LLM compatible avec browser-use.
    Hérite de BaseChatModel pour être compatible avec l'API browser-use.
    """
    
    # Définition des attributs Pydantic
    api_key: str = Field(description="Clé API pour Albert")
    base_url: str = Field(description="URL de base pour l'API Albert")
    model_name: str = Field(description="Nom du modèle à utiliser")
    
    # Attributs privés
    _aclient: Any = PrivateAttr()
    _dict_storage: Dict[str, Any] = PrivateAttr(default_factory=dict)
    
    def __init__(self, api_key: str, base_url: str, model: str, **kwargs):
        """Initialise le wrapper avec les informations d'API Albert."""
        super().__init__(api_key=api_key, base_url=base_url, model_name=model, **kwargs)
        self._aclient = AlbertApiClient(base_url=base_url, api_key=api_key)
        self._dict_storage = {}
    
    @property
    def _llm_type(self) -> str:
        """Retourne le type de LLM pour la compatibilité LangChain."""
        return "albert_api"
    
    def _generate(self, messages: List[BaseMessage], stop=None, run_manager=None, **kwargs):
        """
        Méthode synchrone requise par BaseChatModel.
        Non implémentée car nous utilisons uniquement l'interface asynchrone.
        """
        raise NotImplementedError("Cette méthode n'est pas implémentée. Utilisez _agenerate à la place.")
    
    async def _agenerate(
        self, 
        messages: List[BaseMessage], 
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None, 
        **kwargs
    ):
        """
        Génère une réponse de façon asynchrone à partir d'une liste de messages.
        
        Args:
            messages: Liste de messages au format LangChain
            stop: Liste de chaînes de caractères pour arrêter la génération (non utilisé)
            run_manager: Gestionnaire de callbacks (non utilisé)
            
        Returns:
            Réponse LangChain formatée
        """
        # Convertir les messages LangChain en format compatible avec Albert API
        albert_messages = []
        for msg in messages:
            if isinstance(msg, HumanMessage):
                albert_messages.append({
                    "role": "user",
                    "content": msg.content
                })
            elif isinstance(msg, AIMessage):
                albert_messages.append({
                    "role": "assistant", 
                    "content": msg.content
                })
            else:
                # Traiter d'autres types de messages comme du système si nécessaire
                albert_messages.append({
                    "role": "system" if msg.type == "system" else "user",
                    "content": msg.content
                })
        
        try:
            # Générer la réponse avec Albert
            response_text = await self._aclient.generate(
                model=self.model_name,
                messages=albert_messages
            )
            
            # Créer un message IA pour la réponse
            message = AIMessage(content=response_text)
            
            from langchain_core.outputs import ChatGeneration, ChatResult
            
            # Créer une génération à partir du message
            generation = ChatGeneration(message=message)
            
            # Retourner le résultat formaté
            return ChatResult(generations=[generation])
            
        except Exception as e:
            logger.error(f"Erreur lors de l'appel à Albert API: {str(e)}")
            raise
    
    async def __call__(self, messages=None, **kwargs):
        """
        Fonction appelée par browser-use pour générer des textes.
        
        Args:
            messages: Liste de messages au format ChatGPT
            
        Returns:
            Texte généré par Albert
        """
        if not messages:
            return ""
            
        try:
            # Convertir en format LangChain si nécessaire
            langchain_messages = []
            for msg in messages:
                if msg.get("role") == "user":
                    langchain_messages.append(HumanMessage(content=msg.get("content", "")))
                elif msg.get("role") == "assistant":
                    langchain_messages.append(AIMessage(content=msg.get("content", "")))
                else:
                    # Traiter les messages système ou autres
                    from langchain_core.messages import SystemMessage
                    langchain_messages.append(SystemMessage(content=msg.get("content", "")))
            
            # Utiliser l'API LangChain pour générer
            result = await self._agenerate(langchain_messages)
            
            # Extraire le texte de la réponse
            if result.generations and result.generations[0].message:
                return result.generations[0].message.content
            return ""
            
        except Exception as e:
            logger.error(f"Erreur lors de l'appel à Albert API via __call__: {str(e)}")
            raise
            
    async def get(self, messages=None, **kwargs):
        """
        Méthode get requise par browser-use 0.1.41.
        Délègue à la méthode __call__.
        
        Args:
            messages: Liste de messages au format ChatGPT
            
        Returns:
            Texte généré par Albert
        """
        return await self.__call__(messages, **kwargs)

    def pop(self, key, default=None):
        """
        Méthode pop pour être compatible avec browser-use.
        Cette méthode est appelée par browser-use pour certaines options.
        
        Args:
            key: Clé à pop
            default: Valeur par défaut si la clé n'existe pas
            
        Returns:
            La valeur par défaut car nous n'avons pas d'attributs à pop
        """
        return self._dict_storage.pop(key, default)

    def __getitem__(self, key):
        """
        Permet l'accès comme dictionnaire avec la notation wrapper[key].
        
        Args:
            key: Clé à récupérer
            
        Returns:
            La valeur associée à la clé
        """
        return self._dict_storage.get(key)
        
    def __setitem__(self, key, value):
        """
        Permet l'affectation comme dictionnaire avec la notation wrapper[key] = value.
        
        Args:
            key: Clé à définir
            value: Valeur à associer à la clé
        """
        self._dict_storage[key] = value

    async def close(self):
        """Ferme proprement le client."""
        await self._aclient.close()

async def setup_browser_agent(config) -> Agent:
    """
    Configure et retourne un agent browser-use avec Albert API.
    
    Args:
        config: Configuration de l'application contenant les clés API
        
    Returns:
        Agent browser-use configuré
    """
    # S'assurer que Playwright est installé avant de créer l'agent
    await ensure_playwright_installed()
    
    try:
        # Configurer le wrapper Albert API
        api_key = config.albert_api_token
        base_url = config.albert_api_url
        model = config.albert_model
        
        # Définir explicitement les variables d'environnement critiques pour le mode headless
        os.environ["PLAYWRIGHT_HEADLESS"] = "true"
        
        # Note: default_plan supprimé car non supporté dans la nouvelle version de browser-use
        
        # Créer un agent sans tâche spécifique pour le moment
        agent = Agent(
            task="",  # Sera défini lors de l'exécution
            # Browser-use attend un objet qui hérite de BaseChatModel
            llm=AlbertAgentWrapper(api_key=api_key, base_url=base_url, model=model)
        )
        
        return agent
    except Exception as e:
        logger.error(f"Erreur lors de la configuration de l'agent browser-use: {str(e)}")
        raise

async def extract_webpage_content_with_httpx(url: str) -> Tuple[str, str]:
    """
    Extrait le contenu d'une page web en utilisant httpx (méthode de repli).
    
    Args:
        url: URL de la page à extraire
        
    Returns:
        Tuple (titre, contenu) extrait de la page
    """
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        logger.info(f"Tentative d'extraction avec httpx pour {url}")
        response = await client.get(url)
        response.raise_for_status()
        
        html_content = response.text
        
        # Extraction du titre
        title_match = re.search(r'<title>(.*?)</title>', html_content, re.IGNORECASE | re.DOTALL)
        title = title_match.group(1).strip() if title_match else "Titre non trouvé"
        
        # Nettoyage du contenu pour un texte brut utilisable
        # Supprimer les balises script et style
        content = re.sub(r'<script.*?</script>', '', html_content, flags=re.IGNORECASE | re.DOTALL)
        content = re.sub(r'<style.*?</style>', '', content, flags=re.IGNORECASE | re.DOTALL)
        
        # Suppression des balises HTML
        content = re.sub(r'<[^>]*>', ' ', content)
        
        # Nettoyage des espaces multiples et des lignes vides
        content = re.sub(r'\s+', ' ', content).strip()
        
        # Regrouper les paragraphes
        paragraphs = re.split(r'\s{2,}', content)
        cleaned_content = '\n\n'.join(p.strip() for p in paragraphs if p.strip())
        
        logger.info(f"Extraction avec httpx réussie pour {url}")
        return title, cleaned_content

async def extract_with_llm_summary(url: str, config) -> Tuple[str, str]:
    """
    Utilise directement le LLM pour résumer le contenu d'une URL.
    
    Args:
        url: URL de la page à extraire
        config: Configuration contenant les clés API
        
    Returns:
        Tuple (titre, contenu) généré par le LLM
    """
    api_key = config.albert_api_token
    base_url = config.albert_api_url
    model = config.albert_model
    
    aclient = AlbertApiClient(base_url=base_url, api_key=api_key)
    
    try:
        prompt = f"""
        Je n'ai pas pu accéder directement au contenu de la page {url}. 
        D'après ta connaissance et ce que tu sais de cette URL, peux-tu me fournir :
        1. Un titre probable pour cette page
        2. Un résumé de ce qu'elle pourrait contenir
        
        Format de réponse souhaité :
        TITRE: [titre probable]
        
        CONTENU: [résumé du contenu probable]
        """
        
        messages = [{"role": "user", "content": prompt}]
        response = await aclient.generate(model=model, messages=messages)
        
        # Extraction du titre et du contenu de la réponse
        title_match = re.search(r'TITRE:\s*(.*?)(?:\n|$)', response, re.IGNORECASE)
        content_match = re.search(r'CONTENU:\s*(.*)', response, re.IGNORECASE | re.DOTALL)
        
        title = title_match.group(1).strip() if title_match else f"À propos de {url}"
        content = content_match.group(1).strip() if content_match else response
        
        return title, content
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction avec le LLM: {str(e)}")
        return f"À propos de {url}", f"Le contenu de {url} n'a pas pu être extrait."
    finally:
        await aclient.close()

async def extract_with_direct_playwright(url: str) -> Dict[str, Any]:
    """
    Extraction directe avec Playwright sans impliquer l'agent IA.
    Fonction optimisée pour extraire le contenu rapidement avant d'utiliser l'agent.
    
    Args:
        url: URL à extraire
        
    Returns:
        Dict: Contenu extrait ou None si échec
    """
    try:
        from playwright.async_api import async_playwright
        
        async with async_playwright() as p:
            # Configuration optimisée du navigateur
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage']
            )
            
            # Contexte avec paramètres optimisés
            context = await browser.new_context(
                viewport={"width": 1280, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            
            # Navigation avec timeout étendu
            try:
                await page.goto(url, wait_until="networkidle", timeout=45000)
                await page.wait_for_load_state("networkidle")
                
                # Attente supplémentaire pour le contenu dynamique
                await asyncio.sleep(2)
            except Exception as nav_error:
                logger.warning(f"Navigation issue pour {url}: {str(nav_error)}")
                # Continuer car la page peut être partiellement chargée
            
            # Extraction du titre
            title = await page.title()
            
            # Détection du type de site pour sélecteurs optimisés
            domain = url.split("//")[-1].split("/")[0]
            selectors = []
            
            # Sélecteurs spécifiques par domaine
            if "legifrance.gouv.fr" in url:
                selectors = ['.corpsTexte', '#texte_article_contenu', '#mentionsLegales', '#content']
            elif "service-public.fr" in url:
                selectors = ['#contenu', '.main-content', 'article', '.pane-content']
            elif "github.com" in url:
                selectors = ['.markdown-body', '.repository-content', 'readme-toc', 'article']
            
            # Ajouter les sélecteurs génériques après les spécifiques
            selectors.extend([
                'main', '[role="main"]', 'article', '.article', '#article',
                '.post', '#post', '.content', '#content', '.page-content',
                '.main-content', '#main-content', '.text', '.body'
            ])
            
            # Tentative d'extraction avec sélecteurs
            content = None
            for selector in selectors:
                element = await page.query_selector(selector)
                if element:
                    try:
                        extracted_text = await element.inner_text()
                        if extracted_text and len(extracted_text.strip()) > 200:
                            content = extracted_text
                            logger.info(f"Contenu extrait avec le sélecteur {selector} pour {url}")
                            break
                    except Exception as e:
                        logger.debug(f"Erreur avec sélecteur {selector}: {str(e)}")
            
            # Si aucun sélecteur n'a fonctionné, essayer l'extraction JavaScript
            if not content or len(content.strip()) < 200:
                logger.info(f"Aucun sélecteur efficace pour {url}, utilisation de l'extraction JavaScript")
                try:
                    # Suppression des éléments non pertinents et extraction du body
                    content = await page.evaluate("""() => {
                        // Éliminer éléments non pertinents
                        const elementsÀIgnorer = document.querySelectorAll(
                            'nav, header, footer, aside, [role="navigation"], [role="banner"], [role="complementary"], style, script, iframe'
                        );
                        
                        elementsÀIgnorer.forEach(el => {
                            if (el && el.parentNode) {
                                el.parentNode.removeChild(el);
                            }
                        });
                        
                        // Extraction intelligente du contenu principal
                        const mainContent = document.querySelector('main, [role="main"], article, .content, #content, .main');
                        return mainContent ? mainContent.innerText : document.body.innerText;
                    }""")
                except Exception as js_error:
                    logger.warning(f"Erreur lors de l'extraction JavaScript: {str(js_error)}")
                    # Fallback à l'extraction simple
                    content = await page.evaluate("() => document.body.innerText")
            
            await browser.close()
            
            # Vérifier si le contenu est substantiel
            if content and len(content.strip()) > 300:
                return {
                    "title": title,
                    "content": content.strip(),
                    "url": url,
                    "status": 200,
                    "extraction_method": "direct-playwright"
                }
            else:
                logger.info(f"Contenu extrait insuffisant pour {url} avec extraction directe")
                return None
                
    except Exception as e:
        logger.error(f"Erreur lors de l'extraction directe Playwright: {str(e)}")
        return None

def get_optimized_extraction_prompt(url: str) -> str:
    """
    Génère un prompt optimisé pour l'extraction avec Albert.
    
    Args:
        url: URL à extraire
        
    Returns:
        String: Prompt optimisé
    """
    # Détection du type de site
    domain_type = "general"
    if "legifrance.gouv.fr" in url:
        domain_type = "legifrance"
    elif "service-public.fr" in url:
        domain_type = "service-public"
    elif "github.com" in url:
        domain_type = "github"
    
    # Base commune pour tous les types de site
    base_prompt = f"""
Visite l'URL {url} et extrais uniquement le contenu principal.

ÉTAPES PRÉCISES À SUIVRE:
1. Accède à l'URL en utilisant l'action browser.goto("{url}")
2. Attends que la page soit complètement chargée avec browser.wait_for_load_state("networkidle")
3. Identifie le contenu principal de la page et ignore les menus, publicités, en-têtes et pieds de page
4. Extrais d'abord le titre principal de la page
5. Puis extrais le contenu principal en préservant sa structure

FORMAT DE RÉPONSE REQUIS:
TITRE: [titre principal de la page]

CONTENU: 
[contenu principal structuré]

IMPORTANT:
- N'ajoute AUCUN commentaire ou texte qui n'est pas dans la page originale
- Ne génère PAS de résumé - extrais uniquement le contenu réel
- Assure-toi que le titre et le contenu sont clairement séparés comme dans le format ci-dessus
"""

    # Ajout d'instructions spécifiques selon le type de site
    if domain_type == "legifrance":
        base_prompt += """
SPÉCIFIQUE LÉGIFRANCE:
- Le contenu juridique principal se trouve généralement dans l'élément .corpsTexte ou #texte_article_contenu
- Assure-toi d'extraire tous les articles numérotés et leurs contenus
- Préserve la numérotation et la hiérarchie des sections et articles
- Capture le titre du texte juridique, sa référence et sa date de publication
"""
    elif domain_type == "service-public":
        base_prompt += """
SPÉCIFIQUE SERVICE-PUBLIC:
- Le contenu principal se trouve généralement dans #contenu ou .main-content
- Inclus les éléments importants "À savoir" et "À noter" qui sont dans des encadrés
- Extrais les listes à puces qui contiennent des informations importantes
- Capture les dates de mise à jour ou de publication
"""
    elif domain_type == "github":
        base_prompt += """
SPÉCIFIQUE GITHUB:
- Le contenu principal se trouve dans .markdown-body
- Préserve la structure exacte du README ou du document
- Inclus les blocs de code en les délimitant clairement
- Maintiens les titres et la hiérarchie des sections
"""
    
    return base_prompt

class OptimizedAlbertAgentWrapper(AlbertAgentWrapper):
    """
    Version optimisée du wrapper Albert pour l'extraction web.
    Hérite de AlbertAgentWrapper pour maintenir la compatibilité.
    """
    
    async def __call__(self, messages=None, **kwargs):
        """Méthode optimisée appelée par browser-use."""
        if not messages:
            return ""
            
        # Ajouter un contexte spécifique à l'extraction web au message système
        enhanced_messages = []
        has_system_message = False
        
        web_context = """
Tu es un agent spécialisé en extraction de contenu web.
- Concentre-toi UNIQUEMENT sur l'extraction du contenu réel
- Ne génère JAMAIS de contenu qui n'est pas sur la page
- N'ajoute pas de commentaires ou d'explications personnelles
- Identifie et extrais d'abord le TITRE principal
- Puis extrais le CONTENU principal
"""
        
        # Enrichir le message système ou en ajouter un
        for msg in messages:
            if msg.get("role") == "system":
                enhanced_messages.append({
                    "role": "system",
                    "content": web_context + "\n\n" + msg.get("content", "")
                })
                has_system_message = True
            else:
                enhanced_messages.append(msg)
        
        # Ajouter un message système si aucun n'est présent
        if not has_system_message:
            enhanced_messages = [{"role": "system", "content": web_context}] + enhanced_messages
        
        try:
            # Utiliser la méthode parente pour générer la réponse
            return await super().__call__(enhanced_messages, **kwargs)
        except Exception as e:
            logger.error(f"Erreur OptimizedAlbertAgentWrapper: {str(e)}")
            return f"Erreur d'extraction: {str(e)}"

class AlbertOCRAgentWrapper(OptimizedAlbertAgentWrapper):
    """
    Wrapper Albert spécialisé pour l'extraction web avec OCR.
    Hérite d'OptimizedAlbertAgentWrapper et ajoute des capacités OCR.
    """
    
    def __init__(self, api_key: str, base_url: str, model: str, **kwargs):
        super().__init__(api_key, base_url, model, **kwargs)
        self.ocr_enabled = True
        self.visual_elements_detected = []
        
    async def __call__(self, messages=None, **kwargs):
        """Méthode optimisée avec support OCR."""
        if not messages:
            return ""
            
        # Contexte spécialisé pour navigation avec analyse visuelle
        ocr_context = """
Tu es un agent spécialisé en extraction de contenu web avec analyse d'éléments visuels.

INSTRUCTIONS PRINCIPALES :
- Navigue sur la page et identifie le contenu textuel ET visuel
- Repère les graphiques, tableaux, diagrammes, images avec texte
- Pour les éléments visuels importants, fais défiler pour les rendre visibles
- Extrait le contenu textuel standard de la page
- Signale la présence d'éléments nécessitant une analyse OCR

ÉLÉMENTS VISUELS À DÉTECTER :
- Images contenant du texte (captures d'écran, infographies)
- Graphiques et diagrammes avec légendes
- Tableaux complexes avec données
- Schémas et organigrammes
- Charts et visualisations de données

WORKFLOW :
1. Charger complètement la page
2. Identifier les éléments visuels pertinents
3. Extraire le contenu textuel standard
4. Positionner les éléments visuels pour capture OCR
5. Signaler quels éléments nécessitent une analyse OCR
"""
        
        enhanced_messages = []
        has_system_message = False
        
        # Enrichir le message système avec le contexte OCR
        for msg in messages:
            if msg.get("role") == "system":
                enhanced_messages.append({
                    "role": "system",
                    "content": ocr_context + "\n\n" + msg.get("content", "")
                })
                has_system_message = True
            else:
                enhanced_messages.append(msg)
        
        # Ajouter le contexte OCR si aucun message système
        if not has_system_message:
            enhanced_messages = [{"role": "system", "content": ocr_context}] + enhanced_messages
        
        try:
            # Utiliser la méthode parente optimisée
            result = await super().__call__(enhanced_messages, **kwargs)
            
            # Analyser la réponse pour détecter les mentions d'éléments visuels
            if isinstance(result, str):
                self._detect_visual_elements_from_response(result)
            
            return result
            
        except Exception as e:
            logger.error(f"Erreur AlbertOCRAgentWrapper: {str(e)}")
            return f"Erreur d'extraction avec OCR: {str(e)}"
    
    def _detect_visual_elements_from_response(self, response: str):
        """
        Analyse la réponse de l'agent pour identifier les éléments visuels mentionnés.
        """
        visual_keywords = [
            "graphique", "graph", "chart", "tableau", "table", "image", 
            "diagramme", "schéma", "infographie", "capture", "screenshot",
            "visualization", "données visuelles", "élément visuel"
        ]
        
        response_lower = response.lower()
        detected_elements = []
        
        for keyword in visual_keywords:
            if keyword in response_lower:
                detected_elements.append(keyword)
        
        if detected_elements:
            self.visual_elements_detected = detected_elements
            logger.info(f"Éléments visuels détectés par l'agent: {detected_elements}")
    
    def has_visual_elements(self) -> bool:
        """Retourne True si des éléments visuels ont été détectés."""
        return len(self.visual_elements_detected) > 0
    
    def get_detected_elements(self) -> list:
        """Retourne la liste des éléments visuels détectés."""
        return self.visual_elements_detected.copy()

async def extract_from_agent_response(result):
    """
    Extraction optimisée depuis la réponse de l'agent.
    Gère les différents formats de retour possibles.
    
    Args:
        result: Réponse de l'agent (string ou AgentHistoryList)
        
    Returns:
        String: Contenu extrait et formaté
    """
    # Si c'est une chaîne simple, vérifier si le format attendu est respecté
    if isinstance(result, str):
        # Vérifier si le format TITRE/CONTENU est respecté
        if "TITRE:" in result and "CONTENU:" in result:
            return result.strip()
        else:
            # Si pas de format spécifique, retourner tel quel
            return result.strip()
        
    # Si c'est un AgentHistoryList, extraction structurée
    if hasattr(result, "__class__") and result.__class__.__name__ == "AgentHistoryList":
        # Stratégie 1: Dernière réponse de l'agent
        if hasattr(result, "steps") and result.steps:
            for step in reversed(result.steps):
                if hasattr(step, "result") and isinstance(step.result, str) and len(step.result.strip()) > 50:
                    # Vérifier si le résultat contient "TITRE:" et "CONTENU:"
                    if "TITRE:" in step.result and "CONTENU:" in step.result:
                        return step.result.strip()
                    else:
                        # Résultat textuel significatif
                        return step.result.strip()
        
        # Stratégie 2: Recherche du texte le plus long dans toute la structure
        longest_text = ""
        if hasattr(result, "steps"):
            for step in result.steps:
                for attr_name in ["result", "thought", "observation"]:
                    if hasattr(step, attr_name):
                        text = getattr(step, attr_name)
                        if isinstance(text, str) and len(text.strip()) > len(longest_text):
                            longest_text = text.strip()
        
        if longest_text:
            return longest_text
            
    # Fallback: conversion simple en string
    return str(result).strip()

# Amélioration de la fonction principale d'extraction
async def extract_web_content_optimized(url: str, config) -> Dict[str, Any]:
    """
    Version optimisée de extract_web_content qui intègre l'approche hybride.
    Cette fonction est utilisée en interne par extract_web_content.
    
    Args:
        url: URL de la page à extraire
        config: Configuration de l'application
        
    Returns:
        Dict: Contenu de la page avec métadonnées
    """
    # ÉTAPE 1: Extraction directe avec Playwright
    direct_result = await extract_with_direct_playwright(url)
    
    # Si l'extraction directe a produit un contenu substantiel, l'utiliser
    if direct_result and direct_result.get("content") and len(direct_result["content"]) > 500:
        logger.info(f"Extraction directe Playwright réussie pour {url}")
        return direct_result
        
    # ÉTAPE 2: Extraction via agent IA avec instructions optimisées
    try:
        # Configurer l'agent avec une tâche spécifique optimisée
        extraction_task = get_optimized_extraction_prompt(url)
        
        # Configurer le wrapper Albert API optimisé
        agent = Agent(
            task=extraction_task,
            llm=OptimizedAlbertAgentWrapper(
                api_key=config.albert_api_token,
                base_url=config.albert_api_url,
                model=config.albert_model
            )
            # default_plan supprimé car non supporté dans la nouvelle version de browser-use
        )
        
        # Exécuter l'agent avec un timeout adapté (45s au lieu de 30s)
        extraction_task = asyncio.create_task(agent.run())
        try:
            content = await asyncio.wait_for(extraction_task, timeout=45.0)
            
            # Extraction robuste depuis la réponse de l'agent
            processed_content = await extract_from_agent_response(content)
            
            # Vérification de la qualité du contenu
            if processed_content and len(processed_content) > 100:
                # Analyse du contenu pour extraire titre et corps selon le format
                title_match = re.search(r'TITRE:\s*(.*?)(?:\n|$)', processed_content, re.IGNORECASE)
                content_match = re.search(r'CONTENU:\s*(.*)', processed_content, re.IGNORECASE | re.DOTALL)
                
                if title_match and content_match:
                    title = title_match.group(1).strip()
                    content = content_match.group(1).strip()
                else:
                    # Fallback: Première ligne comme titre si format non respecté
                    lines = processed_content.strip().split('\n')
                    title = lines[0] if lines else url
                    content = '\n'.join(lines[1:]) if len(lines) > 1 else processed_content
                
                return {
                    "title": title,
                    "content": content,
                    "url": url,
                    "status": 200,
                    "extraction_method": "optimized-browser-use"
                }
        except asyncio.TimeoutError:
            logger.warning(f"Timeout de l'extraction IA optimisée pour {url}")
            extraction_task.cancel()
    except Exception as e:
        logger.error(f"Erreur extraction IA optimisée: {str(e)}")
    
    # ÉTAPE 3: Fallback aux méthodes existantes
    return None  # Indique de continuer avec les méthodes existantes

async def extract_web_content(url: str, config) -> Dict[str, Any]:
    """
    Extrait le contenu d'une page web avec browser-use.
    Version améliorée qui utilise d'abord les nouvelles méthodes optimisées,
    puis se replie sur les méthodes existantes en cas d'échec.
    
    Args:
        url: URL de la page à extraire
        config: Configuration de l'application
        
    Returns:
        Dict: Contenu de la page avec métadonnées
    """
    # Vérifier si browser-use est disponible
    await ensure_browser_use_available()
    
    browser_use_failed = False
    browser_use_error = None
    
    try:
        # Essayer d'abord avec l'approche optimisée
        optimized_result = await extract_web_content_optimized(url, config)
        if optimized_result:
            return optimized_result
        
        # Si browser-use est disponible, continuer avec la méthode existante
        if _browser_use_available:
            # Configurer l'agent avec une tâche spécifique pour l'extraction
            # Personnaliser la tâche en fonction du domaine
            is_legifrance = "legifrance.gouv.fr" in url.lower()
            
            if is_legifrance:
                logger.info("Détection d'une URL Légifrance - Utilisation d'une tâche d'extraction spécifique")
                extraction_task = (
                    f"Visite l'URL {url} qui est un texte juridique sur Légifrance. "
                    f"1. Attends que la page soit complètement chargée (environ 5 secondes). "
                    f"2. Recherche le contenu principal qui contient le texte juridique - généralement dans un élément avec id='content' ou class='corpsTexte'. "
                    f"3. Extrait d'abord le titre du texte juridique. "
                    f"4. Puis extrait le contenu du texte juridique (articles, sections, etc). "
                    f"5. Ignore les menus, publicités, en-têtes, pieds de page et les éléments secondaires. "
                    f"6. Assure-toi de capturer l'intégralité du texte juridique. "
                    f"7. Retourne le contenu sous forme de texte brut bien structuré."
                )
            else:
                extraction_task = (
                    f"Visite l'URL {url} et extrait le contenu principal du site. "
                    f"Ignore les menus, publicités, en-têtes, pieds de page et éléments de navigation. "
                    f"Concentre-toi uniquement sur le contenu principal et pertinent. "
                    f"Retourne le contenu sous forme de texte brut sans formatage. "
                    f"Inclus le titre principal dans ta réponse."
                )
            
            # Configurer le wrapper Albert API
            api_key = config.albert_api_token
            base_url = config.albert_api_url
            model = config.albert_model
            
            # Définir explicitement les variables d'environnement critiques pour le mode headless
            os.environ["PLAYWRIGHT_HEADLESS"] = "true"
            
            # Note: default_plan supprimé car non supporté dans la nouvelle version de browser-use
            
            # Créer et configurer l'agent
            agent = Agent(
                task=extraction_task,
                llm=AlbertAgentWrapper(api_key=api_key, base_url=base_url, model=model)
                # default_plan supprimé car non supporté dans la nouvelle version de browser-use
            )
            
            try:
                # Exécuter l'agent pour extraire le contenu avec un timeout de 30 secondes
                logger.info(f"Démarrage de l'extraction browser-use pour {url}")
                
                # Créer une tâche asyncio avec timeout
                extraction_task = asyncio.create_task(agent.run())
                try:
                    # Attendre avec timeout
                    content = await asyncio.wait_for(extraction_task, timeout=30.0)
                    logger.info(f"Extraction terminée pour {url}")
                except asyncio.TimeoutError:
                    logger.warning(f"Timeout de l'extraction browser-use pour {url}")
                    extraction_task.cancel()
                    browser_use_failed = True
                    browser_use_error = "Timeout de l'extraction"
                    raise BrowserExtractionError("Timeout de l'extraction browser-use après 30 secondes")
            except Exception as e:
                logger.error(f"Erreur lors de l'extraction browser-use: {str(e)}")
                browser_use_failed = True
                browser_use_error = str(e)
                raise
                
            # Vérifier si le contenu est une chaîne de caractères
            if not isinstance(content, str):
                logger.warning(f"Le résultat de browser-use n'est pas une chaîne mais un {type(content)}")
                
                # Gestion spécifique pour AgentHistoryList
                if hasattr(content, "__class__") and content.__class__.__name__ == "AgentHistoryList":
                    try:
                        # Essayer d'extraire le contenu textuel des actions de l'agent
                        content_str = ""
                        
                        # Analyser la structure de l'AgentHistoryList pour le débogage
                        if hasattr(content, "steps") and len(content.steps) > 0:
                            logger.info(f"AgentHistoryList contient {len(content.steps)} étapes")
                            
                            # Afficher un aperçu de chaque étape pour le débogage
                            for i, step in enumerate(content.steps):
                                step_info = f"Étape {i+1}: "
                                if hasattr(step, "type"):
                                    step_info += f"Type={step.type}, "
                                if hasattr(step, "thought"):
                                    step_info += f"Thought={step.thought[:30]}..., " if step.thought and len(step.thought) > 30 else f"Thought={step.thought}, "
                                if hasattr(step, "action"):
                                    step_info += f"Action={str(step.action)[:30]}..., " if step.action and len(str(step.action)) > 30 else f"Action={step.action}, "
                                if hasattr(step, "result"):
                                    has_result = step.result is not None
                                    result_preview = str(step.result)[:30] + "..." if has_result and len(str(step.result)) > 30 else str(step.result)
                                    step_info += f"Result={result_preview}"
                                logger.info(step_info)
                            
                            # Extraire le contenu du dernier résultat d'étape réussi
                            for step in reversed(content.steps):
                                if hasattr(step, "result") and step.result:
                                    if isinstance(step.result, str) and step.result.strip():
                                        content_str = step.result
                                        logger.info(f"Contenu extrait de l'étape: {content_str[:100]}...")
                                        break
                        
                        # Vérifier s'il y a des ActionResult dans all_results
                        if not content_str and hasattr(content, "all_results") and content.all_results:
                            logger.info(f"AgentHistoryList contient {len(content.all_results)} résultats d'action")
                            
                            # Parcourir les résultats d'action pour trouver du contenu
                            for action_result in content.all_results:
                                if hasattr(action_result, "extracted_content") and action_result.extracted_content:
                                    if isinstance(action_result.extracted_content, str) and action_result.extracted_content.strip():
                                        content_str = action_result.extracted_content
                                        logger.info(f"Contenu extrait de ActionResult: {content_str[:100]}...")
                                        break
                        
                        # Vérifier les modèles outputs si disponibles
                        if not content_str and hasattr(content, "all_model_outputs") and content.all_model_outputs:
                            logger.info(f"AgentHistoryList contient {len(content.all_model_outputs)} sorties de modèle")
                            
                            # Parcourir les sorties de modèle pour trouver du contenu
                            for model_output in content.all_model_outputs:
                                if isinstance(model_output, str) and model_output.strip():
                                    content_str = model_output
                                    logger.info(f"Contenu extrait d'une sortie de modèle: {content_str[:100]}...")
                                    break
                        
                        # Si aucun contenu valide n'a été trouvé, essayer avec la représentation en chaîne
                        if not content_str:
                            content_str = str(content)
                            logger.info(f"Contenu extrait par conversion directe: {content_str[:100]}...")
                            
                        logger.info(f"Contenu extrait du AgentHistoryList: {len(content_str)} caractères")
                        # Afficher les 500 premiers caractères pour le débogage
                        if content_str:
                            logger.info(f"Aperçu du contenu extrait: {content_str[:min(500, len(content_str))]}...")
                        
                        # Vérifier si le contenu extrait est vide ou trop court
                        if not content_str.strip() or len(content_str.strip()) < 50:
                            logger.warning("Contenu extrait de AgentHistoryList trop court ou vide")
                            browser_use_failed = True
                            browser_use_error = "Contenu extrait insuffisant"
                            raise BrowserExtractionError("Contenu extrait insuffisant")
                        
                        content = content_str
                        
                    except Exception as e:
                        logger.error(f"Erreur lors de l'extraction du contenu du AgentHistoryList: {str(e)}")
                        browser_use_failed = True
                        browser_use_error = str(e)
                        raise BrowserExtractionError(f"Erreur lors de l'extraction du contenu: {str(e)}")
                else:
                    # Pour les autres types, conversion directe en chaîne
                    content_str = str(content)
                    
                    # Vérifier si le contenu converti est vide ou trop court
                    if not content_str.strip() or len(content_str.strip()) < 50:
                        logger.warning("Contenu converti trop court ou vide")
                        browser_use_failed = True
                        browser_use_error = "Contenu converti insuffisant"
                        raise BrowserExtractionError("Contenu converti insuffisant")
                    
                    content = content_str
            else:
                # Vérifier si le contenu est vide ou trop court
                if not content.strip() or len(content.strip()) < 50:
                    logger.warning("Contenu extrait trop court ou vide")
                    browser_use_failed = True
                    browser_use_error = "Contenu extrait insuffisant"
                    raise BrowserExtractionError("Contenu extrait insuffisant")
                
            # Extraction du titre (première ligne généralement)
            lines = content.strip().split('\n')
            title = lines[0] if lines else ""
            
            return {
                "title": title,
                "content": content,
                "url": url,
                "status": 200,
                "extraction_method": "browser-use"
            }
        else:
            # browser-use n'est pas disponible, utiliser la méthode de secours
            logger.warning("browser-use n'est pas disponible, utilisation de la méthode de secours")
            browser_use_failed = True
            browser_use_error = "browser-use non disponible"
    except Exception as e:
        # En cas d'erreur avec browser-use, marquer l'échec
        logger.error(f"Échec de l'extraction avec browser-use: {str(e)}")
        browser_use_failed = True
        browser_use_error = str(e)
    
    # Si browser-use a échoué, utiliser la méthode de secours
    if browser_use_failed:
        logger.warning(f"Passage à la méthode d'extraction de secours pour {url} - Raison: {browser_use_error}")
        return await extract_with_httpx(url)

async def extract_structured_web_data(
    url: str, 
    config,
    extraction_type: str = "general"
) -> Dict[str, Any]:
    """
    Extrait des données structurées d'une page web avec browser-use.
    
    Args:
        url: URL de la page à extraire
        config: Configuration de l'application
        extraction_type: Type d'extraction structurée ("general", "article", "product", etc.)
        
    Returns:
        Dict: Données structurées extraites
    """
    # Vérifier si browser-use est disponible
    await ensure_browser_use_available()
    
    try:
        # Si browser-use est disponible, utiliser la méthode principale
        if _browser_use_available:
            # Configurer les prompts selon le type d'extraction
            extraction_prompts = {
                "general": (
                    f"Visite l'URL {url} et extrait les informations suivantes sous format JSON:\n"
                    f"- title: Titre principal de la page\n"
                    f"- content: Contenu principal du site (ignore menus, publicités, etc.)\n"
                    f"- description: Description concise de la page\n"
                    f"- category: Catégorie de la page (documentation, article, blog, etc.)\n"
                    f"Retourne uniquement le JSON."
                ),
                "article": (
                    f"Visite l'URL {url} et analyse-la comme un article. Extrait les informations suivantes sous format JSON:\n"
                    f"- title: Titre de l'article\n"
                    f"- author: Auteur de l'article (si disponible)\n"
                    f"- date: Date de publication (si disponible)\n"
                    f"- content: Contenu principal de l'article\n"
                    f"- summary: Un bref résumé de l'article\n"
                    f"Retourne uniquement le JSON."
                ),
                "product": (
                    f"Visite l'URL {url} et analyse-la comme une page de produit. Extrait les informations suivantes sous format JSON:\n"
                    f"- title: Nom du produit\n"
                    f"- price: Prix du produit (si disponible)\n"
                    f"- description: Description du produit\n"
                    f"- features: Caractéristiques principales (liste)\n"
                    f"Retourne uniquement le JSON."
                ),
                "repository": (
                    f"Visite l'URL {url} et analyse-la comme un dépôt de code. Extrait les informations suivantes sous format JSON:\n"
                    f"- title: Nom du projet\n"
                    f"- description: Description du projet\n"
                    f"- language: Langage principal (si disponible)\n"
                    f"- stars: Nombre d'étoiles (si disponible)\n"
                    f"- readme: Contenu principal du README\n"
                    f"Retourne uniquement le JSON."
                ),
            }
            
            # Sélectionner la tâche d'extraction
            extraction_task = extraction_prompts.get(
                extraction_type, extraction_prompts["general"]
            )
            
            # Configurer le wrapper Albert API
            api_key = config.albert_api_token
            base_url = config.albert_api_url
            model = config.albert_model
            
            # Définir explicitement les variables d'environnement critiques pour le mode headless
            os.environ["PLAYWRIGHT_HEADLESS"] = "true"
            
            # Note: default_plan supprimé car non supporté dans la nouvelle version de browser-use
            
            # Créer et configurer l'agent
            agent = Agent(
                task=extraction_task,
                llm=OptimizedAlbertAgentWrapper(
                    api_key=config.albert_api_token,
                    base_url=config.albert_api_url,
                    model=config.albert_model
                )
                # default_plan supprimé car non supporté dans la nouvelle version de browser-use
            )
            
            # Exécuter l'agent pour extraire le contenu (timeout : 90s)
            logger.info(f"Démarrage de l'extraction structurée browser-use pour {url}")
            try:
                result = await asyncio.wait_for(agent.run(), timeout=90.0)
            except asyncio.TimeoutError:
                logger.error(f"Timeout (90s) dépassé lors de l'extraction structurée browser-use pour {url}")
                return {
                    "error": "Délai d'extraction dépassé (90s)",
                    "url": url,
                    "status": 0,
                    "extraction_method": "browser-use-timeout"
                }
            logger.info(f"Extraction structurée terminée pour {url}")
            
            # Vérifier si le résultat est de type AgentHistoryList
            if not isinstance(result, str) and hasattr(result, "__class__") and result.__class__.__name__ == "AgentHistoryList":
                try:
                    # Essayer d'extraire le contenu JSON
                    result_str = ""
                    
                    # Parcourir les étapes pour extraire le JSON
                    if hasattr(result, "steps") and result.steps:
                        for step in reversed(result.steps):
                            if hasattr(step, "result") and step.result:
                                if isinstance(step.result, str) and "{" in step.result and "}" in step.result:
                                    result_str = step.result
                                    logger.info(f"Contenu JSON trouvé dans une étape: {result_str[:min(len(result_str), 100)]}...")
                                    break
                    
                    # Vérifier s'il y a des ActionResult dans all_results
                    if not result_str and hasattr(result, "all_results") and result.all_results:
                        logger.info(f"AgentHistoryList contient {len(result.all_results)} résultats d'action")
                        
                        # Parcourir les résultats d'action pour trouver du contenu JSON
                        for action_result in result.all_results:
                            if hasattr(action_result, "extracted_content") and action_result.extracted_content:
                                content = action_result.extracted_content
                                if isinstance(content, str) and "{" in content and "}" in content:
                                    result_str = content
                                    logger.info(f"Contenu JSON extrait d'un ActionResult: {result_str[:min(len(result_str), 100)]}...")
                                    break
                    
                    # Vérifier les sorties de modèle si disponibles
                    if not result_str and hasattr(result, "all_model_outputs") and result.all_model_outputs:
                        logger.info(f"AgentHistoryList contient {len(result.all_model_outputs)} sorties de modèle")
                        
                        # Parcourir les sorties de modèle pour trouver du contenu JSON
                        for model_output in result.all_model_outputs:
                            if isinstance(model_output, str) and "{" in model_output and "}" in model_output:
                                result_str = model_output
                                logger.info(f"Contenu JSON extrait d'une sortie de modèle: {result_str[:min(len(result_str), 100)]}...")
                                break
                    
                    # Si aucun contenu JSON n'a été trouvé, essayer d'extraire du texte et demander à l'API de le convertir
                    if not result_str or not ("{" in result_str and "}" in result_str):
                        # Chercher du texte dans les résultats
                        text_content = ""
                        
                        # Essayer d'extraire du texte des étapes
                        if hasattr(result, "steps") and result.steps:
                            for step in reversed(result.steps):
                                if hasattr(step, "result") and step.result and isinstance(step.result, str) and step.result.strip():
                                    text_content = step.result
                                    break
                        
                        # Essayer avec les ActionResults si nécessaire
                        if not text_content and hasattr(result, "all_results") and result.all_results:
                            for action_result in result.all_results:
                                if hasattr(action_result, "extracted_content") and action_result.extracted_content:
                                    if isinstance(action_result.extracted_content, str) and action_result.extracted_content.strip():
                                        text_content = action_result.extracted_content
                                        break
                    
                    if not result_str:
                        result_str = str(result)
                        
                    logger.info(f"Contenu structuré extrait du AgentHistoryList: {len(result_str)} caractères")
                    result = result_str
                except Exception as e:
                    logger.error(f"Erreur lors de l'extraction du contenu structuré du AgentHistoryList: {str(e)}")
                    return {
                        "error": f"Erreur lors de l'extraction du contenu structuré: {str(e)}",
                        "url": url,
                        "status": 0,
                        "extraction_method": "browser-use-fail"
                    }
            # Vérifier si le résultat est une chaîne de caractères et essayer de le parser comme JSON
            if isinstance(result, str):
                try:
                    # Essayer de convertir le résultat en JSON
                    import json
                    json_result = json.loads(result)
                    return {
                        **json_result,  # Utiliser directement le résultat JSON
                        "url": url,
                        "status": 200,
                        "extraction_method": "browser-use"
                    }
                except json.JSONDecodeError:
                    # Si le résultat n'est pas un JSON valide, retourner le texte brut
                    logger.warning(f"Le résultat de browser-use n'est pas un JSON valide: {result[:100]}...")
                    return {
                        "raw_result": result,
                        "url": url,
                        "status": 200,
                        "extraction_method": "browser-use-text"
                    }
            else:
                # Si le résultat n'est pas une chaîne, le convertir en chaîne
                logger.warning(f"Le résultat de browser-use n'est pas une chaîne mais un {type(result)}")
                str_result = str(result)
                return {
                    "raw_result": str_result,
                    "url": url,
                    "status": 200,
                    "extraction_method": "browser-use-object"
                }
        elif getattr(config, 'allow_fallback_extraction', True):
            # Méthode de repli: Utiliser le LLM directement
            aclient = AlbertApiClient(base_url=config.albert_api_url, api_key=config.albert_api_token)
            
            try:
                # Créer un prompt adapté au type d'extraction
                extraction_description = {
                    "general": "une synthèse générale",
                    "article": "un résumé d'article avec titre, auteur et contenu principal",
                    "product": "des informations sur ce produit (nom, prix, description)",
                    "repository": "des informations sur ce dépôt de code (projet, description, fonctionnalités)",
                    "documentation": "un résumé de la documentation technique"
                }.get(extraction_type, "une synthèse générale")
                
                prompt = f"""
                Je n'ai pas pu accéder directement au contenu de la page {url}.
                Basé sur tes connaissances, peux-tu me fournir {extraction_description}?
                
                Si l'URL semble correspondre à {extraction_type}, génère un résultat structuré
                qui serait typiquement trouvé sur une telle page.
                """
                
                messages = [{"role": "user", "content": prompt}]
                result_str = await aclient.generate(model=config.albert_model, messages=messages)
                
                return {
                    "content": result_str,
                    "url": url,
                    "extraction_type": extraction_type,
                    "status": 200,
                    "extraction_method": "llm-fallback"
                }
            except Exception as llm_error:
                logger.error(f"Extraction LLM a échoué: {str(llm_error)}")
                raise BrowserExtractionError(f"Impossible d'extraire le contenu structuré de l'URL {url}")
            finally:
                await aclient.close()
        else:
            raise BrowserExtractionError(f"browser-use n'est pas disponible et les méthodes de repli ne sont pas activées")
            
    except BrowserExtractionError:
        # Relayer les erreurs spécifiques de notre application
        raise
    except Exception as e:
        # Capture de la stack trace
        stack_trace = traceback.format_exc()
        logger.error(f"Erreur lors de l'extraction structurée avec browser-use: {str(e)}")
        logger.debug(f"Détails de l'erreur: {stack_trace}")
        
        # Capturer et convertir toutes les autres erreurs en BrowserExtractionError
        error_msg = f"Erreur lors de l'extraction structurée avec browser-use: {str(e)}"
        raise BrowserExtractionError(error_msg) from e

def is_browser_extraction_available() -> bool:
    """
    Vérifie si l'extraction de contenu web avec browser-use est disponible.
    
    Returns:
        True si l'extraction est disponible, False sinon
    """
    # Note: Ici nous ne pouvons pas utiliser await car la fonction n'est pas asynchrone
    # Nous pouvons uniquement vérifier l'état actuel sans initialisation asynchrone
    return _browser_use_available 

async def check_playwright_headless_status():
    """
    Fonction de diagnostic pour vérifier l'état de Playwright en mode headless.
    Affiche des informations détaillées sur la configuration et tente de lancer
    un navigateur headless pour confirmer que tout fonctionne correctement.
    
    Returns:
        bool: True si le mode headless fonctionne correctement, False sinon
    """
    logger.info("=== DIAGNOSTIC DE PLAYWRIGHT EN MODE HEADLESS ===")
    
    # Vérifier les variables d'environnement
    headless_env = os.environ.get("PLAYWRIGHT_HEADLESS", "non défini")
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "non défini")
    skip_download = os.environ.get("PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD", "non défini")
    display_env = os.environ.get("DISPLAY", "non défini")
    
    logger.info(f"Variables d'environnement:")
    logger.info(f"  PLAYWRIGHT_HEADLESS = {headless_env}")
    logger.info(f"  PLAYWRIGHT_BROWSERS_PATH = {browsers_path}")
    logger.info(f"  PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = {skip_download}")
    logger.info(f"  DISPLAY = {display_env}")
    
    # Vérifier l'existence du binaire Chromium
    chromium_path = os.path.expanduser("~/.cache/ms-playwright/chromium-1161/chrome-linux/chrome")
    logger.info(f"Binaire Chromium: {os.path.exists(chromium_path)}")
    if os.path.exists(chromium_path):
        logger.info(f"  Chemin: {chromium_path}")
        logger.info(f"  Exécutable: {os.access(chromium_path, os.X_OK)}")
    
    # Vérifier les dépendances système
    try:
        # Utiliser ldd pour vérifier les bibliothèques partagées
        if os.path.exists("/usr/bin/ldd"):
            logger.info("Vérification des bibliothèques partagées:")
            ldd_cmd = ["/usr/bin/ldd", chromium_path]
            ldd_result = subprocess.run(ldd_cmd, capture_output=True, text=True, check=False)
            if ldd_result.returncode == 0:
                # Vérifier les bibliothèques manquantes
                missing_libs = [line for line in ldd_result.stdout.split('\n') if "not found" in line]
                if missing_libs:
                    logger.warning("Bibliothèques manquantes:")
                    for lib in missing_libs:
                        logger.warning(f"  {lib.strip()}")
                else:
                    logger.info("  Toutes les bibliothèques requises sont présentes")
            else:
                logger.warning(f"Impossible de vérifier les bibliothèques: {ldd_result.stderr}")
    except Exception as e:
        logger.warning(f"Erreur lors de la vérification des bibliothèques: {str(e)}")
    
    # Tester l'initialisation de Playwright
    success = False
    try:
        # Tester avec un code Python minimal
        test_code = """
from playwright.sync_api import sync_playwright
import os
print('Variables d\\'environnement dans le test:')
print(f'  PLAYWRIGHT_HEADLESS = {os.environ.get("PLAYWRIGHT_HEADLESS", "non défini")}')
print(f'  DISPLAY = {os.environ.get("DISPLAY", "non défini")}')

# Vérifier que nous sommes dans Docker avec Xvfb
using_docker_xvfb = os.environ.get("DISPLAY") == ":99"
if using_docker_xvfb:
    print("Détection de l'environnement Docker avec Xvfb")

with sync_playwright() as p:
    print(f'Chromium executable: {p.chromium.executable_path}')
    try:
        browser = p.chromium.launch(headless=True)
        print('Browser lancé avec succès en mode headless')
        context = browser.new_context()
        print('Context créé avec succès')
        page = context.new_page()
        print('Page créée avec succès')
        browser.close()
        print('Browser fermé avec succès')
        print('Test complet réussi')
    except Exception as e:
        print(f'Erreur: {str(e)}')
        raise e
"""
        
        logger.info("Tentative de lancement d'un navigateur headless:")
        # Copier l'environnement actuel
        env_copy = os.environ.copy()
        # S'assurer que le mode headless est activé
        env_copy["PLAYWRIGHT_HEADLESS"] = "true"
        
        # Ne pas modifier DISPLAY si déjà défini à :99 (pour Docker)
        if "DISPLAY" in env_copy and env_copy["DISPLAY"] == ":99":
            logger.info("  Utilisation de la configuration Docker avec DISPLAY=:99")
        
        result = subprocess.run(
            [sys.executable, "-c", test_code],
            capture_output=True,
            text=True,
            check=False,
            env=env_copy
        )
        
        # Afficher les résultats
        if result.stdout:
            for line in result.stdout.split("\n"):
                if line.strip():
                    logger.info(f"  {line.strip()}")
        
        if result.stderr:
            for line in result.stderr.split("\n"):
                if line.strip():
                    logger.warning(f"  STDERR: {line.strip()}")
        
        success = result.returncode == 0
        if success:
            logger.info("✅ Test de Playwright en mode headless réussi")
        else:
            logger.warning("❌ Test de Playwright en mode headless échoué")
    
    except Exception as e:
        logger.error(f"Erreur lors du test de Playwright: {str(e)}")
        success = False
    
    logger.info("=== FIN DU DIAGNOSTIC ===")
    return success

async def ensure_browser_use_available():
    """
    S'assure que l'extraction browser-use est disponible avant de l'utiliser.
    Initialise Playwright si nécessaire.
    """
    global _browser_use_available
    
    if not _browser_use_available:
        await ensure_playwright_installed()
        
        # Si l'initialisation a échoué, essayer de diagnostiquer le problème
        if not _browser_use_available:
            logger.warning("L'initialisation de browser-use a échoué, lancement d'un diagnostic...")
            await check_playwright_headless_status()
    
    return _browser_use_available 

async def extract_with_httpx(url: str) -> Dict[str, Any]:
    """
    Extraction avec HTTPX comme méthode de fallback ultime.
    """
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
            response = await client.get(url)
            response.raise_for_status()
            
            # Extraction simple du contenu HTML
            html_content = response.text
            
            # Extraction du titre
            title_match = re.search(r'<title>(.*?)</title>', html_content, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else url
            
            # Nettoyage basique du HTML
            content = re.sub(r'<script.*?</script>', '', html_content, flags=re.IGNORECASE | re.DOTALL)
            content = re.sub(r'<style.*?</style>', '', content, flags=re.IGNORECASE | re.DOTALL)
            content = re.sub(r'<[^>]*>', ' ', content)
            content = re.sub(r'\s+', ' ', content).strip()
            
            return {
                "title": title,
                "content": content,
                "url": url,
                "status": 200,
                "extraction_method": "httpx-fallback"
            }
    except Exception as e:
        logger.error(f"Erreur extraction HTTPX: {str(e)}")
        return {
            "title": url,
            "content": f"Erreur d'extraction: {str(e)}",
            "url": url,
            "status": 500,
            "extraction_method": "httpx-fallback-error"
        }

async def extract_web_content_with_ocr(url: str, config) -> Dict[str, Any]:
    """
    Extraction web hybride utilisant navigation LLM + capture d'écran + OCR Albert.
    
    Cette fonction combine :
    1. Navigation web guidée par Albert LLM
    2. Capture d'écran des parties importantes
    3. OCR Albert sur les captures pour extraire texte des images/graphiques
    4. Fusion du contenu textuel et OCR
    
    Args:
        url: URL à extraire
        config: Configuration avec clés Albert API
        
    Returns:
        Dict: Contenu enrichi avec données OCR
    """
    from playwright.async_api import async_playwright
    
    try:
        # S'assurer que Playwright est installé
        await ensure_playwright_installed()
        
        async with async_playwright() as p:
            # Configuration du navigateur avec mode headless
            browser = await p.chromium.launch(
                headless=True,
                args=['--disable-gpu', '--no-sandbox', '--disable-dev-shm-usage']
            )
            
            context = await browser.new_context(
                viewport={"width": 1280, "height": 1080},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36"
            )
            
            page = await context.new_page()
            
            try:
                # Étape 1: Navigation avec guidance LLM
                logger.info(f"Navigation vers {url} avec guidance LLM")
                
                # Configurer l'agent Albert pour la navigation
                agent = Agent(
                    task=f"""Navigue vers {url} et identifie les éléments visuels importants (graphiques, tableaux, images avec texte).
                    
                    Étapes à suivre :
                    1. Accéder à l'URL et attendre le chargement complet
                    2. Identifier s'il y a des graphiques, tableaux, diagrammes ou images contenant du texte
                    3. Si oui, faire défiler pour que ces éléments soient visibles
                    4. Extraire d'abord le contenu textuel standard de la page
                    5. Signaler la présence d'éléments visuels nécessitant OCR
                    """,
                    llm=AlbertAgentWrapper(
                        api_key=config.albert_api_token,
                        base_url=config.albert_api_url,
                        model=config.albert_model
                    )
                )
                
                # Exécution avec timeout adapté
                navigation_result = await asyncio.wait_for(agent.run(), timeout=60.0)
                
                # Étape 2: Extraction du contenu textuel standard
                await page.goto(url, wait_until="networkidle", timeout=45000)
                await asyncio.sleep(3)  # Laisser le temps au contenu dynamique
                
                # Extraire le titre
                title = await page.title()
                
                # Extraire le contenu textuel principal
                text_content = await page.evaluate("""() => {
                    // Supprimer les éléments non pertinents
                    const elementsToRemove = document.querySelectorAll(
                        'nav, header, footer, aside, [role="navigation"], [role="banner"], [role="complementary"], style, script, iframe'
                    );
                    elementsToRemove.forEach(el => el.remove());
                    
                    // Extraire le contenu principal
                    const mainContent = document.querySelector('main, [role="main"], article, .content, #content, .main');
                    return mainContent ? mainContent.innerText : document.body.innerText;
                }""")
                
                # Étape 3: Détection et capture des éléments visuels
                logger.info("Détection des éléments visuels nécessitant OCR")
                
                visual_elements = await page.evaluate("""() => {
                    const elements = [];
                    
                    // Chercher les images, graphiques, tableaux complexes
                    const candidates = document.querySelectorAll(
                        'img, canvas, svg, .chart, .graph, .diagram, table, .table, .data-table'
                    );
                    
                    candidates.forEach((el, index) => {
                        const rect = el.getBoundingClientRect();
                        if (rect.width > 100 && rect.height > 50) {  // Taille minimale
                            elements.push({
                                index: index,
                                tag: el.tagName.toLowerCase(),
                                className: el.className,
                                width: rect.width,
                                height: rect.height,
                                top: rect.top,
                                left: rect.left
                            });
                        }
                    });
                    
                    return elements;
                }""")
                
                ocr_texts = []
                albert_client = AlbertApiClient(
                    base_url=config.albert_api_url, 
                    api_key=config.albert_api_token
                )
                
                # Étape 4: Capture et OCR des éléments visuels
                if visual_elements:
                    logger.info(f"Trouvé {len(visual_elements)} éléments visuels à analyser par OCR")
                    
                    for i, element in enumerate(visual_elements[:5]):  # Limiter à 5 éléments
                        try:
                            # Faire défiler pour rendre l'élément visible
                            await page.evaluate(f"""() => {{
                                const elements = document.querySelectorAll(
                                    'img, canvas, svg, .chart, .graph, .diagram, table, .table, .data-table'
                                );
                                if (elements[{element['index']}]) {{
                                    elements[{element['index']}].scrollIntoView({{behavior: 'smooth', block: 'center'}});
                                }}
                            }}""")
                            
                            await asyncio.sleep(1)  # Laisser le temps au défilement
                            
                            # Capture d'écran de l'élément spécifique
                            element_screenshot = await page.locator(f"img, canvas, svg, .chart, .graph, .diagram, table, .table, .data-table").nth(element['index']).screenshot()
                            
                            # OCR avec Albert API
                            logger.info(f"OCR élément {i+1}/{len(visual_elements)}: {element['tag']}.{element['className']}")
                            
                            ocr_text = await albert_client.ocr_image(
                                element_screenshot, 
                                filename=f"element_{i}_{element['tag']}.png"
                            )
                            
                            if ocr_text.strip():
                                ocr_texts.append({
                                    "element_type": element['tag'],
                                    "element_class": element['className'],
                                    "ocr_text": ocr_text.strip(),
                                    "position": f"top:{element['top']}, left:{element['left']}"
                                })
                                logger.info(f"OCR réussi - {len(ocr_text)} caractères extraits")
                            
                        except Exception as e:
                            logger.warning(f"Erreur OCR élément {i}: {str(e)}")
                            continue
                
                # Étape 5: Capture d'écran globale comme fallback
                full_screenshot = await page.screenshot(full_page=True)
                
                try:
                    # OCR sur la capture d'écran complète
                    logger.info("OCR sur la capture d'écran complète comme fallback")
                    full_page_ocr = await albert_client.ocr_image(
                        full_screenshot, 
                        filename=f"fullpage_{url.split('/')[-1]}.png"
                    )
                    
                    if full_page_ocr.strip():
                        ocr_texts.append({
                            "element_type": "fullpage",
                            "element_class": "screenshot",
                            "ocr_text": full_page_ocr.strip(),
                            "position": "fullpage"
                        })
                
                except Exception as e:
                    logger.warning(f"Erreur OCR page complète: {str(e)}")
                
                await albert_client.close()
                
                # Étape 6: Fusion du contenu textuel et OCR
                combined_content = text_content
                
                if ocr_texts:
                    combined_content += "\n\n=== CONTENU EXTRAIT PAR OCR ===\n"
                    
                    for ocr_item in ocr_texts:
                        combined_content += f"\n[{ocr_item['element_type'].upper()}] {ocr_item['element_class']}:\n"
                        combined_content += ocr_item['ocr_text']
                        combined_content += f"\n(Position: {ocr_item['position']})\n"
                
                return {
                    "title": title,
                    "content": combined_content,
                    "url": url,
                    "status": 200,
                    "extraction_method": "hybrid-llm-ocr",
                    "text_content_length": len(text_content),
                    "ocr_elements_found": len(ocr_texts),
                    "ocr_texts": ocr_texts,
                    "has_visual_content": len(ocr_texts) > 0
                }
                
            finally:
                await browser.close()
                
    except Exception as e:
        logger.error(f"Erreur extraction hybride LLM+OCR: {str(e)}")
        
        # Fallback vers extraction standard
        try:
            return await extract_web_content(url, config)
        except Exception as fallback_error:
            logger.error(f"Erreur fallback: {str(fallback_error)}")
            return {
                "title": url,
                "content": f"Erreur d'extraction hybride: {str(e)}",
                "url": url,
                "status": 500,
                "extraction_method": "hybrid-llm-ocr-error"
            }

async def extract_with_llm_and_ocr(url: str, albert_client: "AlbertApiClient") -> Dict[str, Any]:
    """
    Extraction hybride combinant browser-use avec OCR Albert pour les éléments visuels.
    Utilise le modèle de vision Mistral d'Albert API pour l'OCR.
    """
    try:
        from playwright.async_api import async_playwright
        import asyncio
        
        logger.info(f"Extraction hybride LLM+OCR pour {url}")
        
        result = {
            "url": url,
            "title": "",
            "content": "",
            "images_ocr": [],
            "method": "hybrid_llm_ocr",
            "success": True
        }
        
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            )
            page = await context.new_page()
            
            try:
                # Navigation vers l'URL
                logger.info(f"Navigation vers {url} avec guidance LLM")
                await page.goto(url, wait_until="networkidle", timeout=30000)
                
                # Extraire le titre
                title = await page.title()
                result["title"] = title
                
                # 1. D'abord, extraire le contenu textuel standard
                try:
                    # Supprimer les éléments non pertinents
                    await page.evaluate("""
                        // Supprimer scripts, styles, nav, footer
                        ['script', 'style', 'nav', 'footer', '.nav', '.navigation', 
                         '.sidebar', '.ads', '.advertisement', '.social-share'].forEach(selector => {
                            document.querySelectorAll(selector).forEach(el => el.remove());
                        });
                    """)
                    
                    # Extraire le contenu principal
                    content_selectors = [
                        'main', 'article', '.content', '.main-content', 
                        '.post-content', '.entry-content', '#content', 'body'
                    ]
                    
                    content_text = ""
                    for selector in content_selectors:
                        try:
                            element = await page.query_selector(selector)
                            if element:
                                text = await element.inner_text()
                                if len(text.strip()) > len(content_text.strip()):
                                    content_text = text
                                    break
                        except:
                            continue
                    
                    result["content"] = content_text.strip()
                    
                except Exception as e:
                    logger.warning(f"Erreur extraction contenu textuel: {e}")
                
                # 2. Identifier et traiter les éléments visuels pour OCR
                logger.info("Recherche d'éléments visuels nécessitant OCR")
                
                # Rechercher des images, graphiques, tableaux complexes
                visual_elements = await page.evaluate("""
                    () => {
                        const elements = [];
                        
                        // Images avec potentiel texte
                        document.querySelectorAll('img').forEach((img, i) => {
                            const rect = img.getBoundingClientRect();
                            if (rect.width > 100 && rect.height > 50) {
                                elements.push({
                                    type: 'image',
                                    index: i,
                                    src: img.src,
                                    alt: img.alt || '',
                                    width: rect.width,
                                    height: rect.height
                                });
                            }
                        });
                        
                        // Canvas (graphiques, charts)
                        document.querySelectorAll('canvas').forEach((canvas, i) => {
                            const rect = canvas.getBoundingClientRect();
                            if (rect.width > 100 && rect.height > 50) {
                                elements.push({
                                    type: 'canvas',
                                    index: i,
                                    width: rect.width,
                                    height: rect.height
                                });
                            }
                        });
                        
                        // SVG (diagrammes)
                        document.querySelectorAll('svg').forEach((svg, i) => {
                            const rect = svg.getBoundingClientRect();
                            if (rect.width > 100 && rect.height > 50) {
                                elements.push({
                                    type: 'svg',
                                    index: i,
                                    width: rect.width,
                                    height: rect.height
                                });
                            }
                        });
                        
                        return elements;
                    }
                """)
                
                logger.info(f"Trouvé {len(visual_elements)} éléments visuels potentiels")
                
                # 3. Prendre des captures d'écran des éléments visuels intéressants
                for i, element in enumerate(visual_elements[:3]):  # Limiter à 3 éléments
                    try:
                        if element['type'] == 'image' and element.get('src'):
                            # Pour les images, essayer de les télécharger directement
                            try:
                                import httpx
                                async with httpx.AsyncClient() as client:
                                    img_response = await client.get(element['src'], timeout=10)
                                    if img_response.status_code == 200:
                                        image_data = img_response.content
                                        
                                        # Déterminer le format d'image
                                        content_type = img_response.headers.get('content-type', '')
                                        if 'jpeg' in content_type or 'jpg' in content_type:
                                            image_format = 'jpeg'
                                        elif 'png' in content_type:
                                            image_format = 'png'
                                        elif 'webp' in content_type:
                                            image_format = 'webp'
                                        else:
                                            image_format = 'jpeg'  # défaut
                                        
                                        # Appeler l'OCR Albert avec le nouveau format
                                        ocr_result = await albert_client.ocr_document(
                                            image_data=image_data,
                                            image_format=image_format,
                                            prompt=f"Extrais tout le texte visible dans cette image. Si c'est un graphique ou un tableau, décris également la structure et les données importantes."
                                        )
                                        
                                        if ocr_result.get("success"):
                                            result["images_ocr"].append({
                                                "type": element['type'],
                                                "source": element.get('src', ''),
                                                "ocr_text": ocr_result["extracted_text"],
                                                "model": ocr_result.get("model", "mistral-small-latest"),
                                                "confidence": ocr_result.get("confidence", "unknown")
                                            })
                                            logger.info(f"OCR réussi sur image {i+1}")
                                        else:
                                            logger.warning(f"OCR échoué sur image {i+1}: {ocr_result.get('error')}")
                                            
                            except Exception as e:
                                logger.warning(f"Erreur téléchargement image {element.get('src')}: {e}")
                        
                        elif element['type'] in ['canvas', 'svg']:
                            # Pour canvas/svg, prendre une capture d'écran de la zone
                            try:
                                selector = f"{element['type']}:nth-of-type({element['index'] + 1})"
                                element_handle = await page.query_selector(selector)
                                if element_handle:
                                    screenshot = await element_handle.screenshot()
                                    
                                    # OCR de la capture d'écran
                                    ocr_result = await albert_client.ocr_document(
                                        image_data=screenshot,
                                        image_format='png',
                                        prompt="Extrais tout le texte et décris les éléments visuels importants (graphiques, tableaux, diagrammes) dans cette capture d'écran."
                                    )
                                    
                                    if ocr_result.get("success"):
                                        result["images_ocr"].append({
                                            "type": element['type'],
                                            "source": f"screenshot_{element['type']}_{i}",
                                            "ocr_text": ocr_result["extracted_text"],
                                            "model": ocr_result.get("model", "mistral-small-latest"),
                                            "confidence": ocr_result.get("confidence", "unknown")
                                        })
                                        logger.info(f"OCR réussi sur {element['type']} {i+1}")
                                    else:
                                        logger.warning(f"OCR échoué sur {element['type']} {i+1}: {ocr_result.get('error')}")
                                        
                            except Exception as e:
                                logger.warning(f"Erreur capture {element['type']}: {e}")
                                
                    except Exception as e:
                        logger.warning(f"Erreur traitement élément {i}: {e}")
                        continue
                
                # 4. Combiner le contenu textuel et OCR
                if result["images_ocr"]:
                    ocr_texts = [item["ocr_text"] for item in result["images_ocr"] if item["ocr_text"].strip()]
                    if ocr_texts:
                        combined_ocr = "\n\n--- CONTENU EXTRAIT DES ÉLÉMENTS VISUELS ---\n\n" + "\n\n".join(ocr_texts)
                        result["content"] = result["content"] + "\n\n" + combined_ocr
                
                logger.info(f"Extraction hybride terminée - Contenu: {len(result['content'])} chars, OCR: {len(result['images_ocr'])} éléments")
                
            except Exception as e:
                logger.error(f"Erreur durant l'extraction hybride: {e}")
                result["success"] = False
                result["error"] = str(e)
                
            finally:
                await browser.close()
        
        return result
        
    except Exception as e:
        logger.error(f"Erreur extraction hybride LLM+OCR: {e}")
        return {
            "url": url,
            "title": "",
            "content": f"Erreur extraction hybride: {str(e)}",
            "images_ocr": [],
            "method": "hybrid_llm_ocr",
            "success": False,
            "error": str(e)
        }