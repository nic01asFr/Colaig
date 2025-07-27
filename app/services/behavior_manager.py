from typing import Dict, Any, Optional, List, Tuple, Union
from datetime import datetime
import asyncio
import json
import os
from pathlib import Path
import time
import traceback

from app.matrix_bot.config import logger
from app.config import Config
from app.services.webdav import WebDAVService
from app.services.behavior_index import BehaviorIndex, BehaviorChunk
from app.services.context.types import ContextType
from app.services.initialization import with_initialized_services
from app.services.webdav_context_manager import get_webdav_context_manager

# Instance globale unique pour le pattern singleton
_behavior_manager_instance: Optional['BehaviorManager'] = None
_behavior_manager_lock = asyncio.Lock()
_behavior_contexts: Dict[str, 'BehaviorManager'] = {}  # Stockage des instances par contexte

async def get_behavior_manager(config: Config, context_path: Optional[str] = None) -> 'BehaviorManager':
    """
    Retourne l'instance unique du BehaviorManager ou une instance spécifique à un contexte.
    
    Args:
        config: Configuration de l'application
        context_path: Chemin du contexte WebDAV spécifique (optionnel)
        
    Returns:
        Une instance de BehaviorManager
    """
    global _behavior_manager_instance, _behavior_contexts
    
    # Si un contexte spécifique est demandé, vérifier s'il existe déjà
    if context_path:
        if context_path in _behavior_contexts:
            return _behavior_contexts[context_path]
        
        # Créer une nouvelle instance pour ce contexte
        async with _behavior_manager_lock:
            if context_path not in _behavior_contexts:
                logger.info(f"Création d'une instance BehaviorManager pour le contexte: {context_path}")
                instance = BehaviorManager(config, context_path=context_path)
                await instance.initialize(lazy_loading=True)  # Initialisation paresseuse
                _behavior_contexts[context_path] = instance
            return _behavior_contexts[context_path]
    
    # Sinon, utiliser l'instance globale
    if _behavior_manager_instance is None:
        async with _behavior_manager_lock:
            if _behavior_manager_instance is None:
                logger.info("Création de l'instance unique du BehaviorManager")
                _behavior_manager_instance = BehaviorManager(config)
                await _behavior_manager_instance.initialize()
    
    return _behavior_manager_instance

async def close_behavior_manager(context_path: Optional[str] = None) -> None:
    """
    Ferme proprement le BehaviorManager.
    
    Args:
        context_path: Chemin du contexte spécifique à fermer (optionnel)
    """
    global _behavior_manager_instance, _behavior_contexts
    
    if context_path:
        # Fermer une instance spécifique
        async with _behavior_manager_lock:
            if context_path in _behavior_contexts:
                logger.info(f"Fermeture de l'instance BehaviorManager pour le contexte: {context_path}")
                await _behavior_contexts[context_path].close()
                del _behavior_contexts[context_path]
    else:
        # Fermer l'instance globale
        async with _behavior_manager_lock:
            if _behavior_manager_instance is not None:
                logger.info("Fermeture de l'instance unique du BehaviorManager")
                await _behavior_manager_instance.close()
                _behavior_manager_instance = None
                
            # Fermer également toutes les instances contextuelles
            for ctx_path, instance in list(_behavior_contexts.items()):
                logger.info(f"Fermeture de l'instance contextuelle: {ctx_path}")
                await instance.close()
            _behavior_contexts.clear()

