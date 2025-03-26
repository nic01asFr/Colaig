from typing import Dict, Any, Optional, List
from datetime import datetime
import asyncio
import json
import os
from pathlib import Path
import time

from matrix_bot.config import logger
from config import Config
from services.webdav import WebDAVService
from services.behavior_index import BehaviorIndex, BehaviorChunk

class BehaviorManager:
    """Gestionnaire des comportements avec synchronisation WebDAV"""
    
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
    
    def __init__(self, config: Config):
        self.config = config
        self._webdav = None
        self._index = None
        self._initialized = False
        self._sync_lock = asyncio.Lock()
        self._cache = {}
        self._config_mode = {}  # room_id -> {active: bool, timestamp: float}
        
    async def initialize(self) -> None:
        """Initialise le gestionnaire de comportements"""
        try:
            if self._initialized:
                logger.warning("BehaviorManager déjà initialisé")
                return
                
            # Vérification du service WebDAV
            if not hasattr(self, '_webdav') or not self._webdav:
                raise RuntimeError("Service WebDAV non défini")
            
            # S'assurer que le service WebDAV est initialisé
            if not self._webdav._initialized:
                await self._webdav.initialize()
            
            # Création structure
            await self._ensure_webdav_directories()
            
            # Vérification configurations par défaut
            await self._ensure_default_configurations()
            
            # Initialisation index
            if not self._index:
                self._index = BehaviorIndex(self.config, self._webdav)
                await self._index.initialize()
                
            # Marquer comme initialisé seulement après que tout est fait avec succès
            self._initialized = True
            logger.info("BehaviorManager initialisé avec succès")
                
        except Exception as e:
            self._initialized = False
            logger.error(f"Erreur initialisation BehaviorManager: {str(e)}")
            raise RuntimeError(f"Échec initialisation BehaviorManager: {str(e)}")
            
    async def close(self) -> None:
        """Ferme proprement le gestionnaire"""
        if not self._initialized:
            return
            
        try:
            if self._webdav:
                await self._webdav.close()
                
            self._initialized = False
            logger.info("Gestionnaire de comportements fermé")
            
        except Exception as e:
            logger.error(f"Erreur fermeture gestionnaire de comportements: {str(e)}")
            raise
            
    async def _ensure_webdav_directories(self) -> None:
        """S'assure que les dossiers nécessaires existent sur WebDAV"""
        try:
            # Créer le dossier racine
            root_dir = self.config.behavior_path
            if not await self._webdav.exists(root_dir):
                if not await self._webdav.create_directory(root_dir):
                    raise RuntimeError(f"Impossible de créer le dossier {root_dir}")
                    
            # Créer les sous-dossiers par type
            for behavior_type in self.behavior_types:
                type_dir = os.path.join(root_dir, behavior_type)
                if not await self._webdav.exists(type_dir):
                    if not await self._webdav.create_directory(type_dir):
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
                    if not await self._webdav.exists(file_path):
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
            if not await self._webdav.exists(behavior_path):
                return None
                
            # Lire le contenu
            content = await self._webdav.read_document(behavior_path)
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
            await self._webdav.write_file(
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
                if await self._webdav.exists(type_dir):
                    files = await self._webdav.list_documents(type_dir)
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
            
            if await self._webdav.exists(behavior_path):
                await self._webdav.delete_file(behavior_path)
                return True
                
            return False
            
        except Exception as e:
            logger.error(f"Erreur suppression comportement {behavior_id}: {str(e)}")
            return False
            
    @property
    def index(self) -> Optional[BehaviorIndex]:
        """Retourne l'index comportemental"""
        return self._index if self._initialized else None 

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