from typing import List, Dict, Any, Optional
from dataclasses import dataclass
import json
from datetime import datetime
import os
from pathlib import Path
import asyncio
import faiss
import numpy as np

from matrix_bot.config import logger
from config import Config
from services.webdav import WebDAVService
from services.embedding_service import EmbeddingService

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
        self.dimension = dimension
        self.index = faiss.IndexFlatL2(dimension)
        self.document_map: Dict[int, DocumentChunk] = {}
        
    def add_document(self, chunk: DocumentChunk, embedding: np.ndarray):
        """Ajoute un document à l'index"""
        index_id = self.index.ntotal
        self.index.add(embedding.reshape(1, -1))
        self.document_map[index_id] = chunk
        
    def search(self, query_embedding: np.ndarray, k: int = 5) -> List[DocumentChunk]:
        """Recherche les k plus proches voisins"""
        if self.index.ntotal == 0:
            return []
            
        D, I = self.index.search(query_embedding.reshape(1, -1), min(k, self.index.ntotal))
        return [self.document_map[i] for i in I[0]]
        
    def save(self, index_path: str, map_path: str):
        """Sauvegarde l'index et la map"""
        # Sauvegarder l'index FAISS
        faiss.write_index(self.index, index_path)
        
        # Sauvegarder la map des documents
        map_data = {
            str(idx): {
                "id": chunk.id,
                "content": chunk.content,
                "document_path": chunk.document_path,
                "chunk_number": chunk.chunk_number,
                "page_number": chunk.page_number,
                "metadata": chunk.metadata,
                "last_updated": chunk.last_updated.isoformat() if chunk.last_updated else None
            }
            for idx, chunk in self.document_map.items()
        }
        with open(map_path, 'w') as f:
            json.dump(map_data, f, indent=2)
            
    @classmethod
    def load(cls, index_path: str, map_path: str) -> 'FAISSIndex':
        """Charge l'index et la map"""
        instance = cls()
        
        # Charger l'index FAISS
        instance.index = faiss.read_index(index_path)
        
        # Charger la map des documents
        with open(map_path, 'r') as f:
            map_data = json.load(f)
            
        instance.document_map = {
            int(idx): DocumentChunk(
                id=data["id"],
                content=data["content"],
                document_path=data["document_path"],
                chunk_number=data["chunk_number"],
                page_number=data["page_number"],
                metadata=data["metadata"],
                last_updated=datetime.fromisoformat(data["last_updated"]) if data["last_updated"] else None
            )
            for idx, data in map_data.items()
        }
        
        return instance

