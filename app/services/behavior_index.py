from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import json
import numpy as np
import faiss

from matrix_bot.config import logger
from config import Config
from services.webdav import WebDAVService

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
    
    def __init__(self, config: Config, webdav_service: WebDAVService):
        self.config = config
        self.webdav = webdav_service
        self.index_dir = Path(self.webdav.BEHAVIOR_DIR)
        self.faiss_index_path = str(self.index_dir / "behavior.faiss")
        self.map_path = str(self.index_dir / "behavior_map.json")
        self._topic_cache = {}
        self._index = BehaviorFAISSIndex(dimension=config.embedding_dimension)
        
    async def initialize(self) -> None:
        """Initialise l'index des behaviors"""
        try:
            # Vérifier/créer le dossier système
            if not await self.webdav.exists(str(self.index_dir)):
                await self.webdav.create_directory(str(self.index_dir))
                
            # Charger l'index existant ou en créer un nouveau
            if await self.webdav.exists(self.faiss_index_path):
                await self._load_index()
            else:
                await self._build_index()
                
            logger.info("Index comportemental initialisé")
            
        except Exception as e:
            logger.error(f"Erreur initialisation index comportemental: {str(e)}")
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

    async def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """Obtient l'embedding d'un texte"""
        try:
            from services.embedding_service import EmbeddingService
            embedding_service = EmbeddingService(self.config)
            embedding = await embedding_service.get_embedding(text)
            return np.array(embedding) if embedding else None
        except Exception as e:
            logger.error(f"Erreur génération embedding: {str(e)}")
            return None
            
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
            
    async def _build_index(self) -> None:
        """Construit l'index à partir des fichiers de configuration"""
        try:
            # Réinitialiser l'index
            self._index = BehaviorFAISSIndex(dimension=self.config.embedding_dimension)
            
            # Parcourir les dossiers de behavior
            for behavior_type in ["actions", "tools", "prompts", "rules"]:
                type_dir = os.path.join(str(self.index_dir), behavior_type)
                if not await self.webdav.exists(type_dir):
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
                        
                        # Obtenir l'embedding
                        embedding = await self._get_embedding(content)
                        if embedding is not None:
                            self._index.add_behavior(chunk, embedding)
                            
                    except Exception as e:
                        logger.warning(f"Erreur indexation {file_path}: {str(e)}")
                        continue
                        
            # Sauvegarder l'index
            await self._save_index()
            logger.info(f"Index comportemental construit avec {self._index._index.ntotal} behaviors")
            
        except Exception as e:
            logger.error(f"Erreur construction index: {str(e)}")
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