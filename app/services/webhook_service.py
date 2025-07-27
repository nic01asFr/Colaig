"""
Service de gestion des webhooks pour Colaig.
Permet d'envoyer et recevoir des informations via des webhooks pour l'intégration avec n8n.
"""

import json
import aiohttp
import logging
import os
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
import uuid
import hmac
import hashlib
import base64
from pydantic import BaseModel, Field
import secrets

from app.config import Config
from app.matrix_bot.config import logger
from app.services.context.types import ContextType

class WebhookConfig(BaseModel):
    """Configuration pour un webhook."""
    url: str
    method: str = "POST"
    headers: Dict[str, str] = Field(default_factory=dict)
    auth_token: Optional[str] = None
    secret: Optional[str] = None
    timeout: int = 30

class WebhookEvent(BaseModel):
    """Événement webhook à envoyer."""
    event_type: str
    payload: Dict[str, Any]
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now().isoformat())

class WebhookResult(BaseModel):
    """Résultat de l'envoi d'un webhook."""
    success: bool
    status_code: Optional[int] = None
    response: Optional[Dict[str, Any]] = None
    error: Optional[str] = None

class WebhookRegistration(BaseModel):
    """Enregistrement d'un webhook entrant."""
    room_id: str
    token: str
    description: Optional[str] = None
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())

