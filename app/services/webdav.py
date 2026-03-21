import os
from typing import List, Optional, Dict, Tuple, Union, Any
from dataclasses import dataclass
import requests
from urllib.parse import urljoin
import io
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams, LTTextContainer, LTChar, LTPage
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.converter import PDFPageAggregator
import xml.etree.ElementTree as ET
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import time
import json
from datetime import datetime
import asyncio
from pathlib import Path
import httpx
import urllib.parse
import unicodedata

from app.config import Config
from app.matrix_bot.config import logger

# Définition d'une fonction locale pour éviter l'importation circulaire
def get_current_time() -> datetime:
    """Fournit un timestamp pour les opérations WebDAV"""
    return datetime.now()

@dataclass
class WebDAVDocument:
    id: str
    path: str
    content: str
    metadata: dict
    score: float = 0.0

@dataclass
class DocumentVersion:
    version: int
    file_name: str
    path: str
    created_at: datetime
    metadata: Dict = None

class WebDAVService:
    # Types MIME supportés
    SUPPORTED_MIME_TYPES = {
        'application/pdf': 'pdf',
        'text/plain': 'text',
        'text/markdown': 'text',
        'application/json': 'text',
        'text/html': 'text',
        'application/xml': 'text',
        'application/octet-stream': 'binary',  # Ajout du support pour les fichiers binaires
        'text/x-markdown': 'text',  # Ajout du support explicite pour Markdown
        'text/markdown': 'text'     # Autre variante du type MIME Markdown
    }
    
    # Dossiers système (chemins relatifs)
    SYSTEM_ROOT = ".albert"
    CONTEXTS_DIR = f"{SYSTEM_ROOT}/contexts"
    VERSIONS_DIR = f"{SYSTEM_ROOT}/versions"
    INDEX_DIR = f"{SYSTEM_ROOT}/index"
    CONFIG_DIR = f"{SYSTEM_ROOT}/config"
    BEHAVIOR_DIR = f"{SYSTEM_ROOT}/behavior"  # Ajout du dossier behavior
    
    def __init__(self, 
                 config: Config = None, 
                 webdav_url: str = None, 
                 webdav_username: str = None, 
                 webdav_password: str = None,
                 webdav_root: str = None):
        """
        Initialise le service WebDAV avec soit:
        - Une configuration complète (Config)
        - Des paramètres individuels (url, username, password, root)
        
        Args:
            config: Configuration complète (peut être None si les autres paramètres sont fournis)
            webdav_url: URL du serveur WebDAV (prioritaire sur config si fourni)
            webdav_username: Nom d'utilisateur (prioritaire sur config si fourni)
            webdav_password: Mot de passe (prioritaire sur config si fourni)
            webdav_root: Chemin racine à utiliser (prioritaire sur config si fourni)
        """
        # Récupérer les valeurs de configuration, en priorité des paramètres directs
        self.config = config
        self.base_url = webdav_url or (config.webdav_url if config else None)
        self.webdav_username = webdav_username or (config.webdav_username if config else None)
        self.webdav_password = webdav_password or (config.webdav_password if config else None)
        webdav_root_path = webdav_root or (config.webdav_root_path if config else "")

        # Vérifier que nous avons au moins l'URL, le nom d'utilisateur et le mot de passe
        if not self.base_url or not self.webdav_username or not self.webdav_password:
            raise ValueError("Les paramètres WebDAV sont insuffisants. L'URL, le nom d'utilisateur et le mot de passe sont requis.")

        # Normaliser : base_url sans slash final, root_path sans slash initial ni final
        self.base_url = self.base_url.rstrip('/')
        webdav_root_path = webdav_root_path.strip('/')

        # Éviter la duplication si root_path est déjà le dernier segment de base_url
        if webdav_root_path and self.base_url.endswith('/' + webdav_root_path):
            self.base_url = self.base_url[:-len(webdav_root_path)].rstrip('/')

        self.versions: Dict[str, List[DocumentVersion]] = {}
        self._initialized = False  # Ajout de l'attribut d'initialisation

        # Construire les chemins complets
        self.root_path = webdav_root_path
        self.system_root = os.path.join(self.root_path, self.SYSTEM_ROOT)
        self.contexts_path = os.path.join(self.root_path, self.CONTEXTS_DIR)
        self.versions_path = os.path.join(self.root_path, self.VERSIONS_DIR)
        self.index_path = os.path.join(self.root_path, self.INDEX_DIR)
        self.config_path = os.path.join(self.root_path, self.CONFIG_DIR)

        # Configuration du client HTTP
        self.http_client = self._build_http_client()
        logger.info("Client WebDAV initialisé")
        # Ne pas lancer la tâche ici, elle sera lancée dans initialize()

    def _build_http_client(self) -> httpx.AsyncClient:
        """Construit le client HTTP avec Basic auth."""
        return httpx.AsyncClient(
            auth=(self.webdav_username, self.webdav_password),
            timeout=60.0,
            headers={'User-Agent': 'AlbertTchap/1.0', 'Accept': '*/*'},
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            http2=True,
            verify=True,
        )

    async def init_webdav(self) -> None:
        """Initialise le service WebDAV ou réinitialise la connexion si déjà initialisé"""
        try:
            # Fermer l'ancienne connexion si elle existe
            if hasattr(self, 'http_client') and self.http_client:
                try:
                    await self.http_client.aclose()
                except Exception:
                    pass  # Ignorer les erreurs de fermeture
            
            # Recréer un nouveau client HTTP si nécessaire
            self.http_client = self._build_http_client()
            logger.info("Client WebDAV réinitialisé")
            
            # Vérifier la configuration
            if not self.base_url:
                raise ValueError("Configuration WebDAV manquante ou invalide")
                
            # Vérifier la connectivité au serveur WebDAV avant de continuer
            test_url = f"{self.base_url.rstrip('/')}"
            try:
                response = await self.http_client.get(test_url, timeout=10.0)
                if response.status_code >= 400:
                    logger.warning(f"Avertissement: Le serveur WebDAV a répondu avec le code {response.status_code}")
            except Exception as conn_err:
                logger.error(f"Erreur de connexion au serveur WebDAV: {str(conn_err)}")
                # Continuer malgré l'erreur, pour permettre le fonctionnement en mode dégradé
                
            # Créer les dossiers système s'ils n'existent pas
            system_paths = [
                self.system_root,
                self.contexts_path,
                self.versions_path,
                self.index_path,
                self.config_path
            ]
            
            for path in system_paths:
                try:
                    if not await self.exists(path):
                        success = await self.create_directory(path)
                        if not success:
                            logger.warning(f"Impossible de créer le dossier {path}, mais on continue")
                except Exception as e:
                    logger.warning(f"Erreur lors de la création du dossier {path}: {str(e)}")
                    # Continuer malgré l'erreur
                        
            # Charger les versions
            try:
                await self._load_versions()
            except Exception as e:
                logger.warning(f"Erreur lors du chargement des versions: {str(e)}")
                # Continuer malgré l'erreur
                
            self._initialized = True
            logger.info("Service WebDAV initialisé avec succès")
            
        except Exception as e:
            logger.error(f"Erreur initialisation WebDAV: {str(e)}")
            if self.http_client:
                await self.http_client.aclose()
            # Même en cas d'erreur, marquer comme initialisé pour éviter les blocages
            self._initialized = True
            logger.warning("Service WebDAV initialisé en mode dégradé")
            
    # Méthode de compatibilité avec l'ancien code
    async def initialize(self) -> None:
        """
        Méthode de compatibilité avec l'ancien code.
        
        Cette méthode redirige vers init_webdav pour maintenir la compatibilité
        avec le code existant qui appelle initialize().
        """
        return await self.init_webdav()

    async def close(self):
        """Ferme proprement le client HTTP"""
        try:
            if self.http_client:
                await self.http_client.aclose()
                logger.info("Session HTTP fermée avec succès")
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture du client HTTP: {str(e)}")
            raise

    async def reinitialize_client(self):
        """Réinitialise le client HTTP s'il a été fermé"""
        try:
            # Vérifier si le client est fermé ou n'existe pas
            if not hasattr(self, 'http_client') or self.http_client is None or self.http_client.is_closed:
                # Fermer l'ancien client si nécessaire
                if hasattr(self, 'http_client') and self.http_client:
                    try:
                        await self.http_client.aclose()
                    except Exception:
                        pass  # Ignorer les erreurs de fermeture
                
                # Créer un nouveau client
                self.http_client = httpx.AsyncClient(
                    auth=(self.webdav_username, self.webdav_password),
                    timeout=60.0,
                    headers={
                        'User-Agent': 'AlbertTchap/1.0',
                        'Accept': '*/*'
                    },
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                    http2=True,
                    verify=True  # Vérification SSL
                )
                logger.info("Client WebDAV réinitialisé après fermeture")
                return True
            return False  # Client déjà ouvert
        except Exception as e:
            logger.error(f"Erreur lors de la réinitialisation du client HTTP: {str(e)}")
            raise

    def _normalize_path(self, path: str) -> str:
        """Normalise un chemin WebDAV"""
        # Nettoyer le chemin
        path = path.strip('/')
        
        # Ajouter le chemin racine si nécessaire
        if self.root_path and not path.startswith(self.root_path.strip('/')):
            path = f"{self.root_path.strip('/')}/{path}"
        
        return path

    def _get_url(self, path: str) -> str:
        """Transforme un chemin relatif en URL complète du serveur WebDAV.
        
        Args:
            path: Chemin relatif à la racine WebDAV
            
        Returns:
            URL complète
        """
        # Normaliser le chemin pour éviter les problèmes de slash
        path = path.strip()
        if not path:
            path = self.root_path
        elif not path.startswith('/'):
            path = f'/{path}'
            
        # Construire le chemin complet en ajoutant le chemin de base
        if self.root_path and self.root_path != '/':
            # Comparer sans le slash initial (path a été normalisé avec un '/' en tête)
            path_stripped = path.lstrip('/')
            if not path_stripped.startswith(self.root_path):
                # Si le chemin ne commence pas déjà par le chemin racine, l'ajouter
                if path == '/':
                    full_path = self.root_path
                else:
                    full_path = f"{self.root_path}{path}"
            else:
                # Si le chemin commence déjà par le chemin racine, ne pas l'ajouter à nouveau
                full_path = path
        else:
            full_path = path

        # Encoder l'URL en plusieurs étapes pour éviter le double-encodage et gérer tous les caractères spéciaux
        
        # 1. Si le chemin contient des caractères déjà URL-encodés (comme %21), les décoder d'abord
        decoded_path = urllib.parse.unquote(full_path)
        
        # 2. Normaliser la forme Unicode pour garantir la cohérence des caractères spéciaux
        normalized_path = unicodedata.normalize('NFC', decoded_path)
        if normalized_path != decoded_path:
            logger.debug(f"[GET_URL] Normalisation Unicode: '{decoded_path}' -> '{normalized_path}'")
        
        # 3. Ensuite, encoder correctement le chemin en préservant la structure
        # Supprimer le slash initial pour le traitement par segments
        path_without_leading_slash = normalized_path[1:] if normalized_path.startswith('/') else normalized_path
        
        # Diviser le chemin en segments
        segments = path_without_leading_slash.split('/')
        
        # 4. Encoder chaque segment individuellement pour préserver la structure du chemin
        encoded_segments = []
        for segment in segments:
            # Ne pas encoder à nouveau si le segment semble déjà encodé
            if "%" in segment and all(c in "0123456789ABCDEFabcdef%" for c in segment if c not in "0123456789ABCDEFabcdef%"):
                # Cette partie semble déjà encodée correctement
                encoded_segments.append(segment)
                logger.debug(f"[GET_URL] Segment déjà encodé préservé: '{segment}'")
            else:
                # Pour les fichiers de contexte (dans le dossier contexts), préserver les caractères spéciaux
                if '.albert/contexts' in full_path:
                    # Garder certains caractères spéciaux tels quels pour les fichiers de contexte
                    encoded_segments.append(urllib.parse.quote(segment, safe='!@:'))
                else:
                    # Encoder normalement pour les autres fichiers, sans caractères safe
                    encoded_segments.append(urllib.parse.quote(segment, safe=''))
        
        # Reconstruire le chemin avec les segments encodés
        encoded_path = '/'.join(encoded_segments)
        
        # Construire l'URL complète
        url = f"{self.base_url}/{encoded_path}"
        
        return url

    async def write_file(self, path: str, content: Union[str, bytes]) -> None:
        """Écrit le contenu dans un fichier WebDAV"""
        try:
            # Vérifier et réinitialiser le client si nécessaire
            if hasattr(self.http_client, 'is_closed') and self.http_client.is_closed:
                await self.reinitialize_client()
                
            logger.info(f"Écriture du fichier WebDAV: {path}")
            url = self._get_url(path)
            
            # Convertir en bytes si nécessaire
            if isinstance(content, str):
                content = content.encode('utf-8')
                
            response = await self.http_client.put(url, content=content)
            response.raise_for_status()
            logger.info(f"Fichier écrit avec succès: {path} ({len(content)} octets)")
        except Exception as e:
            logger.error(f"Erreur lors de l'écriture du fichier WebDAV {path}: {str(e)}")
            raise

    async def read_document(self, path: str, max_retries: int = 3) -> str:
        """Lit le contenu d'un document avec retry"""
        # Vérifier et réinitialiser le client si nécessaire
        if hasattr(self.http_client, 'is_closed') and self.http_client.is_closed:
            await self.reinitialize_client()
            
        url = self._get_url(path)
        
        for attempt in range(max_retries):
            try:
                response = await self.http_client.get(url)
                
                # Vérifier spécifiquement les erreurs 404 avant de lever une exception
                if response.status_code == 404:
                    logger.warning(f"Fichier non trouvé: {path}")
                    raise FileNotFoundError(f"Fichier non trouvé: {path}")
                
                response.raise_for_status()
                
                # Détecter le type de contenu
                content_type = response.headers.get('content-type', '').lower()
                
                # Traiter selon le type MIME
                if 'application/pdf' in content_type:
                    # Convertir le PDF en texte structuré
                    pdf_content = io.BytesIO(response.content)
                    
                    # Paramètres d'extraction améliorés
                    laparams = LAParams(
                        line_margin=0.3,      # Réduit pour mieux détecter les lignes
                        word_margin=0.1,      # Marge entre les mots
                        char_margin=1.0,      # Réduit pour mieux grouper les caractères
                        boxes_flow=0.7,       # Augmenté pour mieux suivre le flux de texte
                        detect_vertical=True,  # Détection du texte vertical
                        all_texts=True        # Capture tout le texte, même décoratif
                    )
                    
                    # Initialiser les outils PDFMiner
                    resource_manager = PDFResourceManager()
                    device = PDFPageAggregator(resource_manager, laparams=laparams)
                    interpreter = PDFPageInterpreter(resource_manager, device)
                    
                    structured_text = []
                    current_page = 1
                    
                    # Extraire le texte page par page
                    for page in PDFPage.get_pages(pdf_content):
                        interpreter.process_page(page)
                        layout = device.get_result()
                        
                        page_text = []
                        for element in layout:
                            if isinstance(element, LTTextContainer):
                                # Extraire le texte avec sa position et ses propriétés
                                text = element.get_text().strip()
                                if text:
                                    # Détecter si c'est un titre basé sur la taille de police
                                    font_sizes = []
                                    for text_line in element:
                                        if isinstance(text_line, LTTextContainer):
                                            for char in text_line:
                                                if isinstance(char, LTChar):
                                                    font_sizes.append(char.size)
                                    
                                    avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 0
                                    
                                    # Ajouter des marqueurs de structure
                                    if avg_font_size > 14:  # Titre probable
                                        text = f"### {text}"
                                    elif avg_font_size > 12:  # Sous-titre probable
                                        text = f"## {text}"
                                    elif avg_font_size > 10:  # Sous-section probable
                                        text = f"# {text}"
                                        
                                    # Ajouter le texte avec sa position
                                    bbox = element.bbox
                                    if bbox:
                                        text = f"{text} [pos: {bbox[0]:.0f}, {bbox[1]:.0f}]"
                                    
                                    page_text.append(text)
                        
                        # Ajouter le numéro de page et le texte
                        if page_text:
                            structured_text.append(f"\n[Page {current_page}]\n")
                            structured_text.extend(page_text)
                            current_page += 1
                    
                    device.close()
                    return "\n".join(structured_text)
                else:
                    # Pour les autres types, retourner le contenu comme texte
                    return response.text
                    
            except httpx.HTTPStatusError as e:
                # Traiter spécifiquement les erreurs 404
                if e.response.status_code == 404:
                    # Propager immédiatement les erreurs 404 sans retry
                    logger.warning(f"Fichier non trouvé: {path}")
                    raise FileNotFoundError(f"Fichier non trouvé: {path}") from e
                
                if attempt < max_retries - 1:
                    logger.warning(f"Tentative {attempt + 1}/{max_retries} échouée pour {path}: {str(e)}")
                    await asyncio.sleep(2 ** attempt)  # Backoff exponentiel
                else:
                    logger.error(f"Échec définitif de lecture du document {path} après {max_retries} tentatives: {str(e)}")
                    raise
            except Exception as e:
                # Autres erreurs
                if attempt < max_retries - 1:
                    logger.warning(f"Tentative {attempt + 1}/{max_retries} échouée pour {path}: {str(e)}")
                    await asyncio.sleep(2 ** attempt)  # Backoff exponentiel
                else:
                    logger.error(f"Échec définitif de lecture du document {path} après {max_retries} tentatives: {str(e)}")
                    raise

    def _href_to_relative_path(self, href: str) -> str:
        """Convertit un href absolu de réponse PROPFIND en chemin relatif à la racine WebDAV."""
        import urllib.parse as _urlparse
        # Cas Nextcloud/ownCloud
        if 'remote.php/dav/files/' in href:
            path = href.split('remote.php/dav/files/')[-1].split('/', 1)[-1]
        else:
            # WebDAV standard : extraire le chemin relatif en supprimant le préfixe base_url
            base_path = _urlparse.urlparse(self.base_url).path.rstrip('/')
            # Le préfixe attendu dans les hrefs : /dav/Archivist/
            full_prefix = base_path + '/' + self.root_path + '/'
            decoded = _urlparse.unquote(href)
            if decoded.startswith(full_prefix):
                path = decoded[len(full_prefix):]
            elif decoded.startswith(base_path + '/'):
                path = decoded[len(base_path) + 1:].lstrip('/')
                if path.startswith(self.root_path + '/'):
                    path = path[len(self.root_path) + 1:]
            else:
                path = decoded.lstrip('/')
        return path.lstrip('/')

    async def list_directory(self, path: str = None) -> List[Dict[str, Any]]:
        """Liste tous les fichiers et répertoires dans un chemin WebDAV
        
        Args:
            path: Chemin du répertoire à lister (relatif à la racine)
            
        Returns:
            List[Dict]: Liste des fichiers et répertoires, chacun avec les attributs:
                - name: Nom du fichier/dossier
                - path: Chemin complet
                - type: 'directory' ou 'file'
                - size: Taille en octets (pour les fichiers)
                - modified: Date de dernière modification
        """
        try:
            # Vérifier et réinitialiser le client si nécessaire
            if hasattr(self.http_client, 'is_closed') and self.http_client.is_closed:
                await self.reinitialize_client()
                
            url = self._get_url(path or self.root_path)
            response = await self.http_client.request(
                "PROPFIND",
                url,
                headers={"Depth": "1"}  # 1 pour obtenir seulement les éléments directs
            )
            response.raise_for_status()
            
            # Parser la réponse XML
            root = ET.fromstring(response.content)
            
            # Extraire les informations des fichiers et dossiers
            items = []
            for response_elem in root.findall('.//{DAV:}response'):
                href = response_elem.find('.//{DAV:}href').text
                if not href:
                    continue
                
                # Ignorer l'élément courant (le répertoire lui-même)
                if href.rstrip('/') == url.rstrip('/'):
                    continue
                
                # Déterminer si c'est un dossier
                is_directory = href.endswith('/')
                item_type = 'directory' if is_directory else 'file'
                
                # Extraire le chemin relatif
                item_path = self._href_to_relative_path(href)
                
                # Extraire le nom
                name = os.path.basename(item_path.rstrip('/'))
                
                # Créer l'objet avec les informations de base
                item_info = {
                    'name': name,
                    'path': item_path,
                    'type': item_type
                }
                
                # Extraire d'autres propriétés si disponibles
                try:
                    size_elem = response_elem.find('.//{DAV:}getcontentlength')
                    if size_elem is not None and size_elem.text:
                        item_info['size'] = int(size_elem.text)
                    
                    modified_elem = response_elem.find('.//{DAV:}getlastmodified')
                    if modified_elem is not None and modified_elem.text:
                        item_info['modified'] = modified_elem.text
                except Exception as prop_err:
                    logger.warning(f"Erreur extraction propriétés: {prop_err}")
                
                items.append(item_info)
            
            return items
            
        except Exception as e:
            logger.error(f"Erreur lors de la liste du répertoire {path}: {str(e)}")
            return []  # Retourner une liste vide en cas d'erreur au lieu de lever une exception

    async def list_documents(self, path: str = None, pattern: str = None) -> List[str]:
        """Liste tous les documents dans le répertoire WebDAV
        
        Args:
            path: Chemin du répertoire à lister (relatif à la racine)
            pattern: Motif de filtrage (glob pattern) - Actuellement ignoré
            
        Returns:
            Liste des chemins de documents
        """
        try:
            # Vérifier et réinitialiser le client si nécessaire
            if hasattr(self.http_client, 'is_closed') and self.http_client.is_closed:
                await self.reinitialize_client()
                
            url = self._get_url(path or self.root_path)
            response = await self.http_client.request(
                "PROPFIND",
                url,
                headers={"Depth": "infinity"}
            )
            response.raise_for_status()
            
            # Parser la réponse XML
            root = ET.fromstring(response.content)
            
            # Extraire les chemins des documents
            documents = []
            for response_elem in root.findall('.//{DAV:}response'):
                href = response_elem.find('.//{DAV:}href').text
                if href and not href.endswith('/'):  # Ignorer les répertoires
                    path = self._href_to_relative_path(href)
                    documents.append(path)
            
            # Note: le paramètre pattern est actuellement ignoré
            # TODO: Implémenter le filtrage par motif si nécessaire
            
            return documents
            
        except Exception as e:
            logger.error(f"Erreur lors de la liste des documents: {str(e)}")
            raise

    async def _load_versions(self) -> None:
        """Charge l'historique des versions depuis le fichier versions.json"""
        try:
            versions_path = os.path.join(self.versions_path, "versions.json")
            if not await self.exists(versions_path):
                self.versions = {
                    "last_update": get_current_time().isoformat(),
                    "documents": {}
                }
                await self._save_versions()
                logger.info("Nouveau fichier de versions créé")
                return

            content = await self.read_document(versions_path)
            if not content.strip():
                self.versions = {
                    "last_update": get_current_time().isoformat(),
                    "documents": {}
                }
                await self._save_versions()
                logger.info("Fichier de versions vide, initialisation effectuée")
                return

            data = json.loads(content)
            if not isinstance(data, dict) or "documents" not in data:
                raise ValueError("Format de fichier de versions invalide")

            self.versions = data
            logger.debug("Historique des versions chargé avec succès")

        except (json.JSONDecodeError, ValueError) as e:
            logger.warning(f"Erreur lors du chargement de l'historique des versions: {str(e)}")
            self.versions = {
                "last_update": get_current_time().isoformat(),
                "documents": {}
            }
            await self._save_versions()

    async def _save_versions(self):
        """Sauvegarde l'historique des versions"""
        try:
            # Convertir les versions en format sérialisable
            versions_data = {
                "last_update": get_current_time().isoformat(),
                "documents": {}
            }
            
            # Convertir chaque version en dictionnaire
            for doc_path, doc_versions in self.versions.get("documents", {}).items():
                if isinstance(doc_versions, list):
                    versions_data["documents"][doc_path] = []
                    for version in doc_versions:
                        if isinstance(version, DocumentVersion):
                            version_dict = {
                                'version': version.version,
                                'path': version.path,
                                'created_at': version.created_at.isoformat(),
                                'metadata': version.metadata or {}
                            }
                        else:
                            # Si c'est déjà un dictionnaire
                            version_dict = {
                                'version': version.get('version', 1),
                                'path': version.get('path', ''),
                                'created_at': version.get('created_at', get_current_time().isoformat()),
                                'metadata': version.get('metadata', {})
                            }
                        versions_data["documents"][doc_path].append(version_dict)
            
            # Sauvegarder dans le fichier versions.json
            versions_path = os.path.join(self.versions_path, "versions.json")
            await self.write_file(versions_path, json.dumps(versions_data, indent=2))
            logger.debug("Versions sauvegardées avec succès")
            
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde des versions: {str(e)}")
            raise

    def _get_version_path(self, original_path: str, version: int) -> str:
        """Génère le chemin pour une version spécifique"""
        dirname = os.path.dirname(original_path)
        basename = os.path.basename(original_path)
        return os.path.join(dirname, f".versions", f"{basename}.v{version}")

    async def get_versions(self, path: str) -> List[DocumentVersion]:
        """Retourne l'historique des versions d'un document"""
        return self.versions.get(path, [])

    async def get_version(self, path: str, version: int) -> Tuple[str, DocumentVersion]:
        """Récupère une version spécifique d'un document"""
        versions = await self.get_versions(path)
        if not versions or version > len(versions):
            raise ValueError(f"Version {version} non trouvée pour {path}")
            
        version_info = versions[version - 1]
        if version == len(versions):
            # Version courante
            content = await self.read_document(path)
        else:
            # Version archivée
            version_path = self._get_version_path(path, version)
            content = await self.read_document(version_path)
            
        return content, version_info

    async def create_directory(self, path: str) -> bool:
        """Crée un répertoire sur le serveur WebDAV
        
        Args:
            path: Chemin du répertoire à créer
            
        Returns:
            bool: True si la création a réussi, False sinon
        """
        try:
            # Vérifier et réinitialiser le client si nécessaire
            if hasattr(self.http_client, 'is_closed') and self.http_client.is_closed:
                await self.reinitialize_client()
                
            # Vérifier si le chemin existe déjà
            exists = await self.exists(path)
            if exists:
                logger.info(f"Dossier déjà existant: {path}")
                return True
                
            # Normaliser le chemin
            if path.endswith('/'):
                path = path[:-1]
                
            # Vérifier si le chemin parent existe et le créer si nécessaire
            parent_path = os.path.dirname(path)
            if parent_path and parent_path != '/':
                parent_exists = await self.exists(parent_path)
                if not parent_exists:
                    parent_created = await self.create_directory(parent_path)
                    if not parent_created:
                        logger.error(f"Échec création dossier parent: {parent_path}")
                        return False
                        
            # Créer le dossier
            url = self._get_url(path)
            try:
                response = await self.http_client.request("MKCOL", url)
                
                # Si statut 201 Created ou 200 OK, c'est un succès
                if response.status_code in [200, 201]:
                    logger.info(f"Dossier créé: {path}")
                    return True
                    
                # Si statut 409 Conflict, vérifier si le dossier existe déjà
                elif response.status_code == 409:
                    # Vérifier si le dossier existe malgré le conflit
                    exists_after_conflict = await self.exists(path)
                    if exists_after_conflict:
                        logger.info(f"Dossier existe malgré conflit: {path}")
                        return True
                    else:
                        logger.error(f"Échec création dossier: {path} (status=409, conflit sans existence)")
                        return False
                else:
                    logger.error(f"Échec création dossier: {path} (status={response.status_code})")
                    return False
                    
            except Exception as e:
                logger.error(f"Erreur lors de la création du dossier {path}: {str(e)}")
                return False
                
        except Exception as e:
            logger.error(f"Erreur générale lors de la création du dossier {path}: {str(e)}")
            return False

    def _validate_filename(self, filename: str) -> bool:
        """Valide un nom de fichier selon les règles WebDAV et de sécurité"""
        # Caractères interdits dans les noms de fichiers
        FORBIDDEN_CHARS = '<>:"/\\|?*\x00-\x1F'
        # Noms de fichiers réservés Windows
        RESERVED_NAMES = {
            'CON', 'PRN', 'AUX', 'NUL',
            'COM1', 'COM2', 'COM3', 'COM4',
            'LPT1', 'LPT2', 'LPT3', 'LPT4'
        }
        
        try:
            # Vérification de base
            if not filename or filename.isspace():
                logger.warning("Nom de fichier vide")
                return False
                
            # Vérification de la longueur
            if len(filename) > 255:
                logger.warning("Nom de fichier trop long")
                return False
                
            # Vérification des caractères interdits
            if any(c in FORBIDDEN_CHARS for c in filename):
                logger.warning("Caractères interdits dans le nom de fichier")
                return False
                
            # Vérification des noms réservés
            name_without_ext = os.path.splitext(filename)[0].upper()
            if name_without_ext in RESERVED_NAMES:
                logger.warning("Nom de fichier réservé")
                return False
                
            # Vérification des espaces en début/fin
            if filename != filename.strip():
                logger.warning("Espaces en début/fin de nom de fichier")
                return False
                
            # Vérification des points
            if filename.startswith('.') and not any(filename.startswith(valid) for valid in ['.git', '.env', '.index']):
                logger.warning("Nom de fichier commençant par un point non autorisé")
                return False
                
            return True
            
        except Exception as e:
            logger.error(f"Erreur lors de la validation du nom de fichier: {str(e)}")
            return False

    def _validate_path(self, path: str) -> bool:
        """Valide un chemin complet"""
        try:
            # Normalisation du chemin
            path = self._get_url(path)
            
            # Validation de chaque composant du chemin
            parts = Path(path).parts
            return all(self._validate_filename(part) for part in parts)
            
        except Exception as e:
            logger.error(f"Erreur lors de la validation du chemin: {str(e)}")
            return False

    async def exists(self, path: str) -> bool:
        """Vérifie si un fichier ou dossier existe sur le serveur WebDAV
        
        Args:
            path: Chemin à vérifier
            
        Returns:
            bool: True si le chemin existe
        """
        try:
            # Vérifier et réinitialiser le client si nécessaire
            if hasattr(self.http_client, 'is_closed') and self.http_client.is_closed:
                await self.reinitialize_client()
                
            url = self._get_url(path)
            response = await self.http_client.request(
                "PROPFIND",
                url,
                headers={"Depth": "0"}
            )
            return response.status_code in [200, 207]
        except Exception as e:
            logger.debug(f"Erreur vérification existence {path}: {str(e)}")
            return False

    async def download_file(self, path: str) -> bytes:
        """Télécharge un fichier depuis WebDAV
        
        Args:
            path: Chemin du fichier à télécharger
            
        Returns:
            bytes: Contenu du fichier
        """
        try:
            url = self._get_url(path)
            response = await self.http_client.get(url)
            response.raise_for_status()
            return response.content
        except Exception as e:
            logger.error(f"Erreur téléchargement fichier {path}: {str(e)}")
            raise

    async def upload_file(self, file_obj, path: str) -> bool:
        """Téléverse un fichier vers le serveur WebDAV
        
        Args:
            file_obj: Objet fichier ouvert en mode binaire ou chemin vers un fichier local
            path: Chemin de destination sur le serveur WebDAV
            
        Returns:
            bool: True si le téléversement a réussi
        """
        try:
            # Normaliser le chemin
            path = path.strip()
            if not path.startswith('/'):
                path = '/' + path
            
            # Vérifier et créer le dossier parent si nécessaire
            parent_dir = os.path.dirname(path)
            if parent_dir and parent_dir != '/':
                parent_exists = await self.exists(parent_dir)
                if not parent_exists:
                    logger.info(f"Création du dossier parent pour le téléversement: {parent_dir}")
                    parent_created = await self.create_directory(parent_dir)
                    if not parent_created:
                        logger.error(f"Impossible de créer le dossier parent {parent_dir}")
                        return False
                    
                # Double vérification que le dossier parent existe
                parent_exists = await self.exists(parent_dir)
                if not parent_exists:
                    logger.error(f"Le dossier parent {parent_dir} n'existe pas, impossible de téléverser le fichier")
                    return False
            
            # Construire l'URL pour le téléversement
            url = self._get_url(path)
            
            # Gestion de différents types d'entrée de fichier
            if isinstance(file_obj, (str, Path)):
                # Si c'est un chemin de fichier local
                with open(file_obj, 'rb') as f:
                    content = f.read()
            elif hasattr(file_obj, 'read'):
                # Si c'est un objet fichier déjà ouvert
                content = file_obj.read()
                # Retourner au début du fichier si possible
                if hasattr(file_obj, 'seek'):
                    file_obj.seek(0)
            elif isinstance(file_obj, bytes):
                # Si c'est déjà un contenu binaire
                content = file_obj
            else:
                raise ValueError(f"Type de fichier non pris en charge: {type(file_obj)}")
                
            # Téléverser le fichier avec PUT
            response = await self.http_client.put(url, content=content)
            
            # Vérifier le statut de la réponse
            if response.status_code in [200, 201, 204]:
                logger.info(f"Fichier téléversé avec succès: {path} ({len(content)} octets)")
                return True
            else:
                logger.error(f"Échec du téléversement: {path} (status={response.status_code})")
                response.raise_for_status()  # Lever l'exception pour plus de détails
                return False
            
        except Exception as e:
            logger.error(f"Erreur lors du téléversement du fichier {path}: {str(e)}")
            raise

    async def get_context_safely(self, context_id: str) -> Optional[Dict[str, Any]]:
        """Récupère un contexte de manière sécurisée avec gestion des erreurs"""
        try:
            # Utiliser directement le chemin complet fourni
            context_path = context_id  # Le chemin complet est maintenant passé directement
            
            try:
                # Lire le document
                content = await self.read_document(context_path)
                
                if not content.strip():
                    logger.warning(f"Contexte {context_path} vide")
                    return None
                
                # Parser le JSON
                try:
                    data = json.loads(content)
                    if not isinstance(data, dict):
                        logger.error(f"Format invalide pour le contexte {context_path}")
                        return None
                    return data
                except json.JSONDecodeError as e:
                    logger.error(f"Erreur décodage JSON pour {context_path}: {str(e)}")
                    return None
                    
            except FileNotFoundError:
                logger.debug(f"Contexte {context_path} non trouvé")
                return None
            except Exception as e:
                # Autres erreurs
                logger.error(f"Erreur lecture contenu contexte {context_path}: {str(e)}")
                return None
            
        except Exception as e:
            logger.error(f"Erreur lecture contexte {context_path}: {str(e)}")
            return None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Ferme proprement les ressources"""
        try:
            if self.http_client:
                await self.http_client.aclose()
                logger.info("Session HTTP fermée avec succès")
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture de la session HTTP: {str(e)}")

    async def delete_file(self, path: str) -> bool:
        """Supprime un fichier sur le serveur WebDAV
        
        Args:
            path: Chemin du fichier à supprimer (relatif à la racine)
            
        Returns:
            bool: True si la suppression a réussi, False sinon
        """
        try:
            logger.info(f"Suppression du fichier WebDAV: {path}")
            url = self._get_url(path)
            
            response = await self.http_client.request("DELETE", url)
            
            if response.status_code in [200, 204]:
                logger.info(f"Fichier supprimé avec succès: {path}")
                return True
            else:
                logger.warning(f"Échec de la suppression du fichier: {path} (status={response.status_code})")
                return False
                
        except Exception as e:
            logger.error(f"Erreur lors de la suppression du fichier WebDAV {path}: {str(e)}")
            return False

    async def create_share_link(self, path: str, password: Optional[str] = None, 
                              expiration_days: Optional[int] = None, 
                              target_user: Optional[str] = None) -> str:
        """
        Crée un lien de partage pour un fichier ou dossier via l'API OCS de Nextcloud.
        
        Stratégie : Toujours créer un lien public (shareType: 3) avec expiration courte
        
        Args:
            path: Chemin relatif du fichier ou dossier
            password: Mot de passe optionnel pour protéger le partage
            expiration_days: Nombre de jours avant expiration du lien
            target_user: Ignoré, mais conservé pour compatibilité
            
        Returns:
            URL du lien de partage ou chaîne vide en cas d'erreur
        """
        try:
            logger.info(f"[OCS_SHARE] Tentative de création d'un lien pour: {path}")
            
            # Validation initiale
            if not path or path.strip() == "":
                logger.error("[OCS_SHARE] Chemin vide fourni")
                return ""
            
            # Extraire la base de l'URL Nextcloud (sans remote.php/dav/files/user)
            base_url = self.base_url
            if "/remote.php/dav/files/" in base_url:
                base_url = base_url.split("/remote.php/dav/files/")[0]
            elif "/remote.php/webdav/" in base_url:
                base_url = base_url.split("/remote.php/webdav/")[0]
            
            # S'assurer que la base URL n'a pas de slash final
            base_url = base_url.rstrip('/')
            
            logger.info(f"[OCS_SHARE] Base URL extraite: {base_url}")
            logger.info(f"[OCS_SHARE] URL WebDAV originale: {self.base_url}")
            
            # Construire l'URL de l'API OCS selon les spécifications officielles
            ocs_url = f"{base_url}/ocs/v2.php/apps/files_sharing/api/v1/shares"
            logger.info(f"[OCS_SHARE] URL OCS API: {ocs_url}")
            
            # Test de connectivité OCS avant de continuer
            try:
                test_response = await self.http_client.get(
                    f"{base_url}/ocs/v2.php/cloud/capabilities",
                    headers={"OCS-APIRequest": "true"}
                )
                logger.info(f"[OCS_SHARE] Test connectivité OCS: status={test_response.status_code}")
                if test_response.status_code != 200:
                    logger.warning(f"[OCS_SHARE] API OCS potentiellement inaccessible: {test_response.status_code}")
            except Exception as test_error:
                logger.warning(f"[OCS_SHARE] Impossible de tester l'API OCS: {str(test_error)}")
            
            # Récupérer le nom de l'espace de travail (workspace)
            workspace_name = "dossiers-colaig-assistant"  # Valeur par défaut
            
            # Tenter de récupérer l'espace de travail depuis les attributs du service
            if hasattr(self, 'workspace_path') and self.workspace_path:
                workspace_name = self.workspace_path
                logger.info(f"[OCS_SHARE] Espace de travail trouvé dans le service: {workspace_name}")
            # Sinon, essayer de le récupérer depuis la configuration
            elif hasattr(self.config, 'webdav_workspace_path') and self.config.webdav_workspace_path:
                workspace_name = self.config.webdav_workspace_path
                logger.info(f"[OCS_SHARE] Espace de travail trouvé dans la configuration: {workspace_name}")
            
            # Normalisation robuste du chemin pour l'API OCS
            normalized_path = path.strip()
            original_raw_path = normalized_path  # Sauvegarder le chemin d'origine pour journalisation
            
            # Décoder d'abord si le chemin contient des caractères encodés (%xx)
            if "%" in normalized_path:
                original_path = normalized_path
                normalized_path = urllib.parse.unquote(normalized_path)
                logger.info(f"[OCS_SHARE] Décodage URL: '{original_path}' -> '{normalized_path}'")
            
            # Normalisation Unicode pour garantir la cohérence des caractères spéciaux
            import unicodedata
            unicode_normalized_path = unicodedata.normalize('NFC', normalized_path)
            if unicode_normalized_path != normalized_path:
                logger.info(f"[OCS_SHARE] Normalisation Unicode: '{normalized_path}' -> '{unicode_normalized_path}'")
                normalized_path = unicode_normalized_path
            
            # Supprimer les préfixes WebDAV complets si présents
            if "/remote.php/dav/files/" in normalized_path:
                # Extraire seulement la partie après /remote.php/dav/files/username/
                parts = normalized_path.split("/remote.php/dav/files/")
                if len(parts) > 1:
                    # Prendre la partie après remote.php/dav/files/
                    remaining = parts[1]
                    # Supprimer le nom d'utilisateur si présent au début
                    if "/" in remaining:
                        username_part, file_part = remaining.split("/", 1)
                        # Vérifier si c'est un nom d'utilisateur valide
                        if username_part == self.webdav_username or username_part == self.webdav_username.split('@')[0]:
                            normalized_path = file_part
                            logger.info(f"[OCS_SHARE] Extraction du chemin après username: '{original_raw_path}' -> '{normalized_path}'")
                        else:
                            # Considérer que c'est déjà le chemin du fichier
                            normalized_path = remaining
                            logger.info(f"[OCS_SHARE] Extraction du chemin complet: '{original_raw_path}' -> '{normalized_path}'")
                    else:
                        normalized_path = remaining
                        logger.info(f"[OCS_SHARE] Extraction sans username: '{original_raw_path}' -> '{normalized_path}'")
            
            # Supprimer le préfixe utilisateur s'il est présent au début du chemin
            if self.webdav_username:
                username_simple = self.webdav_username.split('@')[0]  # Partie avant @
                
                # Essayer avec le nom d'utilisateur complet
                if normalized_path.startswith(f"{self.webdav_username}/"):
                    original_path = normalized_path
                    normalized_path = normalized_path[len(self.webdav_username)+1:]
                    logger.info(f"[OCS_SHARE] Suppression préfixe email: '{original_path}' -> '{normalized_path}'")
                # Essayer avec le nom d'utilisateur simple
                elif normalized_path.startswith(f"{username_simple}/"):
                    original_path = normalized_path
                    normalized_path = normalized_path[len(username_simple)+1:]
                    logger.info(f"[OCS_SHARE] Suppression préfixe utilisateur: '{original_path}' -> '{normalized_path}'")
            
            # CRUCIAL: S'assurer que le chemin N'A PAS de slash initial pour l'API OCS
            # L'API OCS attend un chemin relatif sans slash au début
            normalized_path = normalized_path.lstrip('/')
            
            # S'assurer que le chemin inclut l'espace de travail au début
            if not normalized_path.startswith(f"{workspace_name}/"):
                normalized_path = f"{workspace_name}/{normalized_path}"
                logger.info(f"[OCS_SHARE] Ajout de l'espace de travail au chemin: {normalized_path}")
            
            # Vérifier si le chemin contient déjà un dossier (présence de /)
            if workspace_name in normalized_path and "/" not in normalized_path.replace(f"{workspace_name}/", ""):
                # Récupérer le chemin racine configuré
                webdav_root_path = self.config.webdav_root_path if hasattr(self.config, 'webdav_root_path') else "/documents"
                webdav_root_path = webdav_root_path.strip('/')
                
                # Si le fichier est à la racine, ajouter le chemin racine WebDAV
                if webdav_root_path:
                    if not webdav_root_path.endswith('/'):
                        webdav_root_path += '/'
                    # Remplacer workspace_name/ par workspace_name/webdav_root_path/
                    normalized_path = normalized_path.replace(
                        f"{workspace_name}/", 
                        f"{workspace_name}/{webdav_root_path}/"
                    )
                    logger.info(f"[OCS_SHARE] Ajout du chemin racine: {normalized_path}")
            elif hasattr(self.config, 'webdav_root_path'):
                # Vérifier si le chemin doit être préfixé par le chemin racine après l'espace de travail
                webdav_root_path = self.config.webdav_root_path.strip('/')
                workspace_prefix = f"{workspace_name}/"
                
                if (webdav_root_path and 
                    normalized_path.startswith(workspace_prefix) and 
                    not normalized_path.startswith(f"{workspace_prefix}{webdav_root_path}/")):
                    
                    # Restructurer le chemin pour inclure le chemin racine après l'espace de travail
                    path_after_workspace = normalized_path[len(workspace_prefix):]
                    if not webdav_root_path.endswith('/'):
                        webdav_root_path += '/'
                    normalized_path = f"{workspace_name}/{webdav_root_path}{path_after_workspace}"
                    logger.info(f"[OCS_SHARE] Restructuration du chemin avec racine: {normalized_path}")
            
            logger.info(f"[OCS_SHARE] Chemin normalisé final (sans slash initial): {normalized_path}")
            
            # Toujours utiliser la stratégie de lien public
            logger.info("[OCS_SHARE] Création d'un lien public avec expiration courte")
            
            # Construire les paramètres du partage public conformes à la spécification
            public_params = {
                "path": normalized_path,
                "shareType": 3,  # 3 = lien public
                "permissions": 1,  # 1 = lecture seule
                "format": "json"  # Spécifier explicitement JSON comme format de réponse
            }
            
            # Ajouter un mot de passe si fourni
            if password:
                public_params["password"] = password
                logger.info("[OCS_SHARE] Lien public protégé par mot de passe")
                
            # Utiliser expiration courte par défaut (2 jours) pour les liens publics
            expiry_days = expiration_days if expiration_days else 2
            if expiry_days > 0:
                # Calculer la date d'expiration (format YYYY-MM-DD)
                from datetime import datetime, timedelta
                expiry_date = (datetime.now() + timedelta(days=expiry_days)).strftime("%Y-%m-%d")
                public_params["expireDate"] = expiry_date
                logger.info(f"[OCS_SHARE] Date d'expiration lien public: {expiry_date}")
            
            # En-têtes HTTP selon la spécification officielle
            headers = {
                "OCS-APIRequest": "true",  # En-tête obligatoire selon la spécification
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json"
            }
            
            logger.info(f"[OCS_SHARE] Envoi requête lien public: {public_params}")
            
            response = await self.http_client.post(
                ocs_url, 
                data=public_params,
                headers=headers
            )
            
            logger.info(f"[OCS_SHARE] Réponse lien public: status={response.status_code}, headers={response.headers}")
            logger.info(f"[OCS_SHARE] Réponse lien public contenu: {response.text[:500]}")
            
            # Vérifier le statut HTTP
            if response.status_code not in [200, 201]:
                logger.error(f"[OCS_SHARE] Statut HTTP inattendu: {response.status_code}")
                try:
                    error_text = response.text
                    logger.error(f"[OCS_SHARE] Réponse d'erreur: {error_text}")
                except:
                    logger.error("[OCS_SHARE] Impossible de lire la réponse d'erreur")
                
                # Fallback vers WebDAV direct si nécessaire
                return self._create_webdav_fallback_link(base_url, normalized_path, target_user)
            
            # Parser la réponse JSON
            try:
                response_data = response.json()
                logger.info(f"[OCS_SHARE] Réponse JSON lien public: {response_data}")
            except Exception as json_error:
                logger.error(f"[OCS_SHARE] Erreur parsing JSON: {str(json_error)}")
                logger.error(f"[OCS_SHARE] Réponse brute: {response.text}")
                return self._create_webdav_fallback_link(base_url, normalized_path, target_user)
            
            # Vérifier la structure de la réponse OCS
            ocs_data = response_data.get("ocs", {})
            meta = ocs_data.get("meta", {})
            data = ocs_data.get("data", {})
            
            # Vérifier le statut OCS
            ocs_status = meta.get("status")
            ocs_statuscode = meta.get("statuscode")
            
            logger.info(f"[OCS_SHARE] Status OCS: {ocs_status}, Code: {ocs_statuscode}")
            
            # Code 100 = succès selon la spécification OCS, certains serveurs renvoient 200
            if (ocs_status == "ok" and ocs_statuscode == 200) or ocs_statuscode == 100:
                # Extraire l'URL du partage
                share_url = data.get("url")
                if share_url:
                    # Vérifier si l'URL est bien au format OCS (contient /s/)
                    if "/s/" in share_url or "/f/" in share_url:
                        logger.info(f"[OCS_SHARE] ✅ Lien public créé avec succès: {share_url}")
                    else:
                        logger.warning(f"[OCS_SHARE] ⚠️ Lien public non-OCS créé: {share_url}")
                    return share_url
                else:
                    # Vérifier s'il y a un ID de partage et essayer de construire l'URL
                    token = data.get("token")
                    if token:
                        # Construire l'URL manuellement si nous avons un token
                        constructed_url = f"{base_url}/s/{token}"
                        logger.info(f"[OCS_SHARE] ✅ URL construite à partir du token: {constructed_url}")
                        return constructed_url
                    else:
                        logger.error(f"[OCS_SHARE] Pas d'URL ni de token dans la réponse: {data}")
                        return self._create_webdav_fallback_link(base_url, normalized_path, target_user)
            else:
                error_message = meta.get("message", "Erreur inconnue")
                if ocs_statuscode == 403:
                    logger.error(f"[OCS_SHARE] Accès refusé (403): {error_message}")
                elif ocs_statuscode == 400:
                    logger.error(f"[OCS_SHARE] Requête invalide (400): {error_message}")
                else:
                    logger.error(f"[OCS_SHARE] Erreur OCS ({ocs_statuscode}): {error_message}")
                    
                return self._create_webdav_fallback_link(base_url, normalized_path, target_user)
                
        except Exception as e:
            logger.error(f"[OCS_SHARE] Erreur générale: {str(e)}")
            import traceback
            logger.error(f"[OCS_SHARE] Traceback: {traceback.format_exc()}")
            # En cas d'erreur, fallback vers WebDAV direct
            return self._create_webdav_fallback_link(base_url, path, target_user)

    def _create_webdav_fallback_link(self, base_url: str, path: str, target_user: Optional[str] = None) -> str:
        """
        Crée une URL WebDAV directe comme fallback ultime.
        
        Args:
            base_url: URL de base du serveur
            path: Chemin du fichier
            target_user: Utilisateur cible pour le lien
            
        Returns:
            URL WebDAV directe
        """
        logger.warning("[OCS_SHARE] Création d'un lien direct WebDAV (fallback)")
        
        # Si nous n'avons pas d'utilisateur cible, impossible de créer un lien
        if not target_user:
            logger.error("[OCS_SHARE] Impossible de créer un lien WebDAV sans utilisateur cible")
            return ""
            
        # Extraire prénom.nom pour le fallback WebDAV
        fallback_username = target_user
        
        # Extraction améliorée du prénom.nom
        if '@' in fallback_username:
            # Format Matrix
            if ':' in fallback_username:
                username_part = fallback_username.split(':')[0]
                if username_part.startswith('@'):
                    fallback_username = username_part[1:]
            # Format email
            else:
                fallback_username = fallback_username.split('@')[0]
        
        # Gestion des formats spéciaux (prénom.nom-domaine.gouv.fr1)
        if '.' in fallback_username and '-' in fallback_username:
            # Trouver la première occurrence de tiret après le premier point
            first_dot_pos = fallback_username.find('.')
            first_dash_pos = fallback_username.find('-', first_dot_pos)
            
            if first_dash_pos > first_dot_pos:
                # Extraire seulement prénom.nom
                fallback_username = fallback_username[:first_dash_pos]
                logger.info(f"[OCS_SHARE] Extraction prénom.nom: {target_user} -> {fallback_username}")
        
        # Préparer le chemin pour l'URL WebDAV
        normalized_path = path.lstrip('/')
        
        # Vérifier si le chemin contient un dossier parent
        if '/' in normalized_path:
            parent_folder = normalized_path.rsplit('/', 1)[0]
            logger.info(f"[OCS_SHARE] Dossier parent détecté: {parent_folder}")
        
        # Récupérer l'espace de travail depuis le contexte WebDAV
        # Par défaut, utiliser "dossiers-colaig-assistant" comme espace de travail
        workspace_path = "dossiers-colaig-assistant"
        
        # Tenter de récupérer l'espace de travail depuis les attributs du service WebDAV
        try:
            # Vérifier si le service WebDAV a un attribut workspace_path
            if hasattr(self, 'workspace_path') and self.workspace_path:
                workspace_path = self.workspace_path
                logger.info(f"[OCS_SHARE] Espace de travail trouvé dans le service WebDAV: {workspace_path}")
            # Sinon, essayer de le récupérer depuis la configuration
            elif hasattr(self, 'config') and hasattr(self.config, 'webdav_workspace_path') and self.config.webdav_workspace_path:
                workspace_path = self.config.webdav_workspace_path
                logger.info(f"[OCS_SHARE] Espace de travail trouvé dans la configuration: {workspace_path}")
            # Pour les contextes utilisateurs, nous ne pouvons pas faire d'appels asynchrones ici
            # car cette méthode est appelée de manière synchrone
            # Nous utilisons donc la valeur par défaut
        except Exception as e:
            logger.warning(f"[OCS_SHARE] Erreur lors de la récupération de l'espace de travail: {str(e)}")
            logger.warning(f"[OCS_SHARE] Utilisation de l'espace de travail par défaut: {workspace_path}")
        
        # Construire l'URL WebDAV de fallback avec le chemin complet et l'espace de travail
        fallback_url = f"{base_url}/remote.php/dav/files/{fallback_username}/{workspace_path}/{normalized_path}?download=1"
        
        logger.info(f"[OCS_SHARE] ⚠️ URL WebDAV fallback construite: {fallback_url}")
        return fallback_url

# Fonction d'initialisation globale pour obtenir le service WebDAV
# Version simplifiée qui évite l'importation circulaire
async def initialize_webdav_service(config: Config) -> "WebDAVService":
    """
    Initialise une instance du service WebDAV.
    Pour éviter les importations circulaires, cette fonction crée une nouvelle instance
    au lieu d'utiliser webdav_instance.
    
    Args:
        config: Configuration de l'application
        
    Returns:
        Instance initialisée de WebDAVService
    """
    service = WebDAVService(
        config=config,
        webdav_url=config.webdav_url,
        webdav_username=config.webdav_username,
        webdav_password=config.webdav_password,
        webdav_root=config.webdav_root_path
    )
    await service.init_webdav()
    return service

# Exporter la classe et la fonction d'initialisation
__all__ = ['WebDAVService', 'initialize_webdav_service']