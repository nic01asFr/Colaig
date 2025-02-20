# app/services/bnum_service.py

from typing import Dict, List, Optional, Tuple
from datetime import datetime
from pathlib import Path
import json
import os
import io
import httpx
import asyncio
import re
import tempfile

# Imports PDFMiner.six corrigés
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams, LTTextContainer, LTChar, LTPage
from pdfminer.pdfpage import PDFPage
from pdfminer.pdfinterp import PDFResourceManager, PDFPageInterpreter
from pdfminer.converter import PDFPageAggregator

from matrix_bot.config import logger
from config import Config
from services.webdav import WebDAVService
from core_llm import AlbertApiClient, generate, SYSTEM_PROMPT

class BnumService:
    """Service de gestion de la bibliothèque numérique"""
    CONFIG_FILE = ".albert/bnum_config.json"
    SUPPORTED_EXTENSIONS = {
        'pdf': 'application/pdf',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'txt': 'text/plain',
        'md': 'text/markdown',
    }
    SYSTEM_DIRS = {'.albert', '.versions', '.index', '.git', '__pycache__'}
    
    def __init__(self, config: Config):
        self.config = config
        self.webdav = WebDAVService(config)
        self._collection_id = None
        self._albert_client = None
        
    def is_system_file(self, path: str) -> bool:
        """Vérifie si un fichier est un fichier système"""
        parts = Path(path).parts
        return any(part.startswith('.') or part in self.SYSTEM_DIRS for part in parts)
        
    async def initialize(self) -> None:
        """Initialise le service"""
        try:
            await self._ensure_system_directory()
            
            # Initialiser le client Albert avant de charger la config
            # car elle en a besoin pour vérifier les collections
            self._albert_client = AlbertApiClient(
                base_url=self.config.albert_api_url,
                api_key=self.config.albert_api_token
            )
            
            # Charger la config après l'initialisation du client
            await self._load_config()
            
        except Exception as e:
            logger.error(f"Erreur initialisation BnumService: {str(e)}")
            # S'assurer de fermer le client en cas d'erreur
            if self._albert_client:
                await self._albert_client.close()
            raise
            
    async def _ensure_system_directory(self) -> None:
        """S'assure que le dossier système existe"""
        try:
            albert_dir = os.path.dirname(self.CONFIG_FILE)
            if not await self.webdav.exists(albert_dir):
                logger.info(f"Création du dossier système {albert_dir}")
                if not await self.webdav.create_directory(albert_dir):
                    raise RuntimeError(f"Impossible de créer le dossier {albert_dir}")
        except Exception as e:
            logger.error(f"Erreur création dossier système: {str(e)}")
            raise
            
    async def _load_config(self) -> None:
        """Charge la configuration BNUM"""
        try:
            if await self.webdav.exists(self.CONFIG_FILE):
                content = await self.webdav.read_document(self.CONFIG_FILE)
                config_data = json.loads(content)
                self._collection_id = config_data.get("collection_id")
                
                # Vérifier que la collection existe toujours
                if self._collection_id:
                    try:
                        # Créer un nom unique pour la collection
                        space_name = Path(self.config.webdav_root_path).name
                        collection_name = f"bnum_{space_name}"
                        
                        # Récupérer les collections existantes
                        collections = await self._albert_client.fetch_collections()
                        collection = None
                        
                        # Chercher la collection par nom
                        for coll in collections.values():
                            if coll["name"] == collection_name:
                                collection = coll
                                break
                                
                        # Créer si elle n'existe pas
                        if not collection:
                            collection = await self._albert_client.create_collection(
                                collection_name,
                                self.config.albert_model_embedding
                            )
                            
                        if collection["id"] != self._collection_id:
                            logger.warning("Collection ID mismatch, updating configuration")
                            self._collection_id = collection["id"]
                            await self._save_config()
                            
                    except Exception as e:
                        logger.error(f"Erreur vérification collection: {str(e)}")
                        self._collection_id = None
                        
        except Exception as e:
            logger.error(f"Erreur chargement config BNUM: {str(e)}")
            self._collection_id = None
            
    async def _save_config(self) -> None:
        """Sauvegarde la configuration BNUM"""
        try:
            await self._ensure_system_directory()
            
            config_data = {
                "collection_id": self._collection_id,
                "last_updated": datetime.now().isoformat(),
                "webdav_root": self.config.webdav_root_path,
                "version": "1.0"
            }
            
            await self.webdav.write_file(
                self.CONFIG_FILE,
                json.dumps(config_data, indent=2)
            )
            logger.info("Configuration BNUM sauvegardée")
            
        except Exception as e:
            logger.error(f"Erreur sauvegarde config BNUM: {str(e)}")
            raise
            
    @property
    def collection_id(self) -> Optional[str]:
        """Retourne l'ID de la collection BNUM"""
        return self._collection_id

    def _is_supported_file(self, filename: str) -> bool:
        """Vérifie si le type de fichier est supporté par Albert"""
        ext = Path(filename).suffix.lower().lstrip('.')
        return ext in self.SUPPORTED_EXTENSIONS

    def _get_mime_type(self, filename: str) -> Optional[str]:
        """Retourne le type MIME pour un fichier"""
        ext = Path(filename).suffix.lower().lstrip('.')
        return self.SUPPORTED_EXTENSIONS.get(ext)

    def _validate_document_format(self, document: dict) -> bool:
        """Valide le format d'un document pour l'API Albert"""
        try:
            # Vérifier les champs requis
            if not isinstance(document, dict):
                logger.error("Le document doit être un dictionnaire")
                return False
                
            if not all(key in document for key in ["title", "text"]):
                logger.error("Document manquant title ou text")
                return False
            
            # Vérifier que les champs sont des chaînes non vides
            if not isinstance(document["title"], str) or not document["title"].strip():
                logger.error("Le titre doit être une chaîne non vide")
                return False
                
            if not isinstance(document["text"], str) or not document["text"].strip():
                logger.error("Le texte doit être une chaîne non vide")
                return False
            
            # Vérifier les métadonnées si présentes
            if "metadata" in document:
                if not isinstance(document["metadata"], dict):
                    logger.error("Les métadonnées doivent être un dictionnaire")
                    return False
                    
                required_metadata = ["source", "original_path", "mime_type"]
                if not all(key in document["metadata"] for key in required_metadata):
                    logger.error(f"Métadonnées manquantes: {', '.join(required_metadata)}")
                    return False
                    
                # Vérifier que les valeurs des métadonnées sont des types valides
                for key, value in document["metadata"].items():
                    if not isinstance(value, (str, int, float, bool, type(None))):
                        logger.error(f"Type invalide pour la métadonnée {key}")
                        return False
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur validation format document: {str(e)}")
            return False

    async def _upload_file_to_albert(self, file_content: bytes, filename: str) -> Dict:
        """Upload un fichier vers l'API Albert"""
        try:
            # Vérifier le type MIME
            mime_type = self._get_mime_type(filename)
            if not mime_type:
                raise ValueError(f"Type de fichier non supporté: {filename}")
            
            # Vérifier la taille du fichier
            file_size = len(file_content)
            if file_size == 0:
                raise ValueError(f"Fichier vide: {filename}")
            if file_size > 20 * 1024 * 1024:  # 20 MB (limite de l'API)
                raise ValueError(f"Fichier trop volumineux: {filename} ({file_size / 1024 / 1024:.1f} MB)")

            # Nom du fichier sans caractères spéciaux pour l'API
            safe_filename = os.path.basename(filename).replace("%20", " ")
            
            # Pour les fichiers non-JSON, les envoyer directement à l'API
            if mime_type != "application/json":
                # Créer un BytesIO pour le fichier
                file_obj = io.BytesIO(file_content)
                file_obj.name = safe_filename
                file_obj.type = mime_type
                
                # Upload via le client Albert
                return await self._albert_client.upload_file(file_obj, self._collection_id)
            
            # Pour les fichiers JSON, continuer avec le traitement existant
            formatted_content = []
            
            if mime_type == "text/markdown":
                # Pour les fichiers Markdown, utiliser le contenu directement
                text_content = file_content.decode('utf-8')
                
                # Nettoyer le texte
                text_content = re.sub(r"([.,;:!?])([^\s\d])", r"\1 \2", text_content)  # Ajouter espace après ponctuation
                text_content = re.sub(r"[\xa0\u00a0\r]", " ", text_content)  # Supprimer caractères spéciaux
                text_content = re.sub(r"&nbsp;", " ", text_content)
                
                # Extraire le titre du fichier Markdown (première ligne commençant par #)
                lines = text_content.split('\n')
                title = safe_filename
                content = text_content
                
                for line in lines:
                    if line.startswith('#'):
                        title = line.lstrip('#').strip()
                        content = '\n'.join(lines[lines.index(line)+1:])
                        break
                
                # Créer le document au format attendu par l'API
                document = {
                    "title": title,
                    "text": content.strip(),
                    "metadata": {
                        "source": "bnum",
                        "original_path": filename,
                        "mime_type": mime_type,
                        "file_size": file_size,
                        "format": "markdown"
                    }
                }
                
                if not self._validate_document_format(document):
                    raise ValueError(f"Format de document invalide pour {filename}")
                
                formatted_content = [document]
                
            elif mime_type == "application/pdf":
                # Pour les PDFs, extraire le texte avec PDFMiner
                pdf_content = io.BytesIO(file_content)
                
                # Paramètres d'extraction améliorés
                laparams = LAParams(
                    line_margin=0.3,      # Réduit pour mieux détecter les lignes
                    word_margin=0.1,      # Marge entre les mots
                    char_margin=1.0,      # Réduit pour mieux grouper les caractères
                    boxes_flow=0.7,       # Augmenté pour mieux suivre le flux de texte
                    detect_vertical=True,  # Détection du texte vertical
                    all_texts=True        # Capture tout le texte, même décoratif
                )
                
                formatted_content = []
                current_page = 1
                total_pages = len(list(PDFPage.get_pages(pdf_content)))
                pdf_content.seek(0)  # Remettre le curseur au début
                
                # Extraire le texte page par page
                for page in PDFPage.get_pages(pdf_content):
                    # Initialiser les outils PDFMiner pour chaque page
                    resource_manager = PDFResourceManager()
                    device = PDFPageAggregator(resource_manager, laparams=laparams)
                    interpreter = PDFPageInterpreter(resource_manager, device)
                    
                    # Traiter la page
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
                        page_content = f"\n[Page {current_page}]\n" + "\n".join(page_text)
                        document = {
                            "title": f"{safe_filename} - Page {current_page}",
                            "text": page_content,
                            "metadata": {
                                "source": "bnum",
                                "original_path": filename,
                                "mime_type": mime_type,
                                "page": current_page,
                                "total_pages": total_pages,
                                "file_size": file_size
                            }
                        }
                        
                        if not self._validate_document_format(document):
                            logger.warning(f"Page {current_page} ignorée car format invalide")
                            continue
                        
                        formatted_content.append(document)
                    
                    # Nettoyer les ressources de la page
                    device.close()
                    current_page += 1
                
                # Si aucun texte n'a été extrait, essayer extract_text comme fallback
                if not formatted_content:
                    logger.warning(f"Aucun texte extrait avec PDFMiner pour {filename}, tentative avec extract_text")
                    pdf_content.seek(0)
                    text_content = extract_text(pdf_content)
                    if text_content.strip():
                        document = {
                            "title": safe_filename,
                            "text": text_content,
                            "metadata": {
                                "source": "bnum",
                                "original_path": filename,
                                "mime_type": mime_type,
                                "extraction_method": "fallback",
                                "file_size": file_size
                            }
                        }
                        
                        if self._validate_document_format(document):
                            formatted_content = [document]
                        else:
                            raise ValueError(f"Format de document invalide après fallback pour {filename}")
                    else:
                        raise ValueError(f"Impossible d'extraire du texte du PDF: {filename}")

            else:
                # Pour les autres types, convertir en texte
                text_content = file_content.decode('utf-8')
                document = {
                    "title": safe_filename,
                    "text": text_content,
                    "metadata": {
                        "source": "bnum",
                        "original_path": filename,
                        "mime_type": mime_type,
                        "file_size": file_size
                    }
                }
                
                if not self._validate_document_format(document):
                    raise ValueError(f"Format de document invalide pour {filename}")
                
                formatted_content = [document]

            # Découper en batches de 64 documents maximum
            batch_size = 64
            success_count = 0
            
            for i in range(0, len(formatted_content), batch_size):
                batch = formatted_content[i:i + batch_size]
                
                try:
                    # Préparer et valider le JSON
                    if not isinstance(batch, list):
                        raise ValueError("Le batch doit être une liste de documents")
                    for doc in batch:
                        if not isinstance(doc, dict) or not self._validate_document_format(doc):
                            raise ValueError(f"Format de document invalide: {doc.get('title', 'Unknown')}")
                    
                    # Préparer la requête
                    request_data = {
                        "collection": self._collection_id,
                        "chunker": "LangchainRecursiveCharacterTextSplitter",
                        "chunker_args": {"chunk_size": 500, "chunk_overlap": 100},
                        "documents": batch
                    }
                    
                    # Upload avec retry
                    max_retries = 3
                    
                    for attempt in range(max_retries):
                        try:
                            # Envoyer la requête JSON directement
                            response = await self._albert_client.http_client.post(
                                f"{self._albert_client.base_url}/v1/files",
                                json=request_data,
                                timeout=60.0
                            )
                            
                            if response.status_code == 422:
                                error_detail = response.json().get("detail", "")
                                logger.error(f"Erreur validation API Albert: {error_detail}")
                                raise ValueError(f"Format invalide pour l'API Albert: {error_detail}")
                            
                            response.raise_for_status()
                            success_count += 1
                            logger.info(f"Batch {i//batch_size + 1} uploadé avec succès")
                            break
                            
                        except Exception as e:
                            if attempt == max_retries - 1:
                                raise
                            logger.warning(f"Erreur upload fichier, nouvelle tentative dans 2s")
                            await asyncio.sleep(2)
                
                except Exception as e:
                    logger.error(f"Erreur traitement batch {i}: {str(e)}")
                    raise
            
            return {"status": "success", "batches_uploaded": success_count}
            
        except Exception as e:
            logger.error(f"Erreur upload fichier {filename}: {str(e)}")
            raise

    async def index_documents(self) -> Tuple[int, int]:
        """Indexe les documents dans une collection BNUM
        
        Returns:
            Tuple[int, int]: (nb_indexed, total)
        """
        try:
            # Créer un nom unique pour la collection
            space_name = Path(self.config.webdav_root_path).name
            collection_name = f"bnum_{space_name}"
            
            # Récupérer ou créer la collection
            collections = await self._albert_client.fetch_collections()
            collection = None
            
            # Chercher la collection par nom
            for coll in collections.values():
                if coll["name"] == collection_name:
                    collection = coll
                    break
                    
            # Créer si elle n'existe pas
            if not collection:
                collection = await self._albert_client.create_collection(
                    collection_name,
                    self.config.albert_model_embedding
                )
                
            self._collection_id = collection["id"]
            
            # Lister les documents
            documents = await self.webdav.list_documents()
            valid_docs = [
                doc for doc in documents 
                if not self.is_system_file(doc) and self._is_supported_file(doc)
            ]
            total_docs = len(valid_docs)
            
            if total_docs == 0:
                raise ValueError("Aucun document à indexer")
            
            # Indexer chaque document
            indexed_count = 0
            errors = []
            
            for doc_path in valid_docs:
                try:
                    # Lire le document
                    content = await self.webdav.download_file(doc_path)
                    if not content:
                        logger.warning(f"Document vide ignoré: {doc_path}")
                        continue
                        
                    # Upload vers Albert en utilisant le chemin complet
                    await self._upload_file_to_albert(content, doc_path)
                    
                    indexed_count += 1
                    logger.info(f"Document indexé: {doc_path}")
                    
                except Exception as e:
                    logger.error(f"Erreur indexation {doc_path}: {str(e)}")
                    errors.append((doc_path, str(e)))
                    continue
            
            # Sauvegarder la configuration
            await self._save_config()
            
            # Logger les erreurs s'il y en a
            if errors:
                error_summary = "\n".join(
                    f"- {path}: {error}" 
                    for path, error in errors
                )
                logger.error(f"Erreurs d'indexation:\n{error_summary}")
            
            return indexed_count, total_docs
            
        except Exception as e:
            logger.error(f"Erreur indexation documents: {str(e)}")
            raise

    async def search(self, query: str, limit: int = 5) -> List[Dict]:
        """Recherche dans les documents via l'API Albert"""
        try:
            if not self._collection_id:
                await self._load_config()  # Tentative de rechargement
                if not self._collection_id:
                    raise ValueError("Collection non initialisée")
                    
            # Faire la recherche via AlbertApiClient
            chunks = await self._albert_client.semantic_search(
                self.config.albert_model_embedding,
                query,
                limit,
                [self._collection_id]
            )
            
            # Formater les résultats
            results = []
            for chunk in chunks:
                results.append({
                    "score": chunk["metadata"].get("similarity_score", 0),
                    "chunk": chunk
                })
            
            # Trier par score
            results.sort(key=lambda x: x["score"], reverse=True)
            return results
                    
        except Exception as e:
            logger.error(f"Erreur recherche documents: {str(e)}")
            raise

    async def generate_response(self, query: str, results: List[Dict]) -> str:
        """Génère une réponse basée sur les résultats de recherche"""
        try:
            # Préparer le contexte avec métadonnées enrichies
            context_parts = []
            for result in results:
                chunk = result["chunk"]
                metadata = chunk["metadata"]
                
                # Enrichir le contexte avec les métadonnées disponibles
                context_part = [f"[Document: {metadata['document_name']}]"]
                
                if "document_title" in metadata:
                    context_part.append(f"[Titre: {metadata['document_title']}]")
                if "section_title" in metadata:
                    context_part.append(f"[Section: {metadata['section_title']}]")
                if "page_number" in metadata:
                    context_part.append(f"[Page: {metadata['page_number']}]")
                    
                # Ajouter le score de pertinence
                context_part.append(f"[Score: {result.get('score', 0):.2f}]")
                
                # Ajouter le contenu
                context_part.append(chunk["content"])
                
                context_parts.append("\n".join(context_part))
            
            context = "\n\n---\n\n".join(context_parts)
            
            # Construire le prompt
            messages = [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT
                },
                {
                    "role": "user",
                    "content": (
                        f"Utilise le contexte suivant pour répondre à la question: {query}\n\n"
                        f"Contexte:\n{context}\n\n"
                        "Instructions supplémentaires:\n"
                        "1. Cite les sources pertinentes dans ta réponse\n"
                        "2. Indique le niveau de confiance des informations\n"
                        "3. Précise si certaines informations semblent datées"
                    )
                }
            ]
            
            # Générer la réponse
            response = await generate(self.config, messages)
            return response
            
        except Exception as e:
            logger.error(f"Erreur génération réponse: {str(e)}")
            raise

    async def close(self):
        """Ferme les connexions"""
        try:
            if self._albert_client:
                await self._albert_client.close()
            await self.webdav.close()
        except Exception as e:
            logger.error(f"Erreur fermeture connexions: {str(e)}")
            
    async def __aenter__(self):
        await self.initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()