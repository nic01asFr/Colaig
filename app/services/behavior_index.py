from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import json
import numpy as np
import faiss
import traceback
import asyncio
from functools import lru_cache

from app.matrix_bot.config import logger
from app.config import Config
from app.services.webdav import WebDAVService

class BehaviorPriority:
    """Constantes de priorité pour les comportements"""
    LOW = 0.2
    MEDIUM = 0.5
    HIGH = 0.8
    CRITICAL = 1.0
    
    @staticmethod
    def validate(priority: float) -> float:
        """Valide et normalise une priorité"""
        if not isinstance(priority, (int, float)):
            raise ValueError("La priorité doit être un nombre")
        priority = float(priority)
        return max(0.0, min(1.0, priority))

@dataclass
class BehaviorChunk:
    """Représente un comportement indexé"""
    id: str
    content: str
    behavior_type: str
    priority: float = BehaviorPriority.MEDIUM
    metadata: Dict[str, Any] = None
    embedding: Optional[List[float]] = None
    last_updated: Optional[datetime] = None
    
    def __post_init__(self):
        """Validation après initialisation"""
        self.priority = BehaviorPriority.validate(self.priority)
        if self.behavior_type not in ["actions", "tools", "prompts", "rules"]:
            raise ValueError(f"Type de comportement invalide: {self.behavior_type}")
        if self.metadata is None:
            self.metadata = {}

class BehaviorFAISSIndex:
    """Gère l'index FAISS spécifique aux behaviors"""
    
    def __init__(self, dimension: int = 384):
        self.dimension = dimension
        self._index = faiss.IndexFlatL2(dimension)
        self.behavior_map: Dict[int, BehaviorChunk] = {}
        
    def add_behavior(self, chunk: BehaviorChunk, embedding: np.ndarray) -> None:
        """Ajoute un behavior à l'index"""
        if embedding is None or len(embedding) != self.dimension:
            raise ValueError(f"Dimension de l'embedding invalide: {len(embedding) if embedding is not None else None}")
            
        # Normaliser et ajouter à l'index
        normalized = embedding / np.linalg.norm(embedding)
        index_id = self._index.ntotal
        self._index.add(normalized.reshape(1, -1))
        
        # Stocker le behavior
        self.behavior_map[index_id] = chunk
        
    def search(self, query_embedding: np.ndarray, k: int = 5) -> List[BehaviorChunk]:
        """Recherche les behaviors les plus similaires"""
        if self._index.ntotal == 0:
            return []
            
        # Normaliser la requête
        query_norm = np.linalg.norm(query_embedding)
        if query_norm == 0:
            return []
        query_embedding = query_embedding / query_norm
        
        # Recherche
        D, I = self._index.search(query_embedding.reshape(1, -1), k)
        
        # Traiter les résultats
        results = []
        similarities = 1 - (D[0] ** 2) / 2
        
        for idx, score in zip(I[0], similarities):
            try:
                chunk = self.behavior_map[idx]
                chunk.metadata['similarity_score'] = float(score)
                results.append(chunk)
            except Exception as e:
                logger.warning(f"Erreur récupération behavior {idx}: {str(e)}")
                continue
                
        return results

