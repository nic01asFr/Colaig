"""
Module de gestion des webhooks entrants pour Colaig.
"""

import json
import asyncio
from typing import Dict, Any, List, Optional

from app.matrix_bot.config import logger
from app.services.webhook_service import get_webhook_service, WebhookEvent

class WebhookHandler:
    """
    Gestionnaire pour traiter les webhooks entrants et router vers les salons appropriés.
    """
    
    def __init__(self, config, matrix_client=None):
        """
        Initialise le gestionnaire de webhooks entrants.
        
        Args:
            config: Configuration de l'application
            matrix_client: Client Matrix pour envoyer des messages (optionnel)
        """
        self.config = config
        self.matrix_client = matrix_client
        
        # Dictionnaire des commandes disponibles
        self.commands = {
            "handle_inbound": self._handle_inbound
        }
    
    async def handle_command(self, command: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite une commande webhook.
        
        Args:
            command: Nom de la commande à exécuter
            params: Paramètres de la commande
            
        Returns:
            Résultat de la commande
        """
        logger.info(f"Traitement de la commande webhook: {command} avec paramètres: {params}")
        
        if command not in self.commands:
            return {
                "success": False,
                "error": f"Commande '{command}' inconnue. Commandes disponibles: {', '.join(self.commands.keys())}"
            }
        
        try:
            # Exécuter la commande
            result = await self.commands[command](params)
            return result
        except Exception as e:
            logger.error(f"Erreur lors du traitement de la commande '{command}': {str(e)}")
            return {
                "success": False,
                "error": f"Erreur lors du traitement de la commande: {str(e)}"
            }
    
    async def _handle_inbound(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite un webhook entrant et envoie un message dans le salon correspondant.
        
        Args:
            params: Paramètres de la commande
                - token: Token d'authentification du webhook
                - message: Message à envoyer
                
        Returns:
            Résultat du traitement
        """
        token = params.get("token")
        message = params.get("message")
        
        if not token:
            return {"success": False, "error": "Token d'authentification manquant"}
        
        if not message:
            return {"success": False, "error": "Message manquant"}
        
        # Initialiser le service de webhooks
        webhook_service = await get_webhook_service(self.config)
        
        # Récupérer le salon associé au token
        room_id = await webhook_service.get_room_from_token(token)
        if not room_id:
            return {"success": False, "error": "Token d'authentification invalide"}
            
        if not self.matrix_client:
            return {"success": False, "error": "Client Matrix non initialisé"}
            
        try:
            # Formater le message
            formatted_message = f"""📨 **Message reçu via webhook**

{message}

---
*Token: {token[:8]}...*"""
            
            # Envoyer le message dans le salon
            await self.matrix_client.send_markdown_message(
                room_id,
                formatted_message,
                msgtype="m.notice"
            )
            
            logger.info(f"Message webhook envoyé dans le salon {room_id} avec le token {token[:8]}...")
            
            return {
                "success": True,
                "room_id": room_id,
                "message": "Message envoyé avec succès"
            }
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi du message webhook: {str(e)}")
            return {
                "success": False,
                "error": f"Erreur lors de l'envoi du message: {str(e)}"
            }
    
    async def process_custom_inbound(self, token: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Traite un webhook entrant personnalisé et envoie les données dans le salon correspondant.
        
        Args:
            token: Token d'authentification du webhook
            data: Données à envoyer
                
        Returns:
            Résultat du traitement
        """
        if not token:
            return {"success": False, "error": "Token d'authentification manquant"}
        
        # Initialiser le service de webhooks
        webhook_service = await get_webhook_service(self.config)
        
        # Récupérer le salon associé au token
        room_id = await webhook_service.get_room_from_token(token)
        if not room_id:
            return {"success": False, "error": "Token d'authentification invalide"}
            
        if not self.matrix_client:
            return {"success": False, "error": "Client Matrix non initialisé"}
            
        try:
            # Formater le message
            formatted_message = f"""📨 **Données reçues via webhook**

```json
{json.dumps(data, indent=2, ensure_ascii=False)}
```

---
*Token: {token[:8]}...*"""
            
            # Envoyer le message dans le salon
            await self.matrix_client.send_markdown_message(
                room_id,
                formatted_message,
                msgtype="m.notice"
            )
            
            logger.info(f"Données webhook envoyées dans le salon {room_id} avec le token {token[:8]}...")
            
            return {
                "success": True,
                "room_id": room_id,
                "message": "Données envoyées avec succès"
            }
        except Exception as e:
            logger.error(f"Erreur lors de l'envoi des données webhook: {str(e)}")
            return {
                "success": False,
                "error": f"Erreur lors de l'envoi des données: {str(e)}"
            } 