class BehaviorManager:
    """Gestionnaire des comportements avec synchronisation WebDAV"""
    
    # Constante pour le répertoire des comportements
    BEHAVIOR_DIR = "./behaviors"
    
    _registered_types = {
        "actions": {
            "description": "Actions principales du système",
            "default_files": ["config_assistant.json", "standard_rag.json", "api_integration.json"],
            "required": True
        },
        "tools": {
            "description": "Outils et utilitaires",
            "default_files": ["context_handler.json", "webdav_crud.json", "tchap_messaging.json"],
            "required": True
        },
        "prompts": {
            "description": "Templates de réponse",
            "default_files": ["rag_system.json", "config_assistant.json"],
            "required": True
        },
        "rules": {
            "description": "Règles de comportement",
            "default_files": ["response_handling.json", "config_mode.json"],
            "required": True
        }
    }
    
    @classmethod
    def register_behavior_type(cls, type_name: str, description: str, default_files: List[str] = None, required: bool = False) -> None:
        """Enregistre un nouveau type de comportement"""
        if type_name in cls._registered_types:
            raise ValueError(f"Type de comportement déjà enregistré: {type_name}")
            
        cls._registered_types[type_name] = {
            "description": description,
            "default_files": default_files or [],
            "required": required
        }
        logger.info(f"Nouveau type de comportement enregistré: {type_name}")
    
    @property
    def behavior_types(self) -> List[str]:
        """Liste des types de comportement enregistrés"""
        return list(self._registered_types.keys())
    
    @property
    def required_types(self) -> List[str]:
        """Liste des types de comportement requis"""
        return [t for t, info in self._registered_types.items() if info["required"]]
    
    def get_type_info(self, type_name: str) -> Dict[str, Any]:
        """Récupère les informations d'un type de comportement"""
        if type_name not in self._registered_types:
            raise ValueError(f"Type de comportement inconnu: {type_name}")
        return self._registered_types[type_name].copy()
    
    def __init__(self, config: Config, context_path: Optional[str] = None):
        self.config = config
        self.behavior_index = None
        self.webdav_service = None
        self.webdav_context_manager = None
        self._initialized = False
        self._behaviors_loaded = False
        self._degraded_mode = False
        self._sync_lock = asyncio.Lock()
        self._cache = {}
        self._config_mode = {}  # room_id -> {active: bool, timestamp: float}
        self.context_path = context_path  # Chemin du contexte spécifique
        
    async def set_webdav_service(self, webdav_service: WebDAVService) -> None:
        """Définit le service WebDAV à utiliser"""
        self.webdav_service = webdav_service
        
    async def initialize(self, lazy_loading: bool = False) -> None:
        """
        Initialise le gestionnaire de comportements
        
        Args:
            lazy_loading: Si True, ne charge pas immédiatement les comportements
        """
        if self._initialized:
            logger.warning("BehaviorManager déjà initialisé")
            return
            
        logger.info("Initialisation du BehaviorManager...")
        
        try:
            # Obtenir le WebDAVContextManager au lieu de créer un nouveau WebDAVService
            try:
                from app.services.webdav_context_manager import get_webdav_context_manager
                self.webdav_context_manager = await get_webdav_context_manager(self.config)
            except Exception as e:
                logger.warning(f"Erreur récupération WebDAVContextManager: {str(e)}")
                self.webdav_context_manager = None
            
            # Obtenir un service WebDAV depuis le contexte
            if self.webdav_context_manager:
                logger.info("Attente de la résolution complète du contexte WebDAV...")
                try:
                    # S'assurer que les contextes sont complètement chargés avant de continuer
                    await asyncio.wait_for(
                        self.webdav_context_manager.wait_for_contexts_loaded(), 
                        timeout=30  # Timeout de 30 secondes pour éviter un blocage
                    )
                    logger.info("Contextes WebDAV chargés avec succès")
                except asyncio.TimeoutError:
                    logger.warning("Timeout lors de l'attente du chargement des contextes, continuation en mode dégradé")
                    self._degraded_mode = True
                
                try:
                    # Si un contexte spécifique est fourni, l'utiliser
                    if self.context_path:
                        self.webdav_service = await self.webdav_context_manager.get_service_for_context(self.context_path)
                        logger.info(f"Service WebDAV obtenu pour le contexte: {self.context_path}")
                    else:
                        self.webdav_service = await self.webdav_context_manager.get_default_service()
                        logger.info("Service WebDAV obtenu depuis le WebDAVContextManager")
                except Exception as e:
                    logger.warning(f"Erreur récupération service WebDAV depuis WebDAVContextManager: {str(e)}")
                    logger.warning("Tentative de création directe du service WebDAV")
                    self.webdav_service = None
            else:
                # Fallback si le contexte n'est pas disponible
                logger.warning("Aucun WebDAVContextManager disponible, tentative de création directe")
                self.webdav_service = None
            
            # Fallback: création directe du service WebDAV si nécessaire
            if self.webdav_service is None:
                try:
                    from app.services.webdav_service_factory import get_webdav_service
                    self.webdav_service = await get_webdav_service(self.config)
                    if self.webdav_service:
                        logger.info("Service WebDAV créé directement (fallback)")
                    else:
                        logger.warning("Aucun service WebDAV disponible, fonctionnement en mode local")
                        self._degraded_mode = True
                except Exception as e:
                    logger.warning(f"Erreur création service WebDAV: {str(e)}")
                    logger.warning("Fonctionnement en mode local sans WebDAV")
                    self._degraded_mode = True
            
            # Configuration explicite de l'attribut BEHAVIOR_DIR si le service WebDAV est disponible
            if self.webdav_service:
                # Déterminer le chemin du répertoire behavior
                behavior_dir = None
                
                # 1. Utiliser le contexte spécifique si fourni
                if self.context_path:
                    # Construire le chemin relatif au contexte
                    behavior_dir = os.path.join(self.context_path, ".albert/behavior")
                # 2. Sinon utiliser l'attribut BEHAVIOR_DIR du service WebDAV
                elif hasattr(self.webdav_service, 'BEHAVIOR_DIR'):
                    behavior_dir = getattr(self.webdav_service, 'BEHAVIOR_DIR')
                # 3. Sinon utiliser behavior_path de la config
                elif hasattr(self.config, 'behavior_path'):
                    behavior_dir = self.config.behavior_path
                # 4. En dernier recours, utiliser la constante par défaut
                else:
                    behavior_dir = self.BEHAVIOR_DIR
                
                setattr(self.webdav_service, 'BEHAVIOR_DIR', behavior_dir)
                logger.debug(f"Utilisation du BEHAVIOR_DIR du service WebDAV: {behavior_dir}")
                
            # Créer l'index comportemental
            from app.services.behavior_index import BehaviorIndex
            self.behavior_index = BehaviorIndex(self.config, self.webdav_service)
            logger.info("BehaviorIndex créé pour le BehaviorManager")
            
            # Charger les comportements si pas en mode lazy loading
            if not lazy_loading:
                await self._load_behaviors()
            
            self._initialized = True
            logger.info(f"BehaviorManager initialisé (mode dégradé: {self._degraded_mode}, lazy loading: {lazy_loading})")
            
        except Exception as e:
            logger.error(f"Erreur initialisation BehaviorManager: {str(e)}")
            logger.error(traceback.format_exc())
            self._degraded_mode = True
            self._initialized = True  # Marquer comme initialisé pour permettre le fonctionnement dégradé
            logger.warning("BehaviorManager initialisé en mode dégradé suite à une erreur")

    async def ensure_behaviors_loaded(self) -> None:
        """S'assure que les comportements sont chargés (utile en mode lazy loading)"""
        if not self._behaviors_loaded:
            await self._load_behaviors()
            self._behaviors_loaded = True

    async def _setup_degraded_mode(self) -> None:
        """Configure un mode dégradé avec des comportements minimaux"""
        logger.info("Configuration du mode dégradé avec comportements minimaux")
        try:
            # Créer un nouvel index minimal
            if not self.behavior_index:
                self.behavior_index = BehaviorIndex(self.config, None)  # Mode local
            
            # Force l'utilisation d'embeddings aléatoires
            setattr(self.behavior_index, '_use_random_embeddings', True)
            
            # Créer des comportements par défaut minimaux en mémoire
            await self.behavior_index._build_default_index()
            logger.info("Mode dégradé configuré avec comportements minimaux")
        except Exception as e:
            logger.error(f"Erreur configuration mode dégradé: {str(e)}")
            # Toujours définir un index minimal fonctionnel
            self.behavior_index = BehaviorIndex(self.config, None)
            
    async def _cleanup_resources(self) -> None:
        """Nettoie les ressources en cas d'erreur"""
        try:
            if hasattr(self, 'behavior_index') and self.behavior_index:
                try:
                    await self.behavior_index._save_index()  # Sauvegarder l'état actuel
                except Exception:
                    pass  # Ignorer les erreurs de sauvegarde en nettoyage
                    
            if hasattr(self, 'webdav_service') and self.webdav_service:
                try:
                    await self.webdav_service.close()
                except Exception:
                    pass  # Ignorer les erreurs de fermeture en nettoyage
        except Exception as e:
            logger.error(f"Erreur nettoyage ressources: {str(e)}")
            
    async def close(self) -> None:
        """Ferme proprement le gestionnaire"""
        if not self._initialized:
            return
            
        try:
            await self._cleanup_resources()
            self._initialized = False
            logger.info("BehaviorManager fermé avec succès")
        except Exception as e:
            logger.error(f"Erreur fermeture BehaviorManager: {str(e)}")
            raise
            
    async def _ensure_webdav_directories(self) -> None:
        """S'assure que les dossiers nécessaires existent sur WebDAV"""
        try:
            # Créer le dossier racine
            root_dir = self.config.behavior_path
            if not await self.webdav_service.exists(root_dir):
                if not await self.webdav_service.create_directory(root_dir):
                    raise RuntimeError(f"Impossible de créer le dossier {root_dir}")
                    
            # Créer les sous-dossiers par type
            for behavior_type in self.behavior_types:
                type_dir = os.path.join(root_dir, behavior_type)
                if not await self.webdav_service.exists(type_dir):
                    if not await self.webdav_service.create_directory(type_dir):
                        raise RuntimeError(f"Impossible de créer le dossier {type_dir}")
                        
        except Exception as e:
            logger.error(f"Erreur création dossiers WebDAV: {str(e)}")
            raise
            
    async def _ensure_default_configurations(self) -> None:
        """S'assure que les configurations par défaut existent"""
        try:
            for behavior_type, info in self._registered_types.items():
                type_dir = os.path.join(self.config.behavior_path, behavior_type)
                
                for file_name in info["default_files"]:
                    file_path = os.path.join(type_dir, file_name)
                    if not await self.webdav_service.exists(file_path):
                        default_config = await self._get_default_config(behavior_type, file_name)
                        if default_config:
                            await self.save_behavior(
                                behavior_id=os.path.splitext(file_name)[0],
                                behavior_type=behavior_type,
                                behavior_data=default_config
                            )
                            
        except Exception as e:
            logger.error(f"Erreur création configurations par défaut: {str(e)}")
            raise

    async def _get_default_config(self, behavior_type: str, file_name: str) -> Dict[str, Any]:
        """Retourne la configuration par défaut pour un type de comportement"""
        base_config = {
            "type": behavior_type.rstrip('s'),  # Remove plural
            "description": f"Configuration par défaut pour {file_name}",
            "priority": 1.0 if file_name == "standard_rag.json" else 0.8
        }
        
        if behavior_type == "actions":
            if file_name == "standard_rag.json":
                base_config.update({
                    "configuration": {
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
                })
            elif file_name == "config_assistant.json":
                base_config.update({
                    "configuration": {
                        "capabilities": {
                            "webdav_integration": True,
                            "api_integration": True,
                            "custom_actions": True,
                            "custom_tools": True,
                            "custom_prompts": True
                        },
                        "configuration_steps": {
                            "analyze_request": {
                                "description": "Analyse la demande de configuration",
                                "parameters": ["query", "context"]
                            },
                            "identify_components": {
                                "description": "Identifie les composants nécessaires",
                                "parameters": ["request_type", "requirements"]
                            },
                            "generate_config": {
                                "description": "Génère la configuration appropriée",
                                "parameters": ["components", "format"]
                            }
                        }
                    }
                })
        elif behavior_type == "tools":
            if file_name == "context_handler.json":
                base_config.update({
                    "configuration": {
                        "history_management": {
                            "max_length": 10,
                            "memory_duration": 3600,
                            "cleanup_interval": 300
                        },
                        "topic_tracking": {
                            "relevance_threshold": 0.3,
                            "max_topics": 5
                        }
                    }
                })
            elif file_name == "webdav_crud.json":
                base_config.update({
                    "configuration": {
                        "operations": {
                            "create": {
                                "method": "PUT",
                                "required_params": ["path", "content"]
                            },
                            "read": {
                                "method": "GET",
                                "required_params": ["path"]
                            },
                            "update": {
                                "method": "PUT",
                                "required_params": ["path", "content"]
                            },
                            "delete": {
                                "method": "DELETE",
                                "required_params": ["path"]
                            }
                        },
                        "security": {
                            "check_permissions": True,
                            "validate_paths": True
                        }
                    }
                })
        elif behavior_type == "rules":
            if file_name == "response_handling.json":
                base_config.update({
                    "configuration": {
                        "cleaning_rules": {
                            "remove_patterns": [
                                "Basé sur les documents fournis,",
                                "D'après les documents,",
                                "Selon les sources,"
                            ]
                        },
                        "formatting": {
                            "standard": {
                                "template": "🤖 {response}",
                                "conditions": {"show_sources": False}
                            }
                        }
                    }
                })
            elif file_name == "config_mode.json":
                base_config.update({
                    "configuration": {
                        "mode_detection": {
                            "keywords": ["configurer", "paramétrer", "personnaliser", "adapter"],
                            "context_indicators": ["configuration", "paramétrage", "setup"]
                        },
                        "conversation_rules": {
                            "max_steps": 10,
                            "confirmation_required": True,
                            "allow_backtrack": True,
                            "timeout": 3600
                        },
                        "validation_steps": {
                            "syntax_check": {
                                "enabled": True,
                                "strict": True
                            },
                            "security_check": {
                                "enabled": True,
                                "checks": ["api_keys", "paths", "permissions"]
                            }
                        }
                    }
                })
        elif behavior_type == "prompts":
            if file_name == "rag_system.json":
                base_config.update({
                    "configuration": {
                        "base_prompt": "Vous êtes Colaig, l'assistant de l'État français.",
                        "style_variations": {
                            "formal": {
                                "description": "Style formel pour les échanges professionnels",
                                "prompt_suffix": "Adoptez un ton formel et professionnel."
                            }
                        }
                    }
                })
            elif file_name == "config_assistant.json":
                base_config.update({
                    "configuration": {
                        "base_prompt": "Je suis en mode configuration. Je vais vous guider pas à pas dans la personnalisation de Colaig selon vos besoins.",
                        "conversation_flows": {
                            "initial_assessment": {
                                "message": "Pour commencer, pouvez-vous me décrire en quelques mots ce que vous souhaitez configurer ?",
                                "follow_up": {
                                    "unclear": "Je ne suis pas sûr de bien comprendre votre besoin. Pouvez-vous me donner plus de détails ?",
                                    "not_possible": "Je suis désolé, mais cette configuration n'est pas possible car : {reason}. Voici ce que je peux vous proposer à la place : {alternatives}",
                                    "needs_clarification": "Pour mieux vous aider, j'aurais besoin de précisions sur : {points}"
                                }
                            }
                        }
                    }
                })
                
        return base_config

    async def get_behavior(
        self,
        behavior_id: str,
        behavior_type: str
    ) -> Optional[Dict[str, Any]]:
        """Récupère un comportement depuis WebDAV"""
        if not self._initialized:
            raise RuntimeError("Le gestionnaire n'est pas initialisé")
            
        try:
            # Construire le chemin
            behavior_path = os.path.join(
                self.config.behavior_path,
                behavior_type,
                f"{behavior_id}.json"
            )
            
            # Vérifier l'existence
            if not await self.webdav_service.exists(behavior_path):
                return None
                
            # Lire le contenu
            content = await self.webdav_service.read_document(behavior_path)
            return json.loads(content)
            
        except Exception as e:
            logger.error(f"Erreur lecture comportement {behavior_id}: {str(e)}")
            return None
            
    async def save_behavior(
        self,
        behavior_id: str,
        behavior_type: str,
        behavior_data: Dict[str, Any]
    ) -> bool:
        """Sauvegarde un comportement sur WebDAV"""
        if not self._initialized:
            raise RuntimeError("Le gestionnaire n'est pas initialisé")
            
        try:
            # Valider le type
            if behavior_type not in self.behavior_types:
                raise ValueError(f"Type de comportement invalide: {behavior_type}")
                
            # Construire le chemin
            behavior_path = os.path.join(
                self.config.behavior_path,
                behavior_type,
                f"{behavior_id}.json"
            )
            
            # Sauvegarder
            await self.webdav_service.write_file(
                behavior_path,
                json.dumps(behavior_data, indent=2)
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Erreur sauvegarde comportement {behavior_id}: {str(e)}")
            return False
            
    async def list_behaviors(
        self,
        behavior_type: Optional[str] = None
    ) -> List[str]:
        """Liste les comportements disponibles"""
        if not self._initialized:
            raise RuntimeError("Le gestionnaire n'est pas initialisé")
            
        try:
            behaviors = []
            types_to_check = [behavior_type] if behavior_type else self.behavior_types
            
            for btype in types_to_check:
                type_dir = os.path.join(self.config.behavior_path, btype)
                if await self.webdav_service.exists(type_dir):
                    files = await self.webdav_service.list_documents(type_dir)
                    behaviors.extend([
                        os.path.splitext(os.path.basename(f))[0]
                        for f in files if f.endswith('.json')
                    ])
                    
            return behaviors
            
        except Exception as e:
            logger.error(f"Erreur listage comportements: {str(e)}")
            return []
            
    async def delete_behavior(
        self,
        behavior_id: str,
        behavior_type: str
    ) -> bool:
        """Supprime un comportement"""
        if not self._initialized:
            raise RuntimeError("Le gestionnaire n'est pas initialisé")
            
        try:
            behavior_path = os.path.join(
                self.config.behavior_path,
                behavior_type,
                f"{behavior_id}.json"
            )
            
            if await self.webdav_service.exists(behavior_path):
                await self.webdav_service.delete_file(behavior_path)
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"Erreur suppression comportement {behavior_id}: {str(e)}")
            return False
            
    @property
    def index(self) -> Optional[BehaviorIndex]:
        """Retourne l'index comportemental"""
        return self.behavior_index if self._initialized else None 

    async def is_config_command(self, message: str) -> bool:
        """Vérifie si le message est une commande de configuration"""
        return message.strip().lower() == self.config.config_command.lower()
        
    async def activate_config_mode(self, room_id: str) -> Dict[str, Any]:
        """Active le mode paramétrage pour une salle"""
        try:
            # Récupérer la configuration du mode paramétrage
            config = await self.get_behavior("config_assistant", "actions")
            if not config:
                logger.warning("Configuration du mode paramétrage non trouvée")
                return None
                
            # Activer le mode pour cette salle
            self._config_mode[room_id] = {
                "active": True,
                "timestamp": time.time(),
                "config": config
            }
            
            logger.info(f"Mode paramétrage activé pour la salle {room_id}")
            return config
            
        except Exception as e:
            logger.error(f"Erreur activation mode paramétrage: {str(e)}")
            return None
            
    async def deactivate_config_mode(self, room_id: str) -> None:
        """Désactive le mode paramétrage pour une salle"""
        if room_id in self._config_mode:
            del self._config_mode[room_id]
            logger.info(f"Mode paramétrage désactivé pour la salle {room_id}")
            
    async def is_config_mode_active(self, room_id: str) -> bool:
        """Vérifie si le mode paramétrage est actif pour une salle"""
        if room_id not in self._config_mode:
            return False
            
        config_info = self._config_mode[room_id]
        current_time = time.time()
        
        # Vérifier l'expiration
        if current_time - config_info["timestamp"] > self.config.colaig_config_timeout:
            await self.deactivate_config_mode(room_id)
            return False
            
        return config_info["active"]
        
    async def get_config_mode_context(self, room_id: str) -> Optional[Dict[str, Any]]:
        """Récupère le contexte du mode paramétrage pour une salle"""
        if not await self.is_config_mode_active(room_id):
            return None
            
        return self._config_mode.get(room_id, {}).get("config") 

    async def _load_behaviors(self):
        """Charge les comportements depuis WebDAV ou le système de fichiers local"""
        try:
            # Initialiser l'index comportemental
            if self.behavior_index:
                try:
                    logger.info("Initialisation de l'index comportemental...")
                    await self.behavior_index.initialize()
                    logger.info("Index comportemental initialisé avec succès")
                except Exception as e:
                    logger.error(f"Erreur initialisation index comportemental: {str(e)}")
                    logger.error(traceback.format_exc())
                    # Continuer malgré l'erreur, l'index sera peut-être ré-initialisé plus tard
                    return
            else:
                logger.warning("Aucun index comportemental disponible")
                return
                
            # S'assurer que les répertoires nécessaires existent
            if self.webdav_service:
                try:
                    await self._ensure_webdav_directories()
                    logger.info("Structure de répertoires WebDAV vérifiée")
                except Exception as e:
                    logger.warning(f"Erreur vérification répertoires WebDAV: {str(e)}")
                    # Continuer malgré l'erreur
            else:
                # Mode local
                root_dir = Path(self.config.behavior_path if hasattr(self.config, 'behavior_path') else self.BEHAVIOR_DIR)
                for behavior_type in self.behavior_types:
                    behavior_path = root_dir / behavior_type
                    os.makedirs(behavior_path, exist_ok=True)
                logger.info("Structure de répertoires locaux vérifiée")
                
            # Vérifier et créer les comportements par défaut
            if self.webdav_service:
                try:
                    await self._ensure_default_configurations()
                    logger.info("Configurations par défaut vérifiées")
                except Exception as e:
                    logger.warning(f"Erreur vérification configurations par défaut: {str(e)}")
                    # Continuer malgré l'erreur
            
            # Comportements chargés avec succès
            self._behaviors_loaded = True
            logger.info("Comportements chargés avec succès")
            
        except Exception as e:
            logger.error(f"Erreur chargement comportements: {str(e)}")
            logger.error(traceback.format_exc())
            # Ne pas propager l'erreur, laisser l'application continuer
    
    @with_initialized_services
    async def detect_intent(self, message: str) -> Tuple[bool, str, float]:
        """
        Détecte l'intention de l'utilisateur à partir d'un message.
        
        Args:
            message: Message de l'utilisateur
            
        Returns:
            Tuple (a_une_intention, nom_intention, score)
        """
        # S'assurer que le gestionnaire est initialisé
        if not self._initialized:
            await self.initialize()
        
        return await self.behavior_index.detect_intent(message)
    
    @with_initialized_services
    async def execute_action(self, 
                           intent_name: str, 
                           query: str, 
                           context: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Exécute l'action associée à une intention.
        
        Args:
            intent_name: Nom de l'intention détectée
            query: Requête de l'utilisateur
            context: Contexte de la conversation
            
        Returns:
            Résultat de l'action
        """
        # S'assurer que le gestionnaire est initialisé
        if not self._initialized:
            await self.initialize()
            
        # Récupérer le comportement associé à cette intention
        behavior = self.behavior_index.get_behavior_by_intent(intent_name)
        
        if not behavior:
            return {
                "success": False,
                "error": f"Aucun comportement trouvé pour l'intention {intent_name}",
                "response": "Je ne sais pas comment traiter cette demande."
            }
        
        # Récupérer l'action à exécuter
        action_config = behavior.get("action", {})
        action_type = action_config.get("type")
        
        if not action_type:
            return {
                "success": False,
                "error": f"Aucune action définie pour le comportement {behavior.get('name')}",
                "response": "Je ne sais pas comment traiter cette demande."
            }
        
        try:
            # Exécuter l'action en fonction de son type
            if action_type == "rag":
                # Action RAG (Retrieval Augmented Generation)
                from app.actions.standard_rag_action import StandardRagAction
                
                # Créer et exécuter l'action
                action = StandardRagAction(self.config)
                result = await action.execute(query, context, action_config.get("options", {}))
                
                return result
                
            elif action_type == "synthesis":
                # Action de synthèse
                from app.actions.synthesis_rag_action import SynthesisRagAction
                
                # Créer et exécuter l'action
                action = SynthesisRagAction(self.config)
                result = await action.execute(query, context, action_config.get("options", {}))
                
                return result
                
            else:
                # Type d'action non reconnu
                return {
                    "success": False,
                    "error": f"Type d'action non reconnu: {action_type}",
                    "response": "Je ne sais pas comment traiter cette demande."
                }
                
        except Exception as e:
            logger.error(f"Erreur lors de l'exécution de l'action {action_type}: {str(e)}")
            logger.error(traceback.format_exc())
            
            return {
                "success": False,
                "error": str(e),
                "response": f"Une erreur est survenue lors du traitement de votre demande: {str(e)}"
            } 