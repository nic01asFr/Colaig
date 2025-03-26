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

from config import Config
from matrix_bot.config import logger
from .context.models import get_synchronized_time

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
    
    def __init__(self, config: Config):
        self.config = config
        self.base_url = config.webdav_url
        self.versions: Dict[str, List[DocumentVersion]] = {}
        self._initialized = False  # Ajout de l'attribut d'initialisation
        
        # Construire les chemins complets
        self.root_path = config.webdav_root_path
        self.system_root = os.path.join(self.root_path, self.SYSTEM_ROOT)
        self.contexts_path = os.path.join(self.root_path, self.CONTEXTS_DIR)
        self.versions_path = os.path.join(self.root_path, self.VERSIONS_DIR)
        self.index_path = os.path.join(self.root_path, self.INDEX_DIR)
        self.config_path = os.path.join(self.root_path, self.CONFIG_DIR)
        
        # Configuration du client HTTP
        self.http_client = httpx.AsyncClient(
            auth=(config.webdav_username, config.webdav_password),
            timeout=60.0,
            headers={
                'User-Agent': 'AlbertTchap/1.0',
                'Accept': '*/*'
            },
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
            http2=True,
            verify=True  # Vérification SSL
        )
        logger.info("Client WebDAV initialisé")
        # Ne pas lancer la tâche ici, elle sera lancée dans initialize()

    async def initialize(self) -> None:
        """Initialise le service WebDAV"""
        try:
            # Vérifier la configuration
            if not hasattr(self.config, 'webdav_url') or not self.config.webdav_url:
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

    async def close(self):
        """Ferme proprement le client HTTP"""
        try:
            if self.http_client:
                await self.http_client.aclose()
                logger.info("Session HTTP fermée avec succès")
        except Exception as e:
            logger.error(f"Erreur lors de la fermeture du client HTTP: {str(e)}")
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
            if not path.startswith(self.root_path):
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
            
        # Encoder correctement le chemin pour l'URL
        # Découper le chemin en segments et encoder chaque segment séparément
        
        # Supprimer le slash initial pour le traitement par segments
        path_without_leading_slash = full_path[1:] if full_path.startswith('/') else full_path
        
        # Diviser le chemin en segments et encoder chaque segment séparément
        segments = path_without_leading_slash.split('/')
        encoded_segments = [urllib.parse.quote(segment, safe='') for segment in segments]
        
        # Reconstruire le chemin avec les segments encodés
        encoded_path = '/'.join(encoded_segments)
        
        # Construire l'URL complète
        url = f"{self.base_url}/{encoded_path}"
        
        return url

    async def write_file(self, path: str, content: Union[str, bytes]) -> None:
        """Écrit le contenu dans un fichier WebDAV"""
        try:
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
        url = self._get_url(path)
        
        for attempt in range(max_retries):
            try:
                response = await self.http_client.get(url)
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
                    
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(f"Tentative {attempt + 1}/{max_retries} échouée pour {path}: {str(e)}")
                    await asyncio.sleep(2 ** attempt)  # Backoff exponentiel
                else:
                    logger.error(f"Échec définitif de lecture du document {path} après {max_retries} tentatives: {str(e)}")
                    raise

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
                item_path = href.split('remote.php/dav/files/')[-1].split('/', 1)[-1]
                if item_path.startswith(self.root_path):
                    item_path = item_path[len(self.root_path):].lstrip('/')
                
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

    async def list_documents(self, path: str = None) -> List[str]:
        """Liste tous les documents dans le répertoire WebDAV"""
        try:
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
                    path = href.split('remote.php/dav/files/')[-1].split('/', 1)[-1]
                    if path.startswith(self.root_path):
                        path = path[len(self.root_path):].lstrip('/')
                    documents.append(path)
            
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
                    "last_update": get_synchronized_time().isoformat(),
                    "documents": {}
                }
                await self._save_versions()
                logger.info("Nouveau fichier de versions créé")
                return

            content = await self.read_document(versions_path)
            if not content.strip():
                self.versions = {
                    "last_update": get_synchronized_time().isoformat(),
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
                "last_update": get_synchronized_time().isoformat(),
                "documents": {}
            }
            await self._save_versions()

    async def _save_versions(self):
        """Sauvegarde l'historique des versions"""
        try:
            # Convertir les versions en format sérialisable
            versions_data = {
                "last_update": get_synchronized_time().isoformat(),
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
                                'created_at': version.get('created_at', get_synchronized_time().isoformat()),
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
            # Construire le chemin du contexte
            context_path = f"{self.CONTEXTS_DIR}/{context_id}.json"
            
            # Vérifier si le fichier existe
            if not await self.exists(context_path):
                logger.debug(f"Contexte {context_id} non trouvé")
                return None
            
            # Lire le contenu
            content = await self.read_document(context_path)
            if not content.strip():
                logger.warning(f"Contexte {context_id} vide")
                return None
            
            # Parser le JSON
            try:
                data = json.loads(content)
                if not isinstance(data, dict):
                    logger.error(f"Format invalide pour le contexte {context_id}")
                    return None
                return data
            except json.JSONDecodeError as e:
                logger.error(f"Erreur décodage JSON pour {context_id}: {str(e)}")
                return None
            
        except Exception as e:
            logger.error(f"Erreur lecture contexte {context_id}: {str(e)}")
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