class BehaviorIndex:
    """Gère l'indexation et la recherche des behaviors"""
    
    # Constante pour le répertoire des comportements
    BEHAVIOR_DIR = "./behaviors"
    
    def __init__(self, config: Config, webdav_service: WebDAVService):
        self.config = config
        self.webdav = webdav_service
        
        # Gestion de l'absence de service WebDAV
        if self.webdav is None:
            logger.warning("Aucun service WebDAV fourni à BehaviorIndex, utilisation des valeurs par défaut")
            # Utiliser le chemin de configuration si disponible, sinon la constante par défaut
            self.index_dir = Path(getattr(self.config, "behavior_path", self.BEHAVIOR_DIR))
            self.local_mode = True
            logger.info(f"BehaviorIndex en mode local avec index_dir={self.index_dir}")
        else:
            # Récupérer le BEHAVIOR_DIR depuis plusieurs sources possibles
            behavior_dir = None
            
            # 1. Essayer d'utiliser l'attribut BEHAVIOR_DIR du service WebDAV
            if hasattr(self.webdav, 'BEHAVIOR_DIR'):
                behavior_dir = getattr(self.webdav, 'BEHAVIOR_DIR')
                logger.debug(f"Utilisation du BEHAVIOR_DIR du service WebDAV: {behavior_dir}")
            
            # 2. Sinon, essayer d'utiliser le behavior_path de la config
            elif hasattr(self.config, 'behavior_path'):
                behavior_dir = self.config.behavior_path
                logger.debug(f"Utilisation du behavior_path de la config: {behavior_dir}")
            
            # 3. En dernier recours, utiliser la constante par défaut
            else:
                behavior_dir = self.BEHAVIOR_DIR
                logger.debug(f"Utilisation du BEHAVIOR_DIR par défaut: {behavior_dir}")
            
            self.index_dir = Path(behavior_dir)
            self.local_mode = False
            logger.info(f"BehaviorIndex en mode WebDAV avec index_dir={self.index_dir}")
            
        self.faiss_index_path = str(self.index_dir / "behavior.faiss")
        self.map_path = str(self.index_dir / "behavior_map.json")
        self._topic_cache = {}
        self._index = BehaviorFAISSIndex(dimension=config.embedding_dimension)
        self._embedding_service = None  # Instance réutilisable du service d'embedding
        self._use_random_embeddings = False  # Flag pour le mode dégradé
        self._init_lock = asyncio.Lock()  # Pour éviter les initialisations concurrentes
        
    async def initialize(self) -> None:
        """Initialise l'index des behaviors"""
        async with self._init_lock:
            try:
                # Vérifier/créer le dossier système
                if self.webdav:
                    # Mode WebDAV
                    if not await self.webdav.exists(str(self.index_dir)):
                        logger.info(f"Création du répertoire WebDAV {self.index_dir}")
                        success = await self.webdav.create_directory(str(self.index_dir))
                        if not success:
                            logger.warning(f"Impossible de créer le répertoire {self.index_dir} sur WebDAV")
                            logger.info("Continuation en mode dégradé avec des valeurs par défaut")
                else:
                    # Mode local
                    logger.info(f"Mode local: vérification du répertoire {self.index_dir}")
                    os.makedirs(str(self.index_dir), exist_ok=True)
                    logger.info(f"Répertoire local vérifié/créé: {self.index_dir}")
                    
                # Créer les sous-répertoires pour les différents types de comportements
                behavior_types = ["actions", "tools", "prompts", "rules"]
                for behavior_type in behavior_types:
                    dir_path = os.path.join(str(self.index_dir), behavior_type)
                    if self.webdav:
                        if not await self.webdav.exists(dir_path):
                            logger.info(f"Création du sous-répertoire {behavior_type}")
                            await self.webdav.create_directory(dir_path)
                    else:
                        os.makedirs(dir_path, exist_ok=True)
                        logger.info(f"Sous-répertoire local vérifié/créé: {behavior_type}")
                    
                # Charger l'index existant ou en créer un nouveau
                index_exists = False
                if self.webdav:
                    index_exists = await self.webdav.exists(self.faiss_index_path)
                else:
                    index_exists = Path(self.faiss_index_path).exists()
                    
                if index_exists:
                    logger.info("Index comportemental existant trouvé, chargement...")
                    try:
                        await self._load_index()
                        logger.info("Index comportemental chargé avec succès")
                    except Exception as e:
                        logger.error(f"Erreur chargement index existant: {str(e)}")
                        logger.info("Construction d'un nouvel index...")
                        await self._build_index()
                else:
                    logger.info("Aucun index existant trouvé, construction d'un nouvel index...")
                    await self._build_index()
                    
                logger.info("Index comportemental initialisé")
                
            except Exception as e:
                logger.error(f"Erreur initialisation index comportemental: {str(e)}")
                logger.error(traceback.format_exc())
                logger.info("Tentative de continuation avec comportements minimaux par défaut...")
                try:
                    # Créer un index minimal par défaut en mémoire pour éviter le blocage
                    self._index = BehaviorFAISSIndex(dimension=self.config.embedding_dimension)
                    # Pas d'erreur fatale, pour permettre au bot de démarrer
                except Exception as inner_e:
                    logger.error(f"Erreur création index minimal: {str(inner_e)}")

    async def _get_embedding_service(self) -> Any:
        """Obtient ou crée une instance d'EmbeddingService réutilisable"""
        if self._use_random_embeddings:
            return None  # En mode dégradé, on n'utilise pas le service d'embedding
            
        if self._embedding_service is None:
            try:
                logger.debug("Import du service d'embedding...")
                # Dynamically import to avoid circular imports
                from app.services.embedding_service import EmbeddingService
                logger.debug("Création de l'instance EmbeddingService...")
                self._embedding_service = EmbeddingService(self.config)
                logger.debug("Instance EmbeddingService créée avec succès")
            except Exception as e:
                logger.error(f"Erreur création EmbeddingService: {str(e)}")
                logger.error(traceback.format_exc())
                self._use_random_embeddings = True  # Passer en mode dégradé
                return None
                
        return self._embedding_service

    async def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Obtient l'embedding d'un texte avec gestion des timeouts"""
        # Si mode dégradé actif, retourner directement un embedding aléatoire
        if self._use_random_embeddings:
            logger.debug("Mode dégradé: génération d'embedding aléatoire")
            random_embedding = np.random.rand(self.config.embedding_dimension)
            return random_embedding / np.linalg.norm(random_embedding)
            
        logger.info("Tentative d'obtention d'embedding")
        try:
            # Récupérer ou créer le service d'embedding
            embedding_service = await self._get_embedding_service()
            if embedding_service is None:
                # Fallback sur embedding aléatoire si le service n'est pas disponible
                logger.warning("Service d'embedding non disponible, utilisation d'embedding aléatoire")
                random_embedding = np.random.rand(self.config.embedding_dimension)
                return random_embedding / np.linalg.norm(random_embedding)
            
            # Appel avec timeout explicite
            try:
                embedding_task = embedding_service.get_embedding(text)
                embedding = await asyncio.wait_for(embedding_task, timeout=10.0)
                
                if embedding is None:
                    logger.warning("Embedding nul reçu, génération d'un embedding aléatoire")
                    random_embedding = np.random.rand(self.config.embedding_dimension)
                    return random_embedding / np.linalg.norm(random_embedding)
                    
                logger.info("Embedding obtenu avec succès")
                return np.array(embedding) if embedding else None
                
            except asyncio.TimeoutError:
                logger.error("Timeout lors de la récupération de l'embedding")
                # En cas de timeout, générer un embedding aléatoire
                random_embedding = np.random.rand(self.config.embedding_dimension)
                return random_embedding / np.linalg.norm(random_embedding)
                
        except Exception as e:
            logger.error(f"Erreur génération embedding: {str(e)}")
            logger.error(traceback.format_exc())
            # En cas d'erreur, générer un embedding aléatoire
            random_embedding = np.random.rand(self.config.embedding_dimension)
            return random_embedding / np.linalg.norm(random_embedding)

    async def _build_index(self) -> None:
        """Construit l'index à partir des fichiers de configuration"""
        try:
            # Réinitialiser l'index
            self._index = BehaviorFAISSIndex(dimension=self.config.embedding_dimension)
            
            # Collecter tous les contenus et métadonnées pour traitement par lots
            all_contents = []  # Contenu des fichiers
            content_mappings = []  # Chunks correspondants
            
            # Parcourir les dossiers de behavior
            for behavior_type in ["actions", "tools", "prompts", "rules"]:
                type_dir = os.path.join(str(self.index_dir), behavior_type)
                if self.webdav:
                    if not await self.webdav.exists(type_dir):
                        logger.info(f"Création du dossier manquant: {type_dir}")
                        await self.webdav.create_directory(type_dir)
                        continue
                    
                    # Lister les fichiers de configuration
                    files = await self.webdav.list_documents(type_dir)
                    for file_path in files:
                        if not file_path.endswith('.json'):
                            continue
                            
                        try:
                            # Charger la configuration
                            content = await self.webdav.read_document(file_path)
                            config_data = json.loads(content)
                            
                            # Créer le chunk
                            chunk = BehaviorChunk(
                                id=os.path.splitext(os.path.basename(file_path))[0],
                                content=content,
                                behavior_type=behavior_type,
                                priority=config_data.get("priority", BehaviorPriority.MEDIUM),
                                metadata=config_data.get("metadata", {}),
                                last_updated=datetime.now()
                            )
                            
                            # Ajouter à la liste pour traitement en batch
                            all_contents.append(content)
                            content_mappings.append(chunk)
                            
                        except Exception as e:
                            logger.warning(f"Erreur indexation {file_path}: {str(e)}")
                            continue
                else:
                    # Mode local
                    if not os.path.exists(type_dir):
                        logger.info(f"Création du dossier local manquant: {type_dir}")
                        os.makedirs(type_dir, exist_ok=True)
                        continue
                        
                    # Lister les fichiers de configuration locaux
                    files = [os.path.join(type_dir, f) for f in os.listdir(type_dir) if f.endswith('.json')]
                    for file_path in files:
                        try:
                            # Charger la configuration
                            with open(file_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                            config_data = json.loads(content)
                            
                            # Créer le chunk
                            chunk = BehaviorChunk(
                                id=os.path.splitext(os.path.basename(file_path))[0],
                                content=content,
                                behavior_type=behavior_type,
                                priority=config_data.get("priority", BehaviorPriority.MEDIUM),
                                metadata=config_data.get("metadata", {}),
                                last_updated=datetime.now()
                            )
                            
                            # Ajouter à la liste pour traitement en batch
                            all_contents.append(content)
                            content_mappings.append(chunk)
                        except Exception as e:
                            logger.warning(f"Erreur indexation locale {file_path}: {str(e)}")
                            continue
                    
            # Traiter tous les embeddings en une fois si possible
            if not self._use_random_embeddings and all_contents:
                try:
                    # Récupérer le service d'embedding
                    embedding_service = await self._get_embedding_service()
                    
                    if embedding_service:
                        logger.info(f"Génération en batch de {len(all_contents)} embeddings...")
                        
                        try:
                            # Utiliser un timeout pour le traitement par lot
                            batch_task = embedding_service.get_embeddings(all_contents)
                            embeddings = await asyncio.wait_for(
                                batch_task,
                                timeout=max(30.0, 5.0 * len(all_contents))  # Timeout adaptatif
                            )
                            
                            # Associer les embeddings aux chunks
                            for i, (embedding, chunk) in enumerate(zip(embeddings, content_mappings)):
                                if embedding is not None and np.linalg.norm(embedding) >= 1e-8:
                                    try:
                                        self._index.add_behavior(chunk, embedding)
                                    except Exception as e:
                                        logger.warning(f"Erreur ajout behavior {i} à l'index: {str(e)}")
                                else:
                                    # Utiliser un embedding aléatoire en cas d'échec
                                    random_embedding = np.random.rand(self.config.embedding_dimension)
                                    random_embedding = random_embedding / np.linalg.norm(random_embedding)
                                    self._index.add_behavior(chunk, random_embedding)
                            
                            logger.info(f"Génération batch terminée avec succès")
                            
                        except asyncio.TimeoutError:
                            logger.error(f"Timeout lors de la génération batch des embeddings")
                            self._use_random_embeddings = True
                        except Exception as e:
                            logger.error(f"Erreur génération batch des embeddings: {str(e)}")
                            self._use_random_embeddings = True
                    else:
                        self._use_random_embeddings = True
                        
                except Exception as e:
                    logger.error(f"Erreur récupération du service d'embedding: {str(e)}")
                    self._use_random_embeddings = True
            
            # Si mode dégradé ou échec du batch, utiliser des embeddings aléatoires
            if self._use_random_embeddings:
                logger.warning("Utilisation d'embeddings aléatoires pour tous les comportements")
                for chunk in content_mappings:
                    random_embedding = np.random.rand(self.config.embedding_dimension)
                    random_embedding = random_embedding / np.linalg.norm(random_embedding)
                    self._index.add_behavior(chunk, random_embedding)
            
            # Vérifier si l'index est vide après tentative de construction
            if self._index._index.ntotal == 0:
                logger.warning("Aucun comportement trouvé dans l'index, construction de l'index par défaut")
                await self._build_default_index()
            else:
                # Sauvegarder l'index
                await self._save_index()
                logger.info(f"Index comportemental construit avec {self._index._index.ntotal} behaviors")
            
        except Exception as e:
            logger.error(f"Erreur construction index: {str(e)}")
            logger.error(traceback.format_exc())
            # Fallback en cas d'erreur, utiliser l'index par défaut
            await self._build_default_index()

    async def _build_default_index(self) -> None:
        """Construit un index par défaut avec les comportements essentiels"""
        try:
            # Réinitialiser l'index
            self._index = BehaviorFAISSIndex(dimension=self.config.embedding_dimension)
            
            # Créer les comportements essentiels par défaut
            default_behaviors = {
                "actions": {
                    "standard_rag.json": {
                        "type": "action",
                        "name": "Standard RAG",
                        "description": "Action RAG standard pour les requêtes générales",
                        "priority": 1.0,
                        "configuration": {
                            "search_params": {
                                "include_behavior": True,
                                "include_documents": True,
                                "limit": 10
                            },
                            "response_generation": {
                                "model": self.config.albert_model,
                                "embedding_model": self.config.albert_model_embedding,
                                "max_history": 2
                            }
                        },
                        "metadata": {
                            "associated_tools": "search, context",
                            "associated_prompts": "rag_system"
                        }
                    }
                },
                "tools": {
                    "search.json": {
                        "type": "tool",
                        "name": "search",
                        "description": "Outil de recherche dans les documents",
                        "priority": 0.8,
                        "configuration": {
                            "endpoint": "/search",
                            "parameters": ["query", "limit", "collection"]
                        }
                    },
                    "context.json": {
                        "type": "tool",
                        "name": "context",
                        "description": "Outil de gestion du contexte de conversation",
                        "priority": 0.7,
                        "configuration": {
                            "history_size": 10,
                            "cache_duration": 3600
                        }
                    }
                },
                "prompts": {
                    "rag_system.json": {
                        "type": "prompt",
                        "name": "rag_system",
                        "description": "Prompt système pour les requêtes RAG",
                        "priority": 0.9,
                        "content": "Vous êtes Colaig, un assistant IA. Utilisez les informations fournies pour répondre de manière concise et précise.",
                        "style": "formal"
                    }
                },
                "rules": {
                    "default.json": {
                        "type": "rule",
                        "name": "default",
                        "description": "Règle par défaut pour le traitement des requêtes",
                        "priority": 1.0,
                        "conditions": ["*"],
                        "actions": ["standard_rag"]
                    }
                }
            }
            
            # Ajouter les comportements par défaut à l'index
            for behavior_type, behaviors in default_behaviors.items():
                for filename, behavior_data in behaviors.items():
                    # Créer le chemin complet
                    file_path = os.path.join(str(self.index_dir), behavior_type, filename)
                    behavior_json = json.dumps(behavior_data, indent=2)
                    
                    # Créer le fichier de comportement
                    if self.webdav:
                        await self.webdav.write_file(file_path, behavior_json)
                    else:
                        os.makedirs(os.path.dirname(file_path), exist_ok=True)
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(behavior_json)
                    
                    # Créer le chunk
                    chunk = BehaviorChunk(
                        id=os.path.splitext(filename)[0],
                        content=behavior_json,
                        behavior_type=behavior_type,
                        priority=behavior_data.get("priority", 0.5),
                        metadata=behavior_data.get("metadata", {}),
                        last_updated=datetime.now()
                    )
                    
                    # Créer un embedding factice (vecteur aléatoire) pour les comportements par défaut
                    # Cela sera remplacé lors de la prochaine indexation complète
                    random_embedding = np.random.rand(self.config.embedding_dimension)
                    random_embedding = random_embedding / np.linalg.norm(random_embedding)  # Normaliser
                    
                    self._index.add_behavior(chunk, random_embedding)
            
            # Sauvegarder l'index
            await self._save_index()
            logger.info(f"Index comportemental par défaut construit avec {self._index._index.ntotal} behaviors")
            
        except Exception as e:
            logger.error(f"Erreur construction index par défaut: {str(e)}")
            logger.error(traceback.format_exc())
            raise

    async def search(
        self,
        query: str,
        behavior_type: Optional[str] = None,
        limit: int = 5
    ) -> List[BehaviorChunk]:
        """Recherche dans l'index comportemental"""
        try:
            # Obtenir l'embedding de la requête
            query_embedding = await self._get_embedding(query)
            if query_embedding is None:
                return []
                
            # Recherche de base
            chunks = self._index.search(query_embedding, limit * 2)
            
            # Filtrer par type si nécessaire
            if behavior_type:
                chunks = [c for c in chunks if c.behavior_type == behavior_type]
                
            # Trier par priorité et score
            chunks.sort(
                key=lambda x: (x.priority, x.metadata.get("similarity_score", 0.0)),
                reverse=True
            )
            
            return chunks[:limit]
            
        except Exception as e:
            logger.error(f"Erreur recherche comportementale: {str(e)}")
            return []

    async def analyze_intent(
        self,
        query: str,
        session_context: Optional[Dict] = None,
        room_context: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Analyse l'intention de la requête et retourne les configurations pertinentes"""
        # Configuration par défaut (RAG standard)
        default_response = {
            "detected_intent": "standard_rag",
            "confidence": 1.0,  # Confiance maximale pour le comportement par défaut
            "action_config": self._get_default_config(),
            "context_info": {"conversation_style": "formal"}  # Style par défaut
        }

        try:
            # Vérifier si le mode paramétrage est actif
            if room_context and room_context.get("config_mode_active"):
                config_context = room_context.get("config_mode_context")
                if config_context:
                    return {
                        "detected_intent": "config_assistant",
                        "confidence": 1.0,
                        "action_config": config_context,
                        "context_info": {"conversation_style": "formal"}
                    }

            # Vérifier si des configurations personnalisées existent
            if not await self.webdav.exists(str(self.index_dir)):
                logger.info("Aucune configuration personnalisée trouvée, utilisation du RAG standard")
                return default_response

            # 1. Rechercher les actions potentiellement pertinentes
            action_chunks = await self.search(query, behavior_type="action", limit=3)
            if not action_chunks:
                return default_response

            # 2. Analyser le contexte de la conversation
            context_info = await self._analyze_context(query, session_context, room_context)
            
            # 3. Récupérer les configurations associées
            intent_configs = await self._get_intent_configurations(action_chunks, context_info)
            if not intent_configs:
                return default_response

            # 4. Calculer les scores de pertinence
            scored_intents = await self._score_intents(intent_configs, query, context_info)
            
            # 5. Sélectionner la meilleure configuration
            best_intent = await self._select_best_intent(scored_intents)
            
            # Si le score est suffisant, utiliser la configuration personnalisée
            if best_intent["score"] >= 0.6:  # Seuil de confiance élevé pour override le comportement standard
                return {
                    "detected_intent": best_intent["intent"],
                    "confidence": best_intent["score"],
                    "action_config": best_intent["config"],
                    "context_info": context_info
                }
            
            # Sinon, revenir au comportement standard
            return default_response
            
        except Exception as e:
            logger.error(f"Erreur analyse d'intention: {str(e)}")
            logger.info("Fallback sur le comportement RAG standard")
            return default_response

    def _get_default_config(self) -> Dict[str, Any]:
        """Retourne la configuration RAG par défaut"""
        return {
            "base": {
                "search_params": {
                    "include_behavior": True,
                    "include_documents": False,
                    "behavior_type": "conversation",
                    "limit": 10
                },
                "response_generation": {
                    "model": self.config.albert_model,
                    "embedding_model": self.config.albert_model_embedding,
                    "max_history": 2
                }
            }
        }

    def _extract_topics(self, messages: List[str]) -> set:
        """Extrait les topics des messages"""
        topics = set()
        try:
            for message in messages:
                # Utiliser le cache si disponible
                if message in self._topic_cache:
                    topics.update(self._topic_cache[message])
                    continue
                    
                # Extraire les topics du message
                message_topics = set()
                
                # 1. Mots clés spécifiques
                keywords = {
                    "configuration": {"config", "configurer", "paramétrer", "setup"},
                    "api": {"api", "intégration", "endpoint", "service"},
                    "documentation": {"doc", "documentation", "guide", "exemple"},
                    "sécurité": {"sécurité", "permission", "accès", "authentification"}
                }
                
                for topic, words in keywords.items():
                    if any(word in message.lower() for word in words):
                        message_topics.add(topic)
                        
                # 2. Analyse des entités nommées (simplifié)
                entities = {
                    "webdav": {"webdav", "fichier", "dossier", "stockage"},
                    "conversation": {"conversation", "dialogue", "discussion", "chat"},
                    "comportement": {"comportement", "action", "réponse", "réaction"}
                }
                
                for entity, words in entities.items():
                    if any(word in message.lower() for word in words):
                        message_topics.add(entity)
                        
                # Mettre en cache
                self._topic_cache[message] = message_topics
                topics.update(message_topics)
                
        except Exception as e:
            logger.warning(f"Erreur extraction topics: {str(e)}")
            
        return topics

    async def _analyze_context(
        self,
        query: str,
        session_context: Optional[Dict],
        room_context: Optional[Dict]
    ) -> Dict[str, Any]:
        """Analyse le contexte pour enrichir la détection d'intention"""
        context_info = {
            "active_topics": set(),
            "conversation_style": "formal",
            "relevant_rules": [],
            "custom_params": {}
        }
        
        try:
            # 1. Extraire les topics actifs
            if session_context and "history" in session_context:
                recent_messages = [msg["content"] for msg in session_context["history"][-3:]]
                context_info["active_topics"].update(self._extract_topics(recent_messages))
                
            # Ajouter les topics de la requête actuelle
            context_info["active_topics"].update(self._extract_topics([query]))
            
            # 2. Détecter le style de conversation
            style_indicators = {
                "formal": {"pourriez-vous", "s'il vous plaît", "merci", "cordialement"},
                "casual": {"salut", "hey", "ok", "cool"}
            }
            
            query_lower = query.lower()
            for style, indicators in style_indicators.items():
                if any(indicator in query_lower for indicator in indicators):
                    context_info["conversation_style"] = style
                    break
            
            # 3. Récupérer les règles pertinentes
            rules = await self.search(query, behavior_type="rule", limit=2)
            context_info["relevant_rules"] = [
                rule.metadata for rule in rules if rule.metadata.get("priority", 0) > 0.5
            ]
            
            # 4. Récupérer les paramètres personnalisés
            if room_context and "custom_config" in room_context:
                context_info["custom_params"] = room_context["custom_config"]
                
        except Exception as e:
            logger.warning(f"Erreur analyse contexte: {str(e)}")
            
        return context_info
    
    async def _get_intent_configurations(
        self,
        action_chunks: List[BehaviorChunk],
        context_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Récupère les configurations complètes pour chaque intention possible"""
        configs = []
        
        for chunk in action_chunks:
            try:
                # 1. Charger la configuration de base
                base_config = json.loads(chunk.content) if isinstance(chunk.content, str) else chunk.content
                
                # 2. Enrichir avec les outils associés
                tools = await self.search(
                    chunk.metadata.get("associated_tools", ""),
                    behavior_type="tool",
                    limit=2
                )
                tool_configs = {
                    tool.metadata["name"]: json.loads(tool.content)
                    for tool in tools
                }
                
                # 3. Récupérer les prompts associés
                prompts = await self.search(
                    chunk.metadata.get("associated_prompts", ""),
                    behavior_type="prompt",
                    limit=1
                )
                prompt_config = json.loads(prompts[0].content) if prompts else {}
                
                # 4. Assembler la configuration complète
                full_config = {
                    "intent": base_config["type"],
                    "priority": chunk.priority,
                    "config": {
                        "base": base_config["configuration"],
                        "tools": tool_configs,
                        "prompt": prompt_config,
                        "context_specific": context_info.get("custom_params", {})
                    }
                }
                
                configs.append(full_config)
                
            except Exception as e:
                logger.warning(f"Erreur chargement configuration: {str(e)}")
                continue
                
        return configs
    
    async def _score_intents(
        self,
        intent_configs: List[Dict[str, Any]],
        query: str,
        context_info: Dict[str, Any]
    ) -> List[Dict[str, Any]]:
        """Calcule les scores de pertinence pour chaque intention"""
        scored_intents = []
        
        for config in intent_configs:
            try:
                # 1. Score de base (priorité)
                base_score = config["priority"]
                
                # 2. Score basé sur les topics
                query_topics = self._extract_topics([query])
                topic_overlap = len(query_topics & context_info["active_topics"])
                topic_score = min(topic_overlap * 0.2, 0.6)  # Plafonné à 0.6
                
                # 3. Score basé sur le style
                style_match = (
                    config["config"]["prompt"].get("style", "") == 
                    context_info["conversation_style"]
                )
                style_score = 0.1 if style_match else 0
                
                # 4. Score basé sur les règles
                rule_match = any(
                    rule["type"] == config["intent"]
                    for rule in context_info["relevant_rules"]
                )
                rule_score = 0.15 if rule_match else 0
                
                # 5. Score basé sur les paramètres personnalisés
                custom_score = 0.0
                if context_info["custom_params"]:
                    custom_match = any(
                        param in config["config"]["base"].get("capabilities", {})
                        for param in context_info["custom_params"]
                    )
                    custom_score = 0.15 if custom_match else 0
                
                # Score final normalisé
                final_score = min(
                    base_score + topic_score + style_score + rule_score + custom_score,
                    1.0
                )
                
                scored_intents.append({
                    "intent": config["intent"],
                    "score": final_score,
                    "config": config["config"],
                    "details": {
                        "base_score": base_score,
                        "topic_score": topic_score,
                        "style_score": style_score,
                        "rule_score": rule_score,
                        "custom_score": custom_score
                    }
                })
                
            except Exception as e:
                logger.warning(f"Erreur calcul score: {str(e)}")
                continue
                
        return sorted(scored_intents, key=lambda x: x["score"], reverse=True)
    
    async def _select_best_intent(
        self,
        scored_intents: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Sélectionne la meilleure intention selon les scores"""
        if not scored_intents:
            return {
                "intent": "standard_rag",
                "score": 0.0,
                "config": self._get_default_config()
            }
            
        best_intent = scored_intents[0]
        
        # Si le meilleur score est trop faible, revenir au RAG standard
        if best_intent["score"] < 0.4:
            return {
                "intent": "standard_rag",
                "score": 0.0,
                "config": self._get_default_config()
            }
            
        return best_intent 

    async def _load_index(self) -> None:
        """Charge l'index depuis WebDAV"""
        try:
            # Télécharger l'index FAISS
            index_data = await self.webdav.download_file(self.faiss_index_path)
            temp_index = "temp_behavior.faiss"
            with open(temp_index, "wb") as f:
                f.write(index_data)
                
            # Télécharger la map des behaviors
            map_data = await self.webdav.download_file(self.map_path)
            temp_map = "temp_behavior_map.json"
            with open(temp_map, "w") as f:
                f.write(map_data.decode())
                
            # Charger l'index
            self._index = BehaviorFAISSIndex(dimension=self.config.embedding_dimension)
            self._index._index = faiss.read_index(temp_index)
            
            # Charger la map
            with open(temp_map, 'r') as f:
                map_data = json.load(f)
                
            # Restaurer les behaviors
            for idx, data in map_data.items():
                try:
                    chunk = BehaviorChunk(
                        id=data["id"],
                        content=data["content"],
                        behavior_type=data["behavior_type"],
                        priority=data["priority"],
                        metadata=data["metadata"],
                        embedding=data["embedding"],
                        last_updated=datetime.fromisoformat(data["last_updated"]) if data["last_updated"] else None
                    )
                    self._index.behavior_map[int(idx)] = chunk
                except Exception as e:
                    logger.warning(f"Erreur chargement behavior {idx}: {str(e)}")
                    continue
                    
            # Nettoyage
            os.remove(temp_index)
            os.remove(temp_map)
            
            logger.info(f"Index comportemental chargé avec {self._index._index.ntotal} behaviors")
            
        except Exception as e:
            logger.error(f"Erreur chargement index: {str(e)}")
            raise
            
    async def _save_index(self) -> None:
        """Sauvegarde l'index sur WebDAV"""
        try:
            # Sauvegarder l'index FAISS
            temp_index = "temp_behavior.faiss"
            faiss.write_index(self._index._index, temp_index)
            with open(temp_index, "rb") as f:
                await self.webdav.write_file(self.faiss_index_path, f.read())
                
            # Sauvegarder la map
            map_data = {}
            for idx, chunk in self._index.behavior_map.items():
                map_data[str(idx)] = {
                    "id": chunk.id,
                    "content": chunk.content,
                    "behavior_type": chunk.behavior_type,
                    "priority": chunk.priority,
                    "metadata": chunk.metadata,
                    "embedding": chunk.embedding,
                    "last_updated": chunk.last_updated.isoformat() if chunk.last_updated else None
                }
                
            await self.webdav.write_file(
                self.map_path,
                json.dumps(map_data, indent=2)
            )
            
            # Nettoyage
            os.remove(temp_index)
            
        except Exception as e:
            logger.error(f"Erreur sauvegarde index: {str(e)}")
            raise

    async def close(self) -> None:
        """Ferme proprement les ressources utilisées"""
        try:
            # Fermer le service d'embedding s'il existe
            if self._embedding_service is not None:
                try:
                    await self._embedding_service.close()
                except Exception as e:
                    logger.warning(f"Erreur fermeture du service d'embedding: {str(e)}")
                self._embedding_service = None
                
            # Sauvegarder l'index avant de fermer
            if hasattr(self, '_index') and self._index:
                try:
                    await self._save_index()
                except Exception as e:
                    logger.warning(f"Erreur sauvegarde de l'index: {str(e)}")
                    
        except Exception as e:
            logger.error(f"Erreur fermeture des ressources BehaviorIndex: {str(e)}")
            # Ne pas propager l'erreur pour ne pas bloquer la fermeture des autres services 