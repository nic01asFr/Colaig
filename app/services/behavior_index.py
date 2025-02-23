from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import os
import json

from matrix_bot.config import logger
from config import Config
from services.webdav import WebDAVService
from services.document_index import DocumentIndex, DocumentChunk, FAISSIndex

@dataclass
class BehaviorChunk(DocumentChunk):
    """Représente un morceau de configuration comportementale"""
    behavior_type: str  # actions, tools, prompts, rules
    priority: float = 1.0  # Priorité du comportement

class BehaviorIndex(DocumentIndex):
    """Gère l'indexation et la recherche dans les fichiers de comportement"""
    
    SYSTEM_DIR = ".colaig"
    
    def __init__(self, config: Config, webdav_service: WebDAVService):
        super().__init__(config, webdav_service)
        self.index_dir = Path(config.colaig_behavior_path)
        self.faiss_index_path = str(self.index_dir / "behavior.faiss")
        self.map_path = str(self.index_dir / "behavior_map.json")
        
    async def initialize(self) -> None:
        """Initialise l'index comportemental"""
        try:
            # Créer les dossiers nécessaires
            os.makedirs(self.index_dir, exist_ok=True)
            os.makedirs(self.index_dir / "actions", exist_ok=True)
            os.makedirs(self.index_dir / "tools", exist_ok=True)
            os.makedirs(self.index_dir / "prompts", exist_ok=True)
            os.makedirs(self.index_dir / "rules", exist_ok=True)
            
            # Vérifier et créer les fichiers de configuration par défaut
            await self._ensure_default_behaviors()
            
            # Initialiser l'index FAISS
            await super().initialize()
            
        except Exception as e:
            logger.error(f"Erreur initialisation index comportemental: {str(e)}")
            raise
            
    async def _ensure_default_behaviors(self) -> None:
        """Assure la présence des comportements par défaut"""
        default_behaviors = {
            "actions/config_assistant.json": {
                "type": "action",
                "description": "Assistant de configuration pour Colaig",
                "priority": 0.95,  # Priorité élevée mais inférieure au RAG standard
                "configuration": {
                    "capabilities": {
                        "webdav_integration": True,
                        "api_integration": True,
                        "custom_actions": True,
                        "custom_tools": True,
                        "custom_prompts": True
                    },
                    "default_tools": [
                        "webdav_crud",
                        "tchap_messaging",
                        "context_handler"
                    ],
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
                },
                "example_extension": {
                    "comment": "Pour étendre l'assistant de configuration, ajoutez de nouvelles capacités ou étapes",
                    "new_capability": {
                        "name": "ma_capacite",
                        "description": "Description de la capacité",
                        "required_components": []
                    }
                }
            },
            "actions/standard_rag.json": {
                "type": "action",
                "description": "Action RAG standard pour la recherche et la génération de réponses",
                "priority": 1.0,
                "configuration": {
                    "search_params": {
                        "include_behavior": True,
                        "include_documents": False,
                        "behavior_type": "conversation",
                        "limit": 10
                    },
                    "response_generation": {
                        "model": "meta-llama/Llama-3.1-8B-Instruct",
                        "embedding_model": "BAAI/bge-m3",
                        "max_history": 2
                    }
                },
                "example_extension": {
                    "comment": "Pour ajouter une nouvelle action, dupliquez ce fichier et adaptez les paramètres",
                    "custom_params": {
                        "votre_param": "valeur"
                    }
                }
            },
            "tools/context_handler.json": {
                "type": "tool",
                "description": "Gestionnaire de contexte pour le suivi des conversations",
                "priority": 0.9,
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
                },
                "example_extension": {
                    "comment": "Pour ajouter un nouvel outil, créez un fichier similaire avec vos fonctionnalités",
                    "required_methods": ["initialize", "process", "cleanup"]
                }
            },
            "prompts/rag_system.json": {
                "type": "prompt",
                "description": "Prompts système pour le RAG conversationnel",
                "priority": 0.8,
                "configuration": {
                    "base_prompt": "Vous êtes Colaig, l'assistant de l'État français.",
                    "style_variations": {
                        "formal": {
                            "description": "Style formel pour les échanges professionnels",
                            "indicators": ["pourriez-vous", "s'il vous plaît", "merci"],
                            "prompt_suffix": "Adoptez un ton formel et professionnel."
                        },
                        "casual": {
                            "description": "Style décontracté pour les échanges informels",
                            "indicators": ["salut", "hey", "ok"],
                            "prompt_suffix": "Adoptez un ton cordial tout en restant professionnel."
                        }
                    }
                },
                "example_extension": {
                    "comment": "Pour ajouter un nouveau style, ajoutez une entrée dans style_variations"
                }
            },
            "rules/response_handling.json": {
                "type": "rule",
                "description": "Règles de traitement et formatage des réponses",
                "priority": 0.7,
                "configuration": {
                    "cleaning_rules": {
                        "remove_patterns": [
                            "Basé sur les documents fournis,",
                            "D'après les documents,",
                            "Selon les sources,"
                        ],
                        "split_markers": [
                            "En regardant les documents",
                            "En analysant les sources",
                            "Je vais essayer de"
                        ]
                    },
                    "formatting": {
                        "standard": {
                            "template": "🤖 {response}",
                            "conditions": {"show_sources": False}
                        },
                        "detailed": {
                            "template": "🤖 {response}\n\n💡 Sources :\n{sources}",
                            "conditions": {"show_sources": True}
                        }
                    }
                },
                "example_extension": {
                    "comment": "Pour ajouter de nouvelles règles, suivez la structure ci-dessus"
                }
            },
            "tools/webdav_crud.json": {
                "type": "tool",
                "description": "Outil pour les opérations CRUD sur WebDAV",
                "priority": 0.8,
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
            },
            "tools/tchap_messaging.json": {
                "type": "tool",
                "description": "Outil pour l'envoi de messages via Tchap",
                "priority": 0.8,
                "configuration": {
                    "message_types": {
                        "notice": {
                            "format": "markdown",
                            "emoji_prefix": "ℹ️"
                        },
                        "error": {
                            "format": "markdown",
                            "emoji_prefix": "❌"
                        },
                        "success": {
                            "format": "markdown",
                            "emoji_prefix": "✅"
                        }
                    },
                    "formatting": {
                        "use_markdown": True,
                        "allow_html": False
                    }
                }
            },
            "tools/config_manager.json": {
                "type": "tool",
                "description": "Outil de gestion des configurations Colaig",
                "priority": 0.85,
                "configuration": {
                    "operations": {
                        "analyze_api": {
                            "description": "Analyse une documentation API pour générer des tools et actions",
                            "required_params": ["api_doc", "target_features"]
                        },
                        "generate_behavior": {
                            "description": "Génère un nouveau fichier de comportement",
                            "required_params": ["behavior_type", "config_data"]
                        },
                        "validate_config": {
                            "description": "Valide une configuration avant application",
                            "required_params": ["config_data", "behavior_type"]
                        }
                    },
                    "templates": {
                        "action": {
                            "base_structure": {
                                "type": "action",
                                "description": "",
                                "priority": 0.5,
                                "configuration": {},
                                "example_extension": {}
                            }
                        },
                        "tool": {
                            "base_structure": {
                                "type": "tool",
                                "description": "",
                                "priority": 0.5,
                                "configuration": {},
                                "example_extension": {}
                            }
                        }
                    },
                    "validation_rules": {
                        "required_fields": ["type", "description", "configuration"],
                        "allowed_types": ["action", "tool", "prompt", "rule"],
                        "priority_range": [0.0, 1.0]
                    }
                }
            },
            "actions/api_integration.json": {
                "type": "action",
                "description": "Action pour intégrer une API externe",
                "priority": 0.9,
                "configuration": {
                    "steps": {
                        "analyze_doc": {
                            "description": "Analyse de la documentation API",
                            "tool": "config_manager",
                            "operation": "analyze_api"
                        },
                        "generate_tools": {
                            "description": "Génération des outils d'API",
                            "tool": "config_manager",
                            "operation": "generate_behavior"
                        },
                        "generate_actions": {
                            "description": "Génération des actions utilisant l'API",
                            "tool": "config_manager",
                            "operation": "generate_behavior"
                        }
                    },
                    "validation": {
                        "required_fields": ["api_doc", "target_features"],
                        "security_checks": ["api_key_handling", "url_validation"]
                    }
                }
            },
            "actions/behavior_manager.json": {
                "type": "action",
                "description": "Action pour gérer les comportements personnalisés",
                "priority": 0.9,
                "configuration": {
                    "operations": {
                        "create": {
                            "description": "Créer un nouveau comportement",
                            "tools": ["config_manager", "webdav_crud"],
                            "required_params": ["behavior_type", "config_data"]
                        },
                        "update": {
                            "description": "Mettre à jour un comportement existant",
                            "tools": ["config_manager", "webdav_crud"],
                            "required_params": ["behavior_path", "config_data"]
                        },
                        "delete": {
                            "description": "Supprimer un comportement",
                            "tools": ["config_manager", "webdav_crud"],
                            "required_params": ["behavior_path"]
                        },
                        "list": {
                            "description": "Lister les comportements disponibles",
                            "tools": ["webdav_crud"],
                            "required_params": ["behavior_type"]
                        }
                    }
                }
            },
            "prompts/config_assistant.json": {
                "type": "prompt",
                "description": "Prompts pour l'assistant de configuration",
                "priority": 0.9,
                "configuration": {
                    "base_prompt": "Je suis l'assistant de configuration de Colaig. Je peux vous aider à configurer et personnaliser le système selon vos besoins.",
                    "conversation_flows": {
                        "api_integration": {
                            "initial": "Pour intégrer une nouvelle API, j'aurai besoin de sa documentation. Pouvez-vous me la fournir ou m'indiquer où la trouver ?",
                            "doc_received": "J'ai bien reçu la documentation. Quelles fonctionnalités spécifiques souhaitez-vous intégrer ?",
                            "features_selected": "D'accord. Je vais créer les outils et actions nécessaires. Souhaitez-vous des configurations particulières pour ces fonctionnalités ?"
                        },
                        "behavior_creation": {
                            "initial": "Pour créer un nouveau comportement, j'ai besoin de savoir quel type vous souhaitez (action, tool, prompt, rule).",
                            "type_selected": "Très bien. Maintenant, décrivez-moi le comportement souhaité en langage naturel.",
                            "description_received": "Je vais traduire votre description en configuration technique. Voici ma proposition, souhaitez-vous des ajustements ?"
                        }
                    },
                    "error_handling": {
                        "invalid_input": "Je ne peux pas traiter cette entrée. {error_details}",
                        "missing_info": "Il me manque des informations importantes : {missing_fields}",
                        "validation_failed": "La validation a échoué : {validation_errors}"
                    }
                }
            },
            "prompts/api_integration.json": {
                "type": "prompt",
                "description": "Prompts pour l'intégration d'API",
                "priority": 0.85,
                "configuration": {
                    "base_prompt": "Je vais vous aider à intégrer une nouvelle API dans Colaig.",
                    "analysis_prompts": {
                        "doc_analysis": "Analysons la documentation de l'API {api_name}. Je vais identifier les endpoints pertinents et les structures de données nécessaires.",
                        "feature_selection": "Voici les fonctionnalités disponibles dans l'API : {features}. Lesquelles souhaitez-vous intégrer ?",
                        "security_config": "Comment souhaitez-vous gérer l'authentification pour cette API ? (clé API, OAuth, etc.)"
                    },
                    "generation_prompts": {
                        "tool_creation": "Je génère les outils pour interagir avec l'API. Voici la structure proposée : {tool_structure}",
                        "action_creation": "Je crée maintenant les actions qui utiliseront ces outils. Voici les actions proposées : {action_list}"
                    }
                }
            },
            "prompts/behavior_management.json": {
                "type": "prompt",
                "description": "Prompts pour la gestion des comportements",
                "priority": 0.85,
                "configuration": {
                    "base_prompt": "Je vais vous aider à gérer les comportements personnalisés de Colaig.",
                    "operation_prompts": {
                        "create": {
                            "type_selection": "Quel type de comportement souhaitez-vous créer ? (action/tool/prompt/rule)",
                            "name_input": "Quel nom souhaitez-vous donner à ce comportement ?",
                            "description_input": "Décrivez le comportement souhaité en quelques phrases.",
                            "config_review": "Voici la configuration générée. Souhaitez-vous des modifications ?"
                        },
                        "update": {
                            "selection": "Quel comportement souhaitez-vous modifier ?",
                            "field_selection": "Quels aspects souhaitez-vous modifier ?",
                            "changes_review": "Voici les modifications proposées. Sont-elles correctes ?"
                        },
                        "delete": {
                            "confirmation": "Êtes-vous sûr de vouloir supprimer {behavior_name} ? Cette action est irréversible."
                        },
                        "list": {
                            "filter_prompt": "Souhaitez-vous filtrer les comportements par type ou priorité ?"
                        }
                    }
                }
            },
            "rules/config_mode.json": {
                "type": "rule",
                "description": "Règles pour le mode configuration",
                "priority": 0.9,
                "configuration": {
                    "mode_detection": {
                        "keywords": ["configurer", "paramétrer", "personnaliser", "adapter"],
                        "context_indicators": ["configuration", "paramétrage", "setup"]
                    },
                    "conversation_rules": {
                        "max_steps": 10,
                        "confirmation_required": true,
                        "allow_backtrack": true,
                        "timeout": 3600
                    },
                    "validation_steps": {
                        "syntax_check": {
                            "enabled": true,
                            "strict": true
                        },
                        "security_check": {
                            "enabled": true,
                            "checks": ["api_keys", "paths", "permissions"]
                        },
                        "compatibility_check": {
                            "enabled": true,
                            "verify_dependencies": true
                        }
                    },
                    "limits": {
                        "core_features": [
                            "rag_search",
                            "webdav_operations",
                            "tchap_messaging",
                            "context_handling"
                        ],
                        "extensible_features": [
                            "api_integration",
                            "custom_actions",
                            "custom_tools",
                            "custom_prompts"
                        ],
                        "restricted_operations": [
                            "system_files_access",
                            "direct_db_access",
                            "network_operations"
                        ]
                    }
                }
            },
            "prompts/config_mode.json": {
                "type": "prompt",
                "description": "Prompts pour le mode configuration",
                "priority": 0.95,
                "configuration": {
                    "base_prompt": "Je suis en mode configuration. Je vais vous guider pas à pas dans la personnalisation de Colaig selon vos besoins.",
                    "conversation_steps": {
                        "initial_assessment": {
                            "message": "Pour commencer, pouvez-vous me décrire en quelques mots ce que vous souhaitez configurer ?",
                            "follow_up": {
                                "unclear": "Je ne suis pas sûr de bien comprendre votre besoin. Pouvez-vous me donner plus de détails ?",
                                "not_possible": "Je suis désolé, mais cette configuration n'est pas possible car : {reason}. Voici ce que je peux vous proposer à la place : {alternatives}",
                                "needs_clarification": "Pour mieux vous aider, j'aurais besoin de précisions sur : {points}"
                            }
                        },
                        "feature_exploration": {
                            "core_features": "Voici les fonctionnalités de base que je peux configurer : {features}. Lesquelles vous intéressent ?",
                            "custom_features": "Je peux également créer des fonctionnalités personnalisées. Voici ce qui est possible : {possibilities}",
                            "limitations": "Important : voici les limitations à prendre en compte : {limits}"
                        },
                        "configuration_process": {
                            "step_intro": "Nous allons configurer {feature}. Voici les étapes à suivre :",
                            "parameter_request": "Pour {param}, quelle valeur souhaitez-vous utiliser ? {explanation}",
                            "validation_error": "Il y a un problème avec {param} : {error}. Voici comment le corriger : {solution}",
                            "confirmation": "Voici la configuration proposée : {config}. Est-elle conforme à vos attentes ?"
                        },
                        "documentation_request": {
                            "api_doc": "Pour intégrer cette API, j'ai besoin de sa documentation. Pouvez-vous me la fournir ?",
                            "format_example": "Voici un exemple du format attendu : {example}",
                            "missing_info": "Il manque les informations suivantes dans la documentation : {missing}"
                        },
                        "completion": {
                            "success": "La configuration est terminée et validée. Elle sera active dès la prochaine utilisation.",
                            "partial_success": "La configuration est terminée mais avec quelques compromis : {compromises}",
                            "save_confirmation": "Souhaitez-vous que j'applique cette configuration maintenant ?"
                        }
                    },
                    "error_handling": {
                        "invalid_request": "Je ne peux pas traiter cette demande car : {reason}",
                        "missing_prerequisites": "Il manque les éléments suivants : {missing}",
                        "security_warning": "Attention : {warning}",
                        "suggestion": "Suggestion : {alternative}"
                    }
                }
            }
        }
        
        for path, content in default_behaviors.items():
            full_path = self.index_dir / path
            if not os.path.exists(full_path):
                os.makedirs(os.path.dirname(full_path), exist_ok=True)
                with open(full_path, 'w', encoding='utf-8') as f:
                    json.dump(content, f, ensure_ascii=False, indent=2)
                logger.info(f"Créé fichier de comportement par défaut: {path}")

    async def search(
        self,
        query: str,
        behavior_type: Optional[str] = None,
        limit: int = 5
    ) -> List[BehaviorChunk]:
        """Recherche dans l'index comportemental avec filtrage par type"""
        try:
            # Recherche de base
            chunks = await super().search(query, limit=limit * 2)  # Doubler pour le filtrage
            
            # Convertir en BehaviorChunk
            behavior_chunks = []
            for chunk in chunks:
                if isinstance(chunk, dict):
                    chunk = BehaviorChunk(**chunk)
                    
                # Filtrer par type si spécifié
                if behavior_type and chunk.behavior_type != behavior_type:
                    continue
                    
                behavior_chunks.append(chunk)
            
            # Trier par priorité et score
            behavior_chunks.sort(
                key=lambda x: (x.metadata.get("priority", 0.0), x.metadata.get("score", 0.0)),
                reverse=True
            )
            
            return behavior_chunks[:limit]
            
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
            # Vérifier si des configurations personnalisées existent
            if not os.path.exists(self.index_dir) or not any(os.listdir(self.index_dir)):
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
            
            # 2. Détecter le style de conversation
            if session_context:
                style_config = await self._load_style_config()
                context_info["conversation_style"] = self._detect_style(query, style_config)
            
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
                base_score = config["priority"]
                
                # 1. Score basé sur les topics
                topic_overlap = len(
                    set(self._extract_topics([query])) & 
                    context_info["active_topics"]
                )
                topic_score = topic_overlap * 0.2
                
                # 2. Score basé sur le style
                style_match = (
                    config["config"]["prompt"].get("style", "") == 
                    context_info["conversation_style"]
                )
                style_score = 0.1 if style_match else 0
                
                # 3. Score basé sur les règles
                rule_match = any(
                    rule["type"] == config["intent"]
                    for rule in context_info["relevant_rules"]
                )
                rule_score = 0.15 if rule_match else 0
                
                # Score final
                final_score = base_score + topic_score + style_score + rule_score
                
                scored_intents.append({
                    "intent": config["intent"],
                    "score": final_score,
                    "config": config["config"]
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