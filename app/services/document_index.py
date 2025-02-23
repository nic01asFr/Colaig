from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass
import json
from datetime import datetime
import os
from pathlib import Path
import asyncio
import faiss
import numpy as np
from functools import lru_cache

from matrix_bot.config import logger
from config import Config
from services.webdav import WebDAVService
from services.embedding_service import EmbeddingService
from services.scoring import DynamicScoreManager, ScoringResult
from services.chunk_selector import ChunkSelector
from services.models import DocumentChunk

@dataclass
class DocumentChunk:
    """Représente un morceau de document indexé"""
    id: str
    content: str
    document_path: str
    chunk_number: int
    page_number: Optional[int] = None
    metadata: Dict[str, Any] = None
    embedding: Optional[List[float]] = None
    last_updated: Optional[datetime] = None

class FAISSIndex:
    """Gère l'index FAISS pour la recherche vectorielle"""
    
    def __init__(self, dimension: int = 384):
        """Initialise l'index FAISS
        
        Args:
            dimension: Dimension des vecteurs d'embedding
        """
        if dimension <= 0 or dimension > 1024:
            raise ValueError(f"Dimension invalide: {dimension}")
            
        self.dimension = dimension
        self._index = None
        self.document_map: Dict[int, DocumentChunk] = {}
        self.score_manager = DynamicScoreManager()
        self.chunk_selector = ChunkSelector()
        self._embedding_cache = {}  # Cache des embeddings
        self._initialize_index()
        
    def _initialize_index(self):
        """Initialise l'index FAISS avec la dimension spécifiée"""
        # Pour les petites collections (<10k vecteurs), utiliser IndexFlatL2
        # Pour les grandes collections, utiliser IVF avec clustering
        self._index = faiss.IndexFlatL2(self.dimension)
        self._is_ivf = False
        
    def _maybe_convert_to_ivf(self):
        """Convertit l'index en IVF si nécessaire"""
        if not self._is_ivf and self.index.ntotal > 10000:
            logger.info("Converting to IVF index for better performance")
            # Nombre de centroids = sqrt(N)
            n_centroids = int(np.sqrt(self.index.ntotal))
            quantizer = faiss.IndexFlatL2(self.dimension)
            new_index = faiss.IndexIVFFlat(quantizer, self.dimension, n_centroids)
            
            # Copier les vecteurs
            if self.index.ntotal > 0:
                # Récupérer tous les vecteurs
                all_vectors = faiss.vector_to_array(self.index.get_xb())
                all_vectors = all_vectors.reshape(-1, self.dimension)
                
                # Entraîner et ajouter à l'index IVF
                new_index.train(all_vectors)
                new_index.add(all_vectors)
                
            self._index = new_index
            self._is_ivf = True
        
    @property
    def index(self):
        """Accès à l'index FAISS sous-jacent"""
        if self._index is None:
            self._initialize_index()
        return self._index
        
    def resize_index(self, new_dimension: int):
        """Redimensionne l'index pour une nouvelle dimension
        
        Args:
            new_dimension: Nouvelle dimension des vecteurs
            
        Returns:
            Liste des documents à réindexer
        """
        if new_dimension <= 0 or new_dimension > 1024:
            raise ValueError(f"Nouvelle dimension invalide: {new_dimension}")
            
        if new_dimension == self.dimension:
            return []
            
        logger.info(f"Redimensionnement de l'index: {self.dimension} -> {new_dimension}")
        
        # Sauvegarder les documents actuels
        old_documents = list(self.document_map.values())
        
        # Réinitialiser l'index
        self.dimension = new_dimension
        self._index = faiss.IndexFlatL2(new_dimension)
        self.document_map.clear()
        
        return old_documents
        
    @lru_cache(maxsize=1000)
    def _normalize_vector(self, vector_key: str) -> np.ndarray:
        """Normalise un vecteur et le met en cache
        
        Args:
            vector_key: Clé unique du vecteur (hash du contenu)
            
        Returns:
            Vecteur normalisé
        """
        vector = self._embedding_cache.get(vector_key)
        if vector is None:
            return None
        return vector / np.linalg.norm(vector)
        
    def add_document(self, chunk: DocumentChunk, embedding: np.ndarray):
        """Ajoute un document à l'index
        
        Args:
            chunk: Chunk de document à indexer
            embedding: Vecteur d'embedding
        """
        if embedding is None:
            raise ValueError("Embedding invalide (None)")
            
        if len(embedding) != self.dimension:
            raise ValueError(
                f"Dimension de l'embedding ({len(embedding)}) "
                f"!= dimension de l'index ({self.dimension})"
            )
            
        # Stocker l'embedding dans le cache avec une clé str cohérente
        vector_key = str(hash(chunk.content))
        self._embedding_cache[vector_key] = embedding
        
        # Normaliser et ajouter à l'index
        normalized = embedding / np.linalg.norm(embedding)
        if normalized is not None:
            index_id = self.index.ntotal
            self.index.add(normalized.reshape(1, -1))
            
            # Stocker le chunk avec son embedding
            chunk.embedding = embedding.tolist()  # Convertir en liste pour la sérialisation
            chunk_copy = DocumentChunk(
                id=chunk.id,
                content=chunk.content,
                document_path=chunk.document_path,
                chunk_number=chunk.chunk_number,
                page_number=chunk.page_number,
                metadata=chunk.metadata,
                embedding=chunk.embedding,  # Sauvegarder l'embedding
                last_updated=chunk.last_updated
            )
            self.document_map[index_id] = chunk_copy
        
        # Vérifier si on doit convertir en IVF
        self._maybe_convert_to_ivf()
        
    def _safe_float(self, value: Any) -> float:
        """Convertit une valeur en float de manière sécurisée"""
        if value is None:
            return 0.0
            
        try:
            # Conversion directe si possible
            if isinstance(value, (int, float)):
                # Vérifier les valeurs spéciales
                if isinstance(value, float):
                    if value != value or value in (float('inf'), float('-inf')):
                        return 0.0
                return float(value)
                
            # Traitement des chaînes
            if isinstance(value, str):
                value = value.lower().strip()
                # Valeurs spéciales
                if value in ('undefined', 'nan', 'inf', '-inf', ''):
                    return 0.0
                # Nettoyer et convertir
                value = value.replace(',', '.')
                try:
                    return float(value)
                except ValueError:
                    return 0.0
                    
            # Autres types
            try:
                return float(str(value))
            except (ValueError, TypeError):
                return 0.0
                
        except Exception as e:
            logger.debug(f"Erreur conversion float pour {value}: {str(e)}")
            return 0.0

    def _process_similarity_scores(self, similarities: np.ndarray) -> List[float]:
        """Traite les scores de similarité de manière sécurisée"""
        processed_scores = []
        for sim in similarities:
            try:
                # Conversion et nettoyage
                score = self._safe_float(sim)
                # Normalisation entre 0 et 1
                score = max(0.0, min(1.0, score))
                # Arrondir à 4 décimales
                score = round(float(score), 4)
                processed_scores.append(score)
            except Exception as e:
                logger.warning(f"Erreur traitement score {sim}: {str(e)}")
                processed_scores.append(0.0)
        return processed_scores

    def search(self, query_embedding: np.ndarray, k: int = 5) -> List[DocumentChunk]:
        """Recherche les k plus proches voisins"""
        if self.index.ntotal == 0:
            return []
            
        if len(query_embedding) != self.dimension:
            raise ValueError(f"Dimension incorrecte: {len(query_embedding)} != {self.dimension}")
            
        try:
            # Normaliser le vecteur de requête
            query_norm = np.linalg.norm(query_embedding)
            if query_norm == 0:
                logger.warning("Vecteur de requête nul")
                return []
            query_embedding = query_embedding / query_norm
            
            # Recherche
            k_search = min(k * 4, self.index.ntotal)
            if self._is_ivf:
                self._index.nprobe = min(32, int(np.sqrt(self.index.ntotal)))
                
            D, I = self.index.search(query_embedding.reshape(1, -1), k_search)
            
            # Conversion et traitement des scores
            similarities = 1 - (D[0] ** 2) / 2
            processed_scores = self._process_similarity_scores(similarities)
            
            # Récupération des chunks
            results = []
            for idx, score in zip(I[0], processed_scores):
                try:
                    chunk = self.document_map[idx]
                    if chunk.metadata is None:
                        chunk.metadata = {}
                    chunk.metadata['similarity_score'] = score
                    results.append(chunk)
                except Exception as e:
                    logger.warning(f"Erreur récupération chunk {idx}: {str(e)}")
                    continue
                    
            return results[:k]
            
        except Exception as e:
            logger.error(f"Erreur recherche: {str(e)}")
            return []
        
    def save(self, index_path: str, map_path: str):
        """Sauvegarde l'index et la map
        
        Args:
            index_path: Chemin pour sauvegarder l'index FAISS
            map_path: Chemin pour sauvegarder la map des documents
        """
        try:
            # Sauvegarder l'index FAISS
            if self.index.ntotal == 0:
                logger.warning("Tentative de sauvegarde d'un index vide")
            faiss.write_index(self.index, index_path)
            
            # Sauvegarder la map des documents avec les embeddings
            map_data = {
                str(idx): {
                    "id": chunk.id,
                    "content": chunk.content,
                    "document_path": chunk.document_path,
                    "chunk_number": chunk.chunk_number,
                    "page_number": chunk.page_number,
                    "metadata": chunk.metadata,
                    "embedding": chunk.embedding,  # Sauvegarder l'embedding
                    "last_updated": chunk.last_updated.isoformat() if chunk.last_updated else None
                }
                for idx, chunk in self.document_map.items()
            }
            
            with open(map_path, 'w') as f:
                json.dump(map_data, f, indent=2)
                
            logger.info(f"Index sauvegardé avec {self.index.ntotal} vecteurs")
            
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde de l'index: {str(e)}")
            raise
            
    @classmethod
    def load(cls, index_path: str, map_path: str) -> 'FAISSIndex':
        """Charge l'index et la map
        
        Args:
            index_path: Chemin de l'index FAISS
            map_path: Chemin de la map des documents
            
        Returns:
            Instance de FAISSIndex
        """
        try:
            # Charger l'index FAISS
            index = faiss.read_index(index_path)
            instance = cls(dimension=index.d)
            instance._index = index
            
            # Charger la map des documents
            with open(map_path, 'r') as f:
                map_data = json.load(f)
                
            # Restaurer les documents avec leurs embeddings
            instance.document_map = {}
            for idx, data in map_data.items():
                try:
                    chunk = DocumentChunk(
                        id=data["id"],
                        content=data["content"],
                        document_path=data["document_path"],
                        chunk_number=data["chunk_number"],
                        page_number=data["page_number"],
                        metadata=data["metadata"],
                        embedding=data["embedding"],
                        last_updated=datetime.fromisoformat(data["last_updated"]) if data["last_updated"] else None
                    )
                    
                    # Restaurer l'embedding dans le cache
                    if chunk.embedding:
                        vector_key = str(hash(chunk.content))
                        instance._embedding_cache[vector_key] = np.array(chunk.embedding)
                        
                    instance.document_map[int(idx)] = chunk
                except Exception as e:
                    logger.warning(f"Erreur lors du chargement du chunk {idx}: {str(e)}")
                    continue
            
            logger.info(f"Index chargé avec {instance.index.ntotal} vecteurs")
            return instance
            
        except Exception as e:
            logger.error(f"Erreur lors du chargement de l'index: {str(e)}")
            raise