class DocumentIndex:
    """Gère l'indexation et la recherche dans les documents"""
    
    # Dossier pour les fichiers système
    SYSTEM_DIR = ".albert"
    
    def __init__(self, config: Config, webdav_service: WebDAVService):
        self.config = config
        self.webdav = webdav_service
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
            # Tentative de chargement de l'index existant
            await self.load_index()
            logger.info("Index chargé avec succès")
            
            # Vérification de la fraîcheur
            is_fresh, missing_docs, extra_docs = await self.verify_index_freshness()
            if is_fresh:
                logger.info("Index à jour")
            else:
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
        
        # Liste des motifs de fichiers système à ignorer
        system_patterns = [
            f"{self.SYSTEM_DIR}/",  # Dossier .albert
            f"{self.SYSTEM_DIR}\\", # Variante Windows
            "/.git/",               # Git
            "\\.git\\",            # Git (Windows)
            "/__pycache__/",        # Python cache
            "/.pytest_cache/",      # Pytest cache
            "/.venv/",              # Environnement virtuel
            "/.env",                # Fichiers de configuration
            "/.gitignore",          # Git ignore
            "/desktop.ini",         # Windows
            "/.DS_Store"            # macOS
        ]
        
        # Vérifier si le chemin commence par ou contient un motif système
        for pattern in system_patterns:
            if normalized_path.startswith(pattern) or f"/{normalized_path}" == pattern or pattern in normalized_path.split('/'):
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

    async def update_index(self, documents_to_add: set[str], documents_to_remove: set[str]) -> None:
        """Met à jour l'index en ajoutant et supprimant les documents spécifiés"""
        try:
            # Supprimer les documents obsolètes
            if documents_to_remove:
                logger.info(f"Suppression de {len(documents_to_remove)} documents de l'index")
                new_document_map = {}
                for idx, chunk in self.faiss_index.document_map.items():
                    if chunk.document_path not in documents_to_remove:
                        new_document_map[idx] = chunk
                self.faiss_index.document_map = new_document_map
                
            # Ajouter les nouveaux documents
            if documents_to_add:
                logger.info(f"Ajout de {len(documents_to_add)} documents à l'index")
                empty_docs = set()
                for doc_path in documents_to_add:
                    try:
                        # Vérifier si le document est vide avant de tenter l'indexation
                        content = await self.webdav.read_document(doc_path)
                        if not content or not content.strip():
                            logger.info(f"Document ignoré car vide: {doc_path}")
                            empty_docs.add(doc_path)
                            continue

                        # Découper le document
                        chunks = self._chunk_document(content, doc_path)
                        if not chunks:
                            logger.warning(f"Aucun chunk généré pour le document: {doc_path}")
                            empty_docs.add(doc_path)
                            continue
                            
                        # Obtenir les embeddings pour les chunks
                        texts = [chunk.content for chunk in chunks]
                        embeddings = await self.embedding_service.get_embeddings(texts)
                        
                        # Ajouter à l'index FAISS
                        for chunk, embedding in zip(chunks, embeddings):
                            chunk.embedding = embedding.tolist()
                            self.faiss_index.add_document(chunk, embedding)
                            
                        logger.info(f"Document indexé: {doc_path} ({len(chunks)} chunks)")
                        
                    except Exception as doc_error:
                        logger.error(f"Erreur indexation {doc_path}: {str(doc_error)}")
                        continue

                if empty_docs:
                    logger.info(f"Documents vides ignorés lors de la mise à jour: {empty_docs}")
                        
            logger.info(f"Mise à jour terminée: {self.faiss_index.index.ntotal} chunks au total")
            
        except Exception as e:
            logger.error(f"Erreur mise à jour index: {str(e)}")
            raise
    
    async def build_index(self) -> None:
        """Reconstruit l'index complet"""
        try:
            # Réinitialiser l'index
            self.faiss_index = FAISSIndex(dimension=self.embedding_service.embedding_dimension)
            
            # Lister tous les documents
            all_documents = await self.webdav.list_documents()
            
            # Filtrer les documents système de manière plus stricte
            documents = []
            excluded_docs = []
            
            for doc in all_documents:
                # Vérifier si le document est dans un dossier système
                if self._is_system_file(doc) or '.albert' in doc.split('/'):
                    excluded_docs.append(doc)
                    continue
                documents.append(doc)
            
            total = len(documents)
            logger.info(f"Début de l'indexation de {total} documents")
            logger.info(f"Documents exclus ({len(excluded_docs)}): {', '.join(excluded_docs)}")
            
            empty_docs = set()
            indexed_count = 0
            
            for i, doc_path in enumerate(documents, 1):
                try:
                    # Vérifier si le document est vide
                    content = await self.webdav.read_document(doc_path)
                    if not content or not content.strip():
                        logger.info(f"Document ignoré car vide ({i}/{total}): {doc_path}")
                        empty_docs.add(doc_path)
                        continue

                    # Pour les fichiers Markdown, on conserve les sauts de ligne pour préserver la structure
                    if doc_path.lower().endswith(('.md', '.markdown')):
                        logger.debug(f"Traitement spécial pour le fichier Markdown: {doc_path}")
                        content = content.replace('\r\n', '\n')  # Normalisation des sauts de ligne

                    # Découper le document
                    chunks = self._chunk_document(content, doc_path)
                    if not chunks:
                        logger.warning(f"Aucun chunk généré pour le document ({i}/{total}): {doc_path}")
                        empty_docs.add(doc_path)
                        continue
                    
                    # Obtenir les embeddings pour tous les chunks
                    texts = [chunk.content for chunk in chunks]
                    embeddings = await self.embedding_service.get_embeddings(texts)
                    
                    # Ajouter à l'index FAISS
                    for chunk, embedding in zip(chunks, embeddings):
                        self.faiss_index.add_document(chunk, embedding)
                    
                    indexed_count += 1
                    logger.info(f"Document indexé ({i}/{total}): {doc_path} ({len(chunks)} chunks)")
                    
                except Exception as doc_error:
                    logger.error(f"Erreur indexation {doc_path}: {str(doc_error)}")
                    continue
            
            if empty_docs:
                logger.info(f"Documents vides ignorés ({len(empty_docs)}): {empty_docs}")
            
            logger.info(f"Indexation terminée: {indexed_count} documents indexés, {self.faiss_index.index.ntotal} chunks au total")
            
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
        """Charge l'index depuis WebDAV"""
        try:
            # Créer le répertoire d'index si nécessaire
            os.makedirs(os.path.dirname(self.faiss_index_path), exist_ok=True)
            
            # S'assurer que le dossier système existe sur WebDAV
            system_dir = str(Path(self.config.webdav_root_path) / self.SYSTEM_DIR)
            await self.webdav.create_directory(system_dir)
            
            # Télécharger les fichiers depuis WebDAV
            for local_path, remote_name in [
                (self.faiss_index_path, "faiss.index"),
                (self.map_path, "document_map.json"),
                (self.cache_path, "embedding_cache.json")
            ]:
                remote_path = str(Path(self.config.webdav_root_path) / self.SYSTEM_DIR / remote_name)
                content = await self.webdav.read_document(remote_path)
                with open(local_path, 'wb') as f:
                    f.write(content.encode() if isinstance(content, str) else content)
            
            # Charger l'index FAISS et la map
            self.faiss_index = FAISSIndex.load(self.faiss_index_path, self.map_path)
            
            # Charger le cache des embeddings
            self.embedding_service.load_cache(self.cache_path)
            
            logger.info(f"Index chargé avec {self.faiss_index.index.ntotal} chunks")
            
        except Exception as e:
            logger.error(f"Erreur chargement index: {str(e)}")
            raise
    
    def _chunk_document(self, content: str, doc_path: str) -> List[DocumentChunk]:
        """Découpe un document en chunks"""
        chunks = []
        
        # Traitement spécial pour les fichiers Markdown
        if doc_path.lower().endswith(('.md', '.markdown')):
            # Découpage en sections basé sur les titres Markdown
            sections = []
            current_section = []
            
            for line in content.split('\n'):
                if line.strip().startswith('#') or line.strip().startswith('🚀') or line.strip().startswith('📜'):
                    if current_section:
                        sections.append('\n'.join(current_section).strip())
                        current_section = []
                current_section.append(line)
            
            if current_section:
                sections.append('\n'.join(current_section).strip())
            
            # Filtrer les sections vides
            sections = [s for s in sections if s.strip()]
            
            if not sections:
                logger.warning(f"Document Markdown vide ou sans contenu valide: {doc_path}")
                return []
                
            logger.debug(f"Découpage du document Markdown {doc_path} en {len(sections)} sections")
            for i, section in enumerate(sections):
                logger.debug(f"Section {i+1}/{len(sections)} : {section[:100]}...")
                chunk = DocumentChunk(
                    id=f"{doc_path}_{i}",
                    content=section,
                    document_path=doc_path,
                    chunk_number=i,
                    metadata={
                        "document_name": os.path.basename(doc_path),
                        "total_chunks": len(sections),
                        "is_markdown": True
                    },
                    last_updated=datetime.now()
                )
                chunks.append(chunk)
        else:
            # Découpage standard en paragraphes pour les autres types de documents
            paragraphs = [p.strip() for p in content.split('\n\n') if p.strip()]
            
            if not paragraphs:
                logger.warning(f"Document vide ou sans contenu valide: {doc_path}")
                return []
                
            logger.debug(f"Découpage du document {doc_path} en {len(paragraphs)} paragraphes")
            for i, para in enumerate(paragraphs):
                logger.debug(f"Paragraphe {i+1}/{len(paragraphs)} : {para[:100]}...")
                chunk = DocumentChunk(
                    id=f"{doc_path}_{i}",
                    content=para,
                    document_path=doc_path,
                    chunk_number=i,
                    metadata={
                        "document_name": os.path.basename(doc_path),
                        "total_chunks": len(paragraphs)
                    },
                    last_updated=datetime.now()
                )
                chunks.append(chunk)
        
        logger.info(f"Document découpé en {len(chunks)} chunks: {doc_path}")
        return chunks
    
    async def search(self, query: str, limit: int = 5) -> List[DocumentChunk]:
        """Recherche les chunks les plus pertinents pour une requête"""
        try:
            if self.faiss_index.index.ntotal == 0:
                logger.warning("Index vide, impossible d'effectuer la recherche")
                return []

            # Obtenir l'embedding de la requête avec le même modèle que l'index
            query_embedding = await self.embedding_service.get_embedding(query)
            if query_embedding is None:
                logger.error("Impossible d'obtenir l'embedding pour la requête")
                return []

            # Vérifier la dimension de l'embedding
            if len(query_embedding) != self.faiss_index.dimension:
                logger.error(f"Dimension de l'embedding incorrecte: {len(query_embedding)} != {self.faiss_index.dimension}")
                logger.info("Reconstruction de l'index avec le nouveau modèle...")
                
                # Sauvegarder la nouvelle dimension
                self.faiss_index = FAISSIndex(dimension=len(query_embedding))
                
                # Reconstruire l'index
                await self.build_index()
                
                # Réessayer la recherche avec le nouvel index
                return await self.search(query, limit)

            # Rechercher dans l'index FAISS
            try:
                results = self.faiss_index.search(query_embedding, limit)
                if not results:
                    logger.info("Aucun résultat trouvé pour la requête")
                    return []
                    
                # Log des résultats pour debug
                logger.debug(f"Résultats trouvés: {len(results)} chunks")
                for i, chunk in enumerate(results):
                    logger.debug(f"Résultat {i+1}: {chunk.document_path} - {chunk.content[:100]}...")
                    
                return results
            except Exception as search_error:
                logger.error(f"Erreur lors de la recherche FAISS: {str(search_error)}")
                return []
            
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