class WebhookService:
    """
    Service de gestion des webhooks pour Colaig.
    Permet d'envoyer et recevoir des informations via des webhooks.
    """
    
    def __init__(self, config: Config):
        """
        Initialise le service de webhooks.
        
        Args:
            config: Configuration de l'application
        """
        self.config = config
        self._context_manager = None
        self._webdav_manager = None
    
    async def _get_webdav_for_room(self, room_id):
        """Obtient le service WebDAV et le chemin pour la salle spécifiée."""
        # Initialiser le gestionnaire de contexte si nécessaire
        if not hasattr(self, '_context_manager') or self._context_manager is None:
            from app.services.context.instance import get_context_manager
            self._context_manager = await get_context_manager(self.config)
        
        # Initialiser le gestionnaire WebDAV si nécessaire
        if not hasattr(self, '_webdav_manager') or self._webdav_manager is None:
            from app.services.webdav_context_manager import get_webdav_context_manager
            self._webdav_manager = await get_webdav_context_manager(self.config)
        
        # Obtenir le service WebDAV par défaut dès le début 
        # pour l'utiliser en cas d'échec d'autres méthodes
        default_service = await self._webdav_manager.get_default_service()
        if not default_service:
            logger.error(f"Impossible d'obtenir le service WebDAV par défaut")
            return None, None
            
        # Obtenir le contexte de la salle
        room_context = None
        try:
            room_context = await self._context_manager.get_context(room_id, ContextType.ROOM)
        except Exception as e:
            logger.warning(f"Erreur lors de la récupération du contexte de la salle {room_id}: {str(e)}")
        
        # Obtenir le chemin WebDAV
        webdav_path = None
        if room_context:
            # Essayer d'abord webdav_context (propriété principale)
            if hasattr(room_context, 'webdav_context') and room_context.webdav_context:
                webdav_path = room_context.webdav_context
            # Essayer ensuite shared_context (où certaines commandes stockent le chemin)
            elif hasattr(room_context, 'shared_context') and room_context.shared_context.get('webdav_context'):
                webdav_path = room_context.shared_context.get('webdav_context')
        
        if not webdav_path:
            logger.info(f"Aucun contexte WebDAV trouvé pour la salle {room_id}, utilisation du service par défaut")
            
            # Utiliser un chemin par défaut basé sur l'ID de la salle pour stocker les webhooks
            # Format compatible avec le reste du système
            default_path = f"rooms/{room_id}"
            
            # Créer le répertoire par défaut si nécessaire
            default_webhook_dir = f"{default_path}/.albert/webhooks"
            try:
                # La méthode create_directory gère déjà la création des répertoires parents
                await default_service.create_directory(default_webhook_dir)
                logger.info(f"Répertoire WebDAV par défaut créé pour la salle {room_id}: {default_webhook_dir}")
            except Exception as e:
                logger.warning(f"Erreur lors de la création du répertoire par défaut: {str(e)}")
                # Même en cas d'erreur, on continue avec le service et le chemin par défaut
                # car les opérations suivantes vérifieront l'existence des fichiers/dossiers
            
            return default_service, default_path
        
        # Obtenir le service WebDAV pour ce chemin
        try:
            webdav_service = await self._webdav_manager.get_service_for_context(webdav_path)
            if not webdav_service:
                logger.warning(f"Impossible d'obtenir le service WebDAV pour le chemin {webdav_path}, utilisation du service par défaut")
                return default_service, webdav_path
            
            return webdav_service, webdav_path
        except Exception as e:
            logger.warning(f"Erreur lors de l'obtention du service WebDAV: {str(e)}, utilisation du service par défaut")
            return default_service, webdav_path
        
    async def ensure_initialized(self):
        """Assure que le service est initialisé avec les services requis."""
        try:
            # Initialiser le gestionnaire de contexte si nécessaire
            if not hasattr(self, '_context_manager') or self._context_manager is None:
                from app.services.context.instance import get_context_manager
                self._context_manager = await get_context_manager(self.config)
                logger.info("Gestionnaire de contexte initialisé pour WebhookService")
            
            # Initialiser le gestionnaire WebDAV si nécessaire
            if not hasattr(self, '_webdav_manager') or self._webdav_manager is None:
                from app.services.webdav_context_manager import get_webdav_context_manager
                self._webdav_manager = await get_webdav_context_manager(self.config)
                logger.info("Gestionnaire WebDAV initialisé pour WebhookService")
                
            # Vérifier que nous pouvons obtenir le service WebDAV par défaut
            default_service = await self._webdav_manager.get_default_service()
            if not default_service:
                logger.warning("Service WebDAV par défaut non disponible, fonctionnalité webhook limitée")
            else:
                logger.info("Service WebDAV par défaut disponible pour WebhookService")
                
            return True
        except Exception as e:
            logger.error(f"Erreur lors de l'initialisation du service webhook: {str(e)}")
            # Ne pas faire échouer l'initialisation, fonctionner en mode dégradé
            return True
    
    async def add_webhook(self, name: str, webhook_config: WebhookConfig, room_id: str) -> bool:
        """
        Ajoute ou met à jour une configuration de webhook.
        
        Args:
            name: Nom du webhook
            webhook_config: Configuration du webhook
            room_id: ID du salon
            
        Returns:
            True si l'opération a réussi, False sinon
        """
        try:
            # S'assurer que les services sont initialisés
            await self.ensure_initialized()
            
            # Obtenir le service WebDAV et le chemin pour cette salle
            webdav_service, webdav_path = await self._get_webdav_for_room(room_id)
            if not webdav_service:
                logger.error(f"Impossible d'obtenir un service WebDAV pour la salle {room_id}")
                return False
            
            # Chemin du fichier webhook
            webhook_dir = f"{webdav_path}/.albert/webhooks"
            webhook_file = f"{webhook_dir}/config.json"
            
            # Charger les webhooks existants
            webhooks = {}
            try:
                if await webdav_service.exists(webhook_file):
                    content = await webdav_service.read_document(webhook_file)
                    webhooks = json.loads(content)
                    logger.info(f"Chargé {len(webhooks)} webhooks existants depuis {webhook_file}")
            except Exception as e:
                logger.warning(f"Erreur lors du chargement des webhooks existants: {str(e)}, création d'un nouveau fichier")
                # Continuer avec un dictionnaire vide
            
            # Ajouter le nouveau webhook
            webhooks[name] = webhook_config.model_dump()
            
            # Créer le répertoire si nécessaire
            try:
                # La méthode create_directory gère déjà la création des répertoires parents
                await webdav_service.create_directory(webhook_dir)
                logger.info(f"Répertoire webhooks créé: {webhook_dir}")
            except Exception as e:
                logger.error(f"Erreur lors de la création du répertoire webhooks {webhook_dir}: {str(e)}")
                return False
            
            # Sauvegarder le fichier
            try:
                await webdav_service.write_file(webhook_file, json.dumps(webhooks, ensure_ascii=False, indent=2))
                logger.info(f"Configuration de webhook '{name}' ajoutée avec succès dans {webdav_path}")
                return True
            except Exception as e:
                logger.error(f"Erreur lors de l'écriture du fichier webhook {webhook_file}: {str(e)}")
                return False
                
        except Exception as e:
            logger.error(f"Erreur lors de l'ajout de la configuration de webhook '{name}': {str(e)}")
            return False
    
    async def remove_webhook(self, name: str, room_id: str) -> bool:
        """
        Supprime une configuration de webhook.
        
        Args:
            name: Nom du webhook à supprimer
            room_id: ID du salon
            
        Returns:
            True si l'opération a réussi, False sinon
        """
        try:
            # Obtenir le service WebDAV et le chemin pour cette salle
            webdav_service, webdav_path = await self._get_webdav_for_room(room_id)
            if not webdav_service:
                return False
            
            # Chemin du fichier webhook
            webhook_file = f"{webdav_path}/.albert/webhooks/config.json"
            
            # Vérifier si le fichier existe
            if not await webdav_service.exists(webhook_file):
                return False
            
            # Charger les webhooks
            content = await webdav_service.read_document(webhook_file)
            webhooks = json.loads(content)
            
            # Vérifier si le webhook existe
            if name not in webhooks:
                return False
            
            # Supprimer le webhook
            del webhooks[name]
            
            # Sauvegarder le fichier
            await webdav_service.write_file(webhook_file, json.dumps(webhooks, ensure_ascii=False, indent=2))
            
            logger.info(f"Configuration de webhook '{name}' supprimée avec succès de {webdav_path}")
            return True
        except Exception as e:
            logger.error(f"Erreur lors de la suppression de la configuration de webhook '{name}': {str(e)}")
            return False
    
    async def get_webhook_config(self, name: str, room_id: str) -> Optional[WebhookConfig]:
        """
        Récupère une configuration de webhook par son nom.
        
        Args:
            name: Nom du webhook
            room_id: ID du salon
            
        Returns:
            Configuration du webhook ou None si non trouvée
        """
        try:
            # Obtenir le service WebDAV et le chemin pour cette salle
            webdav_service, webdav_path = await self._get_webdav_for_room(room_id)
            if not webdav_service:
                logger.error(f"Impossible d'obtenir un service WebDAV pour la salle {room_id}")
                return None
            
            # Chemin du fichier webhook
            webhook_file = f"{webdav_path}/.albert/webhooks/config.json"
            
            # Vérifier si le fichier existe
            if not await webdav_service.exists(webhook_file):
                logger.warning(f"Fichier de webhooks non trouvé: {webhook_file}")
                return None
            
            # Charger les webhooks
            try:
                content = await webdav_service.read_document(webhook_file)
                webhooks = json.loads(content)
            except Exception as e:
                logger.error(f"Erreur lors de la lecture des webhooks: {str(e)}")
                return None
            
            # Récupérer le webhook spécifié
            webhook_data = webhooks.get(name)
            if not webhook_data:
                logger.warning(f"Webhook '{name}' non trouvé")
                return None
                
            # Créer l'objet WebhookConfig
            return WebhookConfig(**webhook_data)
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération de la configuration du webhook '{name}': {str(e)}")
            return None
    
    async def list_webhooks(self, room_id: str) -> Dict[str, WebhookConfig]:
        """
        Liste tous les webhooks configurés pour une salle.
        
        Args:
            room_id: ID du salon
            
        Returns:
            Dictionnaire de webhooks {nom: config}
        """
        try:
            # Obtenir le service WebDAV et le chemin pour cette salle
            webdav_service, webdav_path = await self._get_webdav_for_room(room_id)
            if not webdav_service:
                logger.error(f"Impossible d'obtenir un service WebDAV pour la salle {room_id}")
                return {}
            
            # Chemin du fichier webhook
            webhook_file = f"{webdav_path}/.albert/webhooks/config.json"
            
            # Vérifier si le fichier existe
            if not await webdav_service.exists(webhook_file):
                logger.info(f"Fichier de webhooks non trouvé: {webhook_file}")
                return {}
            
            # Charger les webhooks
            try:
                content = await webdav_service.read_document(webhook_file)
                webhooks_dict = json.loads(content)
            except Exception as e:
                logger.error(f"Erreur lors de la récupération des configurations de webhooks: {str(e)}")
                return {}
            
            # Convertir en objets WebhookConfig
            result = {}
            for name, config in webhooks_dict.items():
                try:
                    result[name] = WebhookConfig(**config)
                except Exception as e:
                    logger.warning(f"Erreur lors de la conversion du webhook '{name}': {str(e)}")
                    # Ignorer ce webhook
            
            return result
            
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des webhooks: {str(e)}")
            return {}
    
    async def register_incoming_webhook(self, description: str, room_id: str) -> Optional[Dict[str, Any]]:
        """
        Enregistre un nouveau webhook entrant.
        
        Args:
            description: Description du webhook
            room_id: ID du salon
            
        Returns:
            Webhook enregistré ou None si échec
        """
        try:
            # Obtenir le service WebDAV et le chemin pour cette salle
            webdav_service, webdav_path = await self._get_webdav_for_room(room_id)
            if not webdav_service:
                logger.error(f"Impossible d'obtenir un service WebDAV pour la salle {room_id}")
                return None
            
            # Chemin du fichier registry
            webhook_dir = f"{webdav_path}/.albert/webhooks"
            registry_file = f"{webhook_dir}/registry.json"
            
            # Créer le répertoire si nécessaire
            try:
                await webdav_service.create_directory(webhook_dir)
            except Exception as e:
                logger.error(f"Erreur lors de la création du répertoire webhooks: {str(e)}")
                return None
            
            # Charger les webhooks existants
            registry = []
            try:
                if await webdav_service.exists(registry_file):
                    content = await webdav_service.read_document(registry_file)
                    registry = json.loads(content)
            except Exception as e:
                logger.warning(f"Erreur lors du chargement du registre de webhooks: {str(e)}, création d'un nouveau registre")
                # Continuer avec une liste vide
            
            # Générer un nouveau token
            token = secrets.token_hex(16)
            
            # Créer le webhook entrant
            webhook = {
                'token': token,
                'description': description,
                'room_id': room_id,
                'created_at': datetime.now().isoformat()
            }
            
            # Ajouter à la liste
            registry.append(webhook)
            
            # Enregistrer le fichier mis à jour
            try:
                await webdav_service.write_file(registry_file, json.dumps(registry, ensure_ascii=False, indent=2))
                logger.info(f"Webhook entrant enregistré avec succès: {token}")
                return webhook
            except Exception as e:
                logger.error(f"Erreur lors de l'écriture du fichier de registre: {str(e)}")
                return None
                
        except Exception as e:
            logger.error(f"Erreur lors de l'enregistrement du webhook entrant: {str(e)}")
            return None
    
    async def unregister_incoming_webhook(self, token: str, room_id: str) -> bool:
        """
        Supprime un webhook entrant.
        
        Args:
            token: Token d'authentification du webhook
            room_id: ID du salon
            
        Returns:
            True si l'opération a réussi, False sinon
        """
        try:
            # Obtenir le service WebDAV et le chemin pour cette salle
            webdav_service, webdav_path = await self._get_webdav_for_room(room_id)
            if not webdav_service:
                return False
            
            # Chemin du fichier de registre
            registry_file = f"{webdav_path}/.albert/webhooks/registry.json"
            
            # Vérifier si le fichier existe
            if not await webdav_service.exists(registry_file):
                return False
            
            # Charger le registre
            content = await webdav_service.read_document(registry_file)
            registry = json.loads(content)
            
            # Vérifier si le token existe
            if token not in registry:
                return False
            
            # Supprimer le token
            del registry[token]
            
            # Sauvegarder le registre
            await webdav_service.write_file(registry_file, json.dumps(registry, ensure_ascii=False, indent=2))
            
            logger.info(f"Webhook entrant supprimé avec succès")
            return True
        except Exception as e:
            logger.error(f"Erreur lors de la suppression du webhook entrant: {str(e)}")
            return False
    
    async def get_room_from_token(self, token: str) -> Optional[str]:
        """
        Récupère l'ID du salon associé à un token de webhook.
        
        Args:
            token: Token d'authentification du webhook
            
        Returns:
            ID du salon ou None si non trouvé
        """
        # Pour cette méthode, nous devons parcourir tous les salons car nous ne savons pas
        # à quel salon appartient le token. Ce n'est pas optimal mais c'est la seule solution
        # sans maintenir une table de correspondance globale.
        try:
            # Obtenir tous les contextes de salon
            from app.services.context.instance import get_context_manager
            context_manager = await get_context_manager(self.config)
            
            # Obtenir tous les contextes de salon
            room_contexts = await context_manager.list_contexts(ContextType.ROOM)
            
            # Pour chaque salon, vérifier si le token existe dans son registre
            for room_id, _ in room_contexts.items():
                webdav_service, webdav_path = await self._get_webdav_for_room(room_id)
                if not webdav_service or not webdav_path:
                    continue
                
                registry_file = f"{webdav_path}/.albert/webhooks/registry.json"
                if not await webdav_service.exists(registry_file):
                    continue
                
                content = await webdav_service.read_document(registry_file)
                registry = json.loads(content)
                
                if token in registry:
                    return room_id
            
            return None
        except Exception as e:
            logger.error(f"Erreur lors de la récupération du salon associé au token: {str(e)}")
            return None
    
    async def list_incoming_webhooks(self, room_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Liste tous les webhooks entrants.
        
        Args:
            room_id: Filtrer par ID de salon (optionnel)
            
        Returns:
            Liste des webhooks entrants
        """
        result = []
        
        try:
            if room_id:
                # Obtenir les webhooks pour un salon spécifique
                webdav_service, webdav_path = await self._get_webdav_for_room(room_id)
                if not webdav_service:
                    return []
                
                registry_file = f"{webdav_path}/.albert/webhooks/registry.json"
                if not await webdav_service.exists(registry_file):
                    return []
                
                content = await webdav_service.read_document(registry_file)
                registry = json.loads(content)
                
                for token, registration in registry.items():
                    result.append({
                        "token": token,
                        "room_id": registration["room_id"],
                        "description": registration.get("description"),
                        "created_at": registration.get("created_at"),
                        "url": f"{self.config.webhook_base_url}/api/webhooks/inbound?token={token}" if hasattr(self.config, "webhook_base_url") else None
                    })
            else:
                # Obtenir tous les webhooks de tous les salons
                from app.services.context.instance import get_context_manager
                context_manager = await get_context_manager(self.config)
                
                room_contexts = await context_manager.list_contexts(ContextType.ROOM)
                
                for room_id, _ in room_contexts.items():
                    # Ajouter les webhooks de ce salon à la liste
                    room_webhooks = await self.list_incoming_webhooks(room_id)
                    result.extend(room_webhooks)
            
            return result
        except Exception as e:
            logger.error(f"Erreur lors de la récupération des webhooks entrants: {str(e)}")
            return []
    
    def _generate_signature(self, payload: str, secret: str) -> str:
        """
        Génère une signature HMAC pour le payload.
        
        Args:
            payload: Payload en JSON
            secret: Clé secrète
            
        Returns:
            Signature encodée en base64
        """
        signature = hmac.new(
            secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).digest()
        return base64.b64encode(signature).decode('utf-8')
    
    async def send_webhook(self, name: str, event: WebhookEvent, room_id: str) -> WebhookResult:
        """
        Envoie un événement à un webhook configuré.
        
        Args:
            name: Nom du webhook
            event: Événement à envoyer
            room_id: ID du salon
            
        Returns:
            Résultat de l'envoi
        """
        # Récupérer la configuration du webhook
        webhook_config = await self.get_webhook_config(name, room_id)
        if not webhook_config:
            return WebhookResult(
                success=False,
                error=f"Configuration de webhook '{name}' non trouvée"
            )
        
        try:
            # Préparer les données
            payload = event.model_dump()
            json_payload = json.dumps(payload)
            
            # Préparer les headers
            headers = dict(webhook_config.headers or {})
            headers["Content-Type"] = "application/json"
            
            # Ajouter le token d'authentification si présent
            if webhook_config.auth_token:
                headers["Authorization"] = f"Bearer {webhook_config.auth_token}"
            
            # Ajouter la signature si un secret est configuré
            if webhook_config.secret:
                signature = self._generate_signature(json_payload, webhook_config.secret)
                headers["X-Signature"] = signature
            
            # Envoyer la requête
            async with aiohttp.ClientSession() as session:
                request_method = getattr(session, webhook_config.method.lower())
                
                async with request_method(
                    webhook_config.url,
                    headers=headers,
                    json=payload,
                    timeout=webhook_config.timeout
                ) as response:
                    status_code = response.status
                    response_data = await response.json() if response.content_type == "application/json" else await response.text()
                    
                    if 200 <= status_code < 300:
                        logger.info(f"Webhook '{name}' envoyé avec succès, statut: {status_code}")
                        return WebhookResult(
                            success=True,
                            status_code=status_code,
                            response=response_data if isinstance(response_data, dict) else {"data": response_data}
                        )
                    else:
                        logger.warning(f"Échec de l'envoi du webhook '{name}', statut: {status_code}")
                        return WebhookResult(
                            success=False,
                            status_code=status_code,
                            error=f"Erreur HTTP {status_code}",
                            response=response_data if isinstance(response_data, dict) else {"data": response_data}
                        )
        except aiohttp.ClientError as e:
            logger.error(f"Erreur de connexion lors de l'envoi du webhook '{name}': {str(e)}")
            return WebhookResult(
                success=False,
                error=f"Erreur de connexion: {str(e)}"
            )
        except Exception as e:
            logger.error(f"Erreur inattendue lors de l'envoi du webhook '{name}': {str(e)}")
            import traceback
            logger.error(f"Détails de l'erreur: {traceback.format_exc()}")
            return WebhookResult(
                success=False,
                error=f"Erreur inattendue: {str(e)}"
            )

# Instance singleton du service
_webhook_service_instance = None

async def get_webhook_service(config: Config) -> WebhookService:
    """
    Récupère l'instance du service de webhooks.
    
    Args:
        config: Configuration de l'application
        
    Returns:
        Instance du service de webhooks
    """
    global _webhook_service_instance
    if _webhook_service_instance is None:
        _webhook_service_instance = WebhookService(config)
        await _webhook_service_instance.ensure_initialized()
    
    return _webhook_service_instance 