class DocumentIndex:
    """Gère l'indexation et la recherche dans les documents"""
    
    # Dossier pour les fichiers système
    SYSTEM_DIR = ".albert"
    
    def __init__(self, config: Config, webdav_service: WebDAVService):
        self.config = config
        self.webdav = webdav_service
        
        # Validation de la configuration
        if not config.albert_model_embedding:
            raise ValueError("Modèle d'embedding non configuré")
            
        if config.embedding_dimension <= 0 or config.embedding_dimension > 1024:
            raise ValueError(f"Dimension d'embedding invalide: {config.embedding_dimension}")
            
        if config.embedding_batch_size <= 0:
            raise ValueError(f"Taille de batch invalide: {config.embedding_batch_size}")
            
        self.embedding_service = EmbeddingService(config)
        
        # Chemins des fichiers d'index
        self.index_dir = Path(config.webdav_root_path) / self.SYSTEM_DIR
        self.faiss_index_path = str(self.index_dir / "faiss.index")
        self.map_path = str(self.index_dir / "document_map.json")
        self.cache_path = str(self.index_dir / "embedding_cache.json")
        
        # Index FAISS
        self.faiss_index = FAISSIndex(dimension=self.embedding_service.embedding_dimension)
        
    async def initialize(self) -> None:
        """Initialise l'index, le charge s'il existe ou le crée si nécessaire"""
        try:
            # Vérification de la dimension des embeddings
            current_dimension = self.embedding_service.embedding_dimension
            if current_dimension != self.faiss_index.dimension:
                logger.warning(
                    f"Différence de dimension détectée - Index: {self.faiss_index.dimension}, "
                    f"Service: {current_dimension}"
                )
                
                try:
                    # Récupérer les documents existants
                    old_documents = self.faiss_index.resize_index(current_dimension)
                    
                    # Recalculer les embeddings avec la nouvelle dimension
                    for chunk in old_documents:
                        try:
                            embedding = await self.embedding_service.get_embedding(chunk.content)
                            if embedding is not None:
                                self.faiss_index.add_document(chunk, embedding)
                            else:
                                logger.warning(f"Impossible de recalculer l'embedding pour {chunk.id}")
                        except Exception as e:
                            logger.error(f"Erreur lors du recalcul de l'embedding pour {chunk.id}: {str(e)}")
                            continue
                            
                    await self.save_index()
                    logger.info(f"Index redimensionné avec succès: {current_dimension}")
                    
                except Exception as e:
                    logger.error(f"Erreur lors du redimensionnement de l'index: {str(e)}")
                    raise
                    
            # Tentative de chargement de l'index existant
            await self.load_index()
            logger.info("Index chargé avec succès")
            
            # Vérification de la fraîcheur
            is_fresh, missing_docs, extra_docs = await self.verify_index_freshness()
            if not is_fresh:
                logger.info("Index partiellement obsolète, mise à jour nécessaire")
                await self.update_index(missing_docs, extra_docs)
                await self.save_index()
                
        except Exception as e:
            logger.warning(f"Erreur de chargement: {str(e)}, reconstruction...")
            try:
                await self.build_index()
                await self.save_index()
                logger.info("Index reconstruit avec succès")
            except Exception as rebuild_error:
                logger.error(f"Échec de la reconstruction: {str(rebuild_error)}")
                raise
    
    def _is_system_file(self, path: str) -> bool:
        """Détermine si un fichier est un fichier système"""
        normalized_path = path.replace('\\', '/').strip('/')
        
        # Liste des fichiers cachés légitimes à ne pas exclure
        ALLOWED_HIDDEN_FILES = {
            '.gitkeep',
            '.nojekyll',
            '.env.example'
        }
        
        # Vérification de chaque composant du chemin
        path_parts = normalized_path.split('/')
        for part in path_parts:
            # Exclure tous les dossiers/fichiers cachés sauf ceux de la liste autorisée
            if part.startswith('.') and part not in ALLOWED_HIDDEN_FILES:
                return True
                
            # Liste des motifs de fichiers système à ignorer
            system_patterns = [
                "__pycache__",         # Python cache
                ".pytest_cache",       # Pytest cache
                ".venv",              # Environnement virtuel
                "desktop.ini",        # Windows
                ".DS_Store",          # macOS
                "node_modules",       # Node.js
                ".idea",              # IntelliJ
                ".vscode",            # VSCode
                ".cache",             # Cache générique
                "tmp",                # Dossiers temporaires
                ".tmp"                # Fichiers temporaires
            ]
            
            # Vérification des motifs système
            if any(pattern in part for pattern in system_patterns):
                return True
                
        return False

    async def verify_index_freshness(self) -> tuple[bool, set[str], set[str]]:
        """Vérifie si l'index est à jour avec les documents actuels
        
        Returns:
            tuple[bool, set[str], set[str]]: (is_fresh, missing_docs, extra_docs)
        """
        try:
            # Obtenir la liste des documents actuels
            all_docs = await self.webdav.list_documents()
            
            # Filtrer les documents système et créer un ensemble de chemins normalisés
            current_docs = {
                doc for doc in all_docs 
                if not self._is_system_file(doc) and not any(
                    pattern in doc for pattern in [
                        f"/{self.SYSTEM_DIR}/",
                        f"\\{self.SYSTEM_DIR}\\",
                        f"{self.SYSTEM_DIR}/"
                    ]
                )
            }
            
            # Obtenir l'ensemble des documents indexés
            indexed_docs = {
                chunk.document_path 
                for chunk in self.faiss_index.document_map.values()
                if not self._is_system_file(chunk.document_path)
            }
            
            # Vérifier les documents manquants et en trop
            missing_docs = current_docs - indexed_docs
            extra_docs = indexed_docs - current_docs
            
            # Vérifier si les documents manquants sont vides
            if missing_docs:
                empty_docs = set()
                for doc_path in missing_docs.copy():
                    try:
                        content = await self.webdav.read_document(doc_path)
                        if not content or not content.strip():
                            logger.info(f"Document ignoré car vide: {doc_path}")
                            empty_docs.add(doc_path)
                            missing_docs.remove(doc_path)
                    except Exception as e:
                        logger.error(f"Erreur lecture document {doc_path}: {str(e)}")
                        continue
                
                if empty_docs:
                    logger.info(f"Documents vides ignorés: {empty_docs}")
            
            # Vérifier si les ensembles sont identiques
            is_fresh = not missing_docs and not extra_docs
            
            logger.info(f"Documents actuels: {len(current_docs)}, Documents indexés: {len(indexed_docs)}")
            if missing_docs:
                logger.info(f"Documents non indexés: {missing_docs}")
            if extra_docs:
                logger.info(f"Documents indexés en trop: {extra_docs}")
                
            return is_fresh, missing_docs, extra_docs
            
        except Exception as e:
            logger.error(f"Erreur vérification index: {str(e)}")
            return False, set(), set()

    async def _process_document_batch(self, documents: List[str]) -> None:
        """Traite un lot de documents pour l'indexation"""
        empty_docs = set()
        
        for doc_path in documents:
            try:
                # Vérifier si le document est vide
                content = await self.webdav.read_document(doc_path)
                if not content or not content.strip():
                    logger.info(f"Document ignoré car vide: {doc_path}")
                    empty_docs.add(doc_path)
                    continue

                # Pour les fichiers Markdown, on conserve les sauts de ligne
                if doc_path.lower().endswith(('.md', '.markdown')):
                    logger.debug(f"Traitement spécial pour le fichier Markdown: {doc_path}")
                    content = content.replace('\r\n', '\n')

                # Découper le document
                chunks = self._chunk_document(content, doc_path)
                if not chunks:
                    logger.warning(f"Aucun chunk généré pour le document: {doc_path}")
                    empty_docs.add(doc_path)
                    continue
                
                # Obtenir les embeddings pour tous les chunks
                texts = [chunk.content for chunk in chunks]
                try:
                    embeddings = await self.embedding_service.get_embeddings(texts)
                    
                    # Ajouter à l'index FAISS
                    for chunk, embedding in zip(chunks, embeddings):
                        if embedding is not None:  # Vérifier que l'embedding est valide
                            self.faiss_index.add_document(chunk, embedding)
                        else:
                            logger.warning(f"Embedding invalide pour le chunk {chunk.id}")
                            
                    logger.info(f"Document indexé: {doc_path} ({len(chunks)} chunks)")
                    
                except Exception as embed_error:
                    logger.error(f"Erreur d'embedding pour {doc_path}: {str(embed_error)}")
                    continue
                    
            except Exception as doc_error:
                logger.error(f"Erreur indexation {doc_path}: {str(doc_error)}")
                continue

        if empty_docs:
            logger.info(f"Documents vides ignorés: {empty_docs}")
            
    async def update_index(self, documents_to_add: set[str], documents_to_remove: set[str]) -> None:
        """Met à jour l'index en ajoutant et supprimant les documents spécifiés"""
        try:
            # Supprimer les documents obsolètes
            if documents_to_remove:
                logger.info(f"Suppression de {len(documents_to_remove)} documents obsolètes")
                # Créer un nouvel index temporaire
                temp_index = FAISSIndex(dimension=self.embedding_service.embedding_dimension)
                
                # Copier uniquement les documents à conserver
                for idx, chunk in self.faiss_index.document_map.items():
                    if chunk.document_path not in documents_to_remove:
                        temp_index.add_document(chunk, np.array(chunk.embedding))
                
                # Remplacer l'ancien index
                self.faiss_index = temp_index
                logger.info(f"Documents supprimés, {self.faiss_index.index.ntotal} chunks restants")
            
            # Ajouter les nouveaux documents
            if documents_to_add:
                logger.info(f"Ajout de {len(documents_to_add)} nouveaux documents")
                
                # Traiter les documents par lots
                batch_size = 10
                for i in range(0, len(documents_to_add), batch_size):
                    batch = list(documents_to_add)[i:i+batch_size]
                    await self._process_document_batch(batch)
                    
                logger.info(f"Documents ajoutés, {self.faiss_index.index.ntotal} chunks au total")
            
        except Exception as e:
            logger.error(f"Erreur lors de la mise à jour de l'index: {str(e)}")
            raise
            
    async def build_index(self) -> None:
        """Construit l'index à partir de zéro"""
        try:
            # Créer le dossier .albert s'il n'existe pas
            system_dir = str(Path(self.config.webdav_root_path) / self.SYSTEM_DIR)
            if not await self.webdav.exists(system_dir):
                logger.info(f"Création du dossier système {system_dir}")
                if not await self.webdav.create_directory(system_dir):
                    raise RuntimeError(f"Impossible de créer le dossier {system_dir}")
            
            # Réinitialiser l'index
            self.faiss_index = FAISSIndex(dimension=self.embedding_service.embedding_dimension)
            
            # Lister tous les documents et filtrer les fichiers système
            all_docs = await self.webdav.list_documents()
            documents = [
                doc for doc in all_docs 
                if not self._is_system_file(doc) and not any(
                    pattern in doc for pattern in [
                        f"/{self.SYSTEM_DIR}/",
                        f"\\{self.SYSTEM_DIR}\\",
                        f"{self.SYSTEM_DIR}/"
                    ]
                )
            ]
            
            total = len(documents)
            logger.info(f"Construction de l'index pour {total} documents (exclus {len(all_docs) - total} fichiers système)")
            
            # Traiter les documents par lots
            batch_size = 10
            for i in range(0, total, batch_size):
                batch = documents[i:i+batch_size]
                await self._process_document_batch(batch)
                logger.info(f"Progression: {min(i+batch_size, total)}/{total} documents traités")
            
            logger.info(f"Construction terminée: {self.faiss_index.index.ntotal} chunks au total")
            
        except Exception as e:
            logger.error(f"Erreur construction index: {str(e)}")
            raise
    
    async def save_index(self) -> None:
        """Sauvegarde l'index sur WebDAV"""
        try:
            # Créer le répertoire d'index si nécessaire
            os.makedirs(os.path.dirname(self.faiss_index_path), exist_ok=True)
            
            # S'assurer que le dossier système existe sur WebDAV
            system_dir = str(Path(self.config.webdav_root_path) / self.SYSTEM_DIR)
            await self.webdav.create_directory(system_dir)
            
            # Sauvegarder l'index FAISS et la map localement
            self.faiss_index.save(self.faiss_index_path, self.map_path)
            
            # Sauvegarder le cache des embeddings
            self.embedding_service.save_cache(self.cache_path)
            
            # Uploader vers WebDAV
            for path in [self.faiss_index_path, self.map_path, self.cache_path]:
                with open(path, 'rb') as f:
                    remote_path = str(Path(self.config.webdav_root_path) / self.SYSTEM_DIR / Path(path).name)
                    await self.webdav.write_file(remote_path, f.read())
            
            logger.info("Index sauvegardé avec succès")
            
        except Exception as e:
            logger.error(f"Erreur sauvegarde index: {str(e)}")
            raise
    
    async def load_index(self) -> None:
        """Charge l'index depuis le stockage"""
        try:
            # Vérifier que les fichiers existent
            if not await self.webdav.exists(self.faiss_index_path):
                logger.error("Fichier d'index FAISS manquant")
                raise FileNotFoundError("Fichier d'index FAISS manquant")
                
            if not await self.webdav.exists(self.map_path):
                logger.error("Fichier de mapping manquant")
                raise FileNotFoundError("Fichier de mapping manquant")
            
            # Télécharger l'index FAISS d'abord
            try:
                index_data = await self.webdav.download_file(self.faiss_index_path)
                temp_index = "temp_faiss.index"
                with open(temp_index, "wb") as f:
                    f.write(index_data)
                
                # Vérifier l'intégrité de l'index
                try:
                    faiss_index = faiss.read_index(temp_index)
                    if faiss_index.ntotal == 0:
                        logger.warning("Index FAISS vide détecté")
                except Exception as idx_error:
                    logger.error(f"Index FAISS corrompu: {str(idx_error)}")
                    raise
                    
            except Exception as e:
                logger.error(f"Erreur téléchargement index FAISS: {str(e)}")
                raise
            
            # Télécharger la map des documents ensuite
            try:
                map_data = await self.webdav.download_file(self.map_path)
                temp_map = "temp_map.json"
                with open(temp_map, "w") as f:
                    f.write(map_data.decode())
                    
                # Vérifier l'intégrité de la map
                try:
                    with open(temp_map, 'r') as f:
                        map_content = json.load(f)
                    if not isinstance(map_content, dict):
                        raise ValueError("Format de map invalide")
                except json.JSONDecodeError as json_error:
                    logger.error(f"Map JSON corrompue: {str(json_error)}")
                    raise
                except Exception as map_error:
                    logger.error(f"Erreur validation map: {str(map_error)}")
                    raise
                    
            except Exception as e:
                logger.error(f"Erreur téléchargement document map: {str(e)}")
                # Si la map est corrompue mais l'index existe, tenter une reconstruction
                if os.path.exists(temp_index):
                    logger.warning("Map corrompue, tentative de reconstruction...")
                    # Créer un nouvel index avec la même dimension
                    self.faiss_index = FAISSIndex(dimension=self.embedding_service.embedding_dimension)
                    self.faiss_index._index = faiss.read_index(temp_index)
                    self.faiss_index.document_map = {}
                    
                    # Sauvegarder immédiatement le nouvel état
                    await self.save_index()
                    logger.info(f"Index reconstruit avec {self.faiss_index.index.ntotal} vecteurs")
                    return
                raise
                
            try:
                # Charger l'index complet
                self.faiss_index = FAISSIndex.load(temp_index, temp_map)
                
                # Vérifier la cohérence
                if self.faiss_index.index.ntotal == 0:
                    logger.warning("Index chargé est vide")
                elif self.faiss_index.index.ntotal != len(self.faiss_index.document_map):
                    logger.warning(f"Incohérence détectée: {self.faiss_index.index.ntotal} vecteurs mais {len(self.faiss_index.document_map)} documents")
                
                # Vérifier la dimension
                if self.faiss_index.dimension != self.embedding_service.embedding_dimension:
                    raise ValueError(f"Dimension incorrecte: {self.faiss_index.dimension} != {self.embedding_service.embedding_dimension}")
                
                logger.info(f"Index chargé avec succès: {self.faiss_index.index.ntotal} vecteurs et {len(self.faiss_index.document_map)} documents")
                
            finally:
                # Nettoyage
                if os.path.exists(temp_index):
                    os.remove(temp_index)
                if os.path.exists(temp_map):
                    os.remove(temp_map)
                
        except Exception as e:
            logger.error(f"Erreur chargement index: {str(e)}")
            raise
    
    def _chunk_document(self, content: str, doc_path: str) -> List[DocumentChunk]:
        """Découpe un document en chunks en préservant le contexte"""
        chunks = []
        
        # Traitement spécial pour les fichiers Markdown
        if doc_path.lower().endswith(('.md', '.markdown')):
            # Découpage en sections basé sur les titres Markdown
            sections = []
            current_section = []
            current_title = ""
            
            for line in content.split('\n'):
                if line.strip().startswith('#'):
                    if current_section:
                        sections.append((current_title, '\n'.join(current_section).strip()))
                        current_section = []
                    current_title = line.strip()
                current_section.append(line)
            
            if current_section:
                sections.append((current_title, '\n'.join(current_section).strip()))
            
            # Filtrer les sections vides
            sections = [(title, content) for title, content in sections if content.strip()]
            
            if not sections:
                logger.warning(f"Document Markdown vide ou sans contenu valide: {doc_path}")
                return []
                
            logger.debug(f"Découpage du document Markdown {doc_path} en {len(sections)} sections")
            for i, (title, section) in enumerate(sections):
                logger.debug(f"Section {i+1}/{len(sections)} : {title}")
                chunk = DocumentChunk(
                    id=f"{doc_path}_{i}",
                    content=section,
                    document_path=doc_path,
                    chunk_number=i,
                    metadata={
                        "document_name": os.path.basename(doc_path),
                        "total_chunks": len(sections),
                        "is_markdown": True,
                        "section_title": title,
                        "document_title": sections[0][0] if sections else "",
                    },
                    last_updated=datetime.now()
                )
                chunks.append(chunk)
        else:
            # Découpage intelligent pour les PDF et autres documents
            # Détecter si c'est un PDF structuré (avec nos marqueurs)
            is_structured_pdf = '[Page 1]' in content and any(line.strip().startswith('##') for line in content.split('\n'))
            
            if is_structured_pdf:
                # Découpage basé sur les pages et sections avec chevauchement
                current_page = 0
                current_section = []
                current_title = ""
                document_title = ""
                sections = []
                page_content = {}
                
                # Première passe : collecter le contenu par page
                for line in content.split('\n'):
                    if line.strip().startswith('[Page '):
                        current_page = int(line.strip()[6:-1])
                        if current_page not in page_content:
                            page_content[current_page] = []
                    else:
                        if current_page > 0:
                            page_content[current_page].append(line)

                # Deuxième passe : identifier les sections avec leur contexte
                current_page = 0
                for line in content.split('\n'):
                    if line.strip().startswith('[Page '):
                        current_page = int(line.strip()[6:-1])
                        if current_section and current_title:
                            # Ajouter le contexte des pages adjacentes
                            context_before = []
                            context_after = []
                            if current_page > 1 and (current_page-1) in page_content:
                                context_before = page_content[current_page-1][-5:]  # 5 dernières lignes
                            if (current_page+1) in page_content:
                                context_after = page_content[current_page+1][:5]    # 5 premières lignes
                            
                            full_section = []
                            if context_before:
                                full_section.append("--- Contexte de la page précédente ---")
                                full_section.extend(context_before)
                            full_section.extend(current_section)
                            if context_after:
                                full_section.append("--- Contexte de la page suivante ---")
                                full_section.extend(context_after)
                            
                            sections.append((current_title, current_page - 1, '\n'.join(full_section).strip()))
                            current_section = []
                    elif line.strip().startswith('###'):
                        if not document_title:
                            document_title = line.strip()[4:].strip()
                        if current_section and current_title:
                            sections.append((current_title, current_page, '\n'.join(current_section).strip()))
                            current_section = []
                        current_title = line.strip()[4:].strip()
                    elif line.strip().startswith('##'):
                        if current_section and current_title:
                            sections.append((current_title, current_page, '\n'.join(current_section).strip()))
                            current_section = []
                        current_title = line.strip()[3:].strip()
                    elif line.strip().startswith('#'):
                        if current_section and current_title:
                            sections.append((current_title, current_page, '\n'.join(current_section).strip()))
                            current_section = []
                        current_title = line.strip()[2:].strip()
                    else:
                        current_section.append(line)
                
                if current_section and current_title:
                    sections.append((current_title, current_page, '\n'.join(current_section).strip()))
                
                # Créer les chunks avec contexte enrichi
                for i, (title, page, section) in enumerate(sections):
                    if not section.strip():
                        continue
                        
                    # Extraire les positions si présentes
                    positions = []
                    for line in section.split('\n'):
                        if '[pos:' in line:
                            pos_start = line.find('[pos:')
                            pos_end = line.find(']', pos_start)
                            if pos_end > pos_start:
                                positions.append(line[pos_start:pos_end+1])
                    
                    # Ajouter le contexte du document
                    contextualized_content = []
                    contextualized_content.append(f"Document: {os.path.basename(doc_path)}")
                    if document_title:
                        contextualized_content.append(f"Titre du document: {document_title}")
                    contextualized_content.append(f"Section actuelle: {title}")
                    contextualized_content.append(f"Page: {page}")
                    
                    # Ajouter le contexte des sections adjacentes
                    if i > 0:
                        prev_title, prev_page, _ = sections[i-1]
                        contextualized_content.append(f"Section précédente: {prev_title} (Page {prev_page})")
                    if i < len(sections) - 1:
                        next_title, next_page, _ = sections[i+1]
                        contextualized_content.append(f"Section suivante: {next_title} (Page {next_page})")
                    
                    # Ajouter les positions si présentes
                    if positions:
                        contextualized_content.append("Positions dans la page: " + ", ".join(positions))
                    
                    contextualized_content.append("\nContenu:")
                    contextualized_content.append(section)
                    
                    chunk = DocumentChunk(
                        id=f"{doc_path}_{i}",
                        content='\n'.join(contextualized_content),
                        document_path=doc_path,
                        chunk_number=i,
                        page_number=page,
                        metadata={
                            "document_name": os.path.basename(doc_path),
                            "document_title": document_title,
                            "total_chunks": len(sections),
                            "section_title": title,
                            "is_pdf": True,
                            "page": page,
                            "has_position_data": bool(positions),
                            "adjacent_sections": {
                                "previous": sections[i-1][0] if i > 0 else None,
                                "next": sections[i+1][0] if i < len(sections) - 1 else None
                            }
                        },
                        last_updated=datetime.now()
                    )
                    chunks.append(chunk)
            else:
                # Découpage standard en paragraphes avec chevauchement
                paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
                
                if not paragraphs:
                    logger.warning(f"Document vide ou sans contenu valide: {doc_path}")
                    return []
                
                # Créer des chunks avec chevauchement pour préserver le contexte
                overlap = 2  # Nombre de paragraphes de chevauchement
                chunk_size = 5  # Nombre de paragraphes par chunk
                
                for i in range(0, len(paragraphs), chunk_size - overlap):
                    # Prendre chunk_size paragraphes avec chevauchement
                    chunk_paragraphs = paragraphs[i:i + chunk_size]
                    if not chunk_paragraphs:
                        continue
                        
                    # Ajouter le contexte du document
                    contextualized_content = f"Document: {os.path.basename(doc_path)}\n\n"
                    contextualized_content += '\n\n'.join(chunk_paragraphs)
                    
                    chunk = DocumentChunk(
                        id=f"{doc_path}_{i//chunk_size}",
                        content=contextualized_content,
                        document_path=doc_path,
                        chunk_number=i//chunk_size,
                        metadata={
                            "document_name": os.path.basename(doc_path),
                            "total_chunks": (len(paragraphs) + chunk_size - 1) // chunk_size,
                            "start_paragraph": i,
                            "end_paragraph": min(i + chunk_size, len(paragraphs))
                        },
                        last_updated=datetime.now()
                    )
                    chunks.append(chunk)
        
        logger.info(f"Document découpé en {len(chunks)} chunks: {doc_path}")
        return chunks
    
    async def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Recherche les chunks les plus pertinents pour une requête
        
        Args:
            query: Requête de recherche
            limit: Nombre maximum de résultats
            
        Returns:
            Liste des chunks au format attendu par l'API Albert
        """
        try:
            if self.faiss_index.index.ntotal == 0:
                logger.warning("Index vide, impossible d'effectuer la recherche")
                return []

            # Obtenir l'embedding de la requête
            try:
                query_embedding = await self.embedding_service.get_embedding(query)
                if query_embedding is None:
                    logger.error("Impossible d'obtenir l'embedding pour la requête")
                    return []
            except Exception as e:
                logger.error(f"Erreur lors de la génération de l'embedding de recherche: {str(e)}")
                return []

            # Vérifier la dimension de l'embedding
            if len(query_embedding) != self.faiss_index.dimension:
                logger.error(f"Dimension de l'embedding incorrecte: {len(query_embedding)} != {self.faiss_index.dimension}")
                return []

            # Effectuer la recherche avec plus de résultats pour avoir plus de contexte
            chunks = self.faiss_index.search(query_embedding, limit * 2)
            
            # Formater les résultats au format attendu par l'API Albert
            formatted_chunks = []
            for chunk in chunks:
                formatted_chunk = {
                    "id": chunk.id,
                    "content": chunk.content,
                    "metadata": {
                        "document_name": os.path.basename(chunk.document_path),
                        "document_path": chunk.document_path,
                        "chunk_number": chunk.chunk_number,
                        "page_number": chunk.page_number,
                        "similarity_score": chunk.metadata.get('similarity_score', 0),
                        "context_boost": chunk.metadata.get('context_boost', 0),
                        "metadata_boost": chunk.metadata.get('metadata_boost', 0),
                        "adjusted_score": chunk.metadata.get('similarity_score', 0),  # Le score ajusté est déjà dans similarity_score
                        **(chunk.metadata or {})
                    }
                }
                formatted_chunks.append(formatted_chunk)
                
                # Limiter au nombre demandé
                if len(formatted_chunks) >= limit:
                    break
            
            return formatted_chunks
            
        except Exception as e:
            logger.error(f"Erreur lors de la recherche: {str(e)}")
            return []

    async def __aenter__(self):
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            await self.save_index()
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde finale de l'index: {str(e)}")
            raise
