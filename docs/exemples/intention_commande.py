#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Exemple d'intégration du système d'intention avec les commandes Albert.

Ce script montre comment le système peut analyser une intention utilisateur
et exécuter la commande appropriée sans que l'utilisateur n'utilise explicitement
la syntaxe de commande.
"""

import asyncio
import logging
from typing import Dict, Any, Optional, List

from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser
from matrix_bot.client import MatrixClient

from app.services.behavior_index import BehaviorIndex
from app.commands.registry import command_registry, RoomMessageText
from app.services.context.instance import context_manager

# Configuration du logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("intention_commandes")


class IntentionHandler:
    """Gestionnaire d'intention pour les commandes Albert"""
    
    def __init__(self, behavior_index: BehaviorIndex, matrix_client: MatrixClient):
        self.behavior_index = behavior_index
        self.matrix_client = matrix_client
        self.command_registry = command_registry
        
    async def process_message(self, ep: EventParser) -> Optional[str]:
        """
        Traite un message utilisateur pour détecter l'intention et exécuter la commande appropriée.
        
        Args:
            ep: Parser d'événement contenant le message
            
        Returns:
            Réponse à envoyer à l'utilisateur ou None si aucune commande n'a été exécutée
        """
        # Vérifier si c'est une commande explicite (commençant par !)
        message = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
        if message.startswith("!"):
            logger.info(f"Message commençant par ! détecté, laissant le système standard le traiter")
            return None
            
        # Extraire les informations de base
        room_id = ep.room.room_id
        sender = ep.sender
        
        # Configuration pour l'analyse d'intention
        config = getattr(self.matrix_client, "albert_config", self.matrix_client.config)
        
        # Récupérer le contexte de session
        from app.commands import get_unified_session_context
        session_context = await get_unified_session_context(config, room_id, sender)
        
        # Récupérer le contexte de la salle (room)
        room_context = {}  # À implémenter selon les besoins
        
        try:
            # Analyser l'intention avec le behavior index
            logger.info(f"Analyse de l'intention pour: '{message}'")
            intent_analysis = await self.behavior_index.analyze_intent(
                query=message,
                session_context=session_context.to_dict() if hasattr(session_context, 'to_dict') else session_context,
                room_context=room_context
            )
            
            # Si aucune intention n'est détectée, retourner None
            if not intent_analysis or intent_analysis.get("detected_intent") == "standard_rag":
                logger.info(f"Aucune intention spécifique détectée, utilisant le RAG standard")
                return None
                
            # Récupérer la configuration de l'action détectée
            logger.info(f"Intention détectée: {intent_analysis.get('detected_intent')} "
                       f"(confiance: {intent_analysis.get('confidence', 0)})")
            
            action_config = intent_analysis.get("action_config", {})
            
            # Extraire le nom de la commande à exécuter
            command_name = action_config.get("configuration", {}).get("command")
            if not command_name:
                logger.warning(f"Configuration d'action sans commande associée")
                return None
                
            # Vérifier si la commande existe
            if not self.command_registry.is_valid_command(command_name):
                logger.warning(f"Commande '{command_name}' non trouvée dans le registre")
                return f"Désolé, la commande '{command_name}' n'est pas disponible."
                
            # Extraire les paramètres de la commande
            parameters = self._extract_parameters(message, action_config)
            
            # Construire un message simulant l'appel direct de la commande
            # Format: !commande param1 param2 ...
            simulated_command = f"!{command_name}"
            if "query" in parameters:
                simulated_command += f" {parameters['query']}"
            elif parameters:
                # Ajouter d'autres paramètres selon le format attendu par la commande
                for param_name, param_value in parameters.items():
                    if param_name != "command":
                        simulated_command += f" {param_value}"
            
            logger.info(f"Exécution de la commande simulée: '{simulated_command}'")
            
            # Créer un événement simulé avec la commande
            from nio import RoomMessageText as NioRoomMessageText
            simulated_event = NioRoomMessageText(
                room_id=room_id,
                sender=sender,
                body=simulated_command,
                formatted_body=None,
                msgtype="m.room.message"
            )
            simulated_event.event_id = ep.event.event_id
            
            # Créer un EventParser simulé
            from matrix_bot.room import Room
            simulated_ep = EventParser(
                event=simulated_event,
                room=ep.room,
                sender=sender
            )
            
            # Trouver la fonction associée à la commande
            cmd_func = None
            for name, feature in self.command_registry.function_register.items():
                if name in self.command_registry.activated_functions:
                    if feature.get("commands") and command_name in feature["commands"]:
                        cmd_func = feature["func"]
                        break
            
            if not cmd_func:
                logger.error(f"Fonction pour la commande '{command_name}' non trouvée")
                return f"Désolé, je n'ai pas pu exécuter la commande '{command_name}'."
            
            # Exécuter la commande
            try:
                result = await cmd_func(simulated_ep, self.matrix_client)
                
                # Formater la réponse selon le template si disponible
                if isinstance(result, str) and action_config.get("configuration", {}).get("response_templates", {}).get("success"):
                    template = action_config["configuration"]["response_templates"]["success"]
                    return template.replace("{{result}}", result)
                
                return result
            except Exception as e:
                logger.error(f"Erreur lors de l'exécution de la commande '{command_name}': {str(e)}")
                
                # Utiliser le template d'erreur si disponible
                error_template = action_config.get("configuration", {}).get("response_templates", {}).get("error")
                if error_template:
                    return error_template.replace("{{error_message}}", str(e))
                
                return f"Désolé, une erreur s'est produite lors de l'exécution de la commande: {str(e)}"
        
        except Exception as e:
            logger.error(f"Erreur lors du traitement de l'intention: {str(e)}")
            return f"Désolé, une erreur s'est produite lors de l'analyse de votre demande."
    
    def _extract_parameters(self, message: str, action_config: Dict[str, Any]) -> Dict[str, str]:
        """
        Extrait les paramètres de la commande à partir du message utilisateur.
        
        Args:
            message: Message utilisateur
            action_config: Configuration de l'action
            
        Returns:
            Dictionnaire des paramètres extraits
        """
        parameters = {}
        
        # Récupérer les définitions des paramètres
        param_defs = action_config.get("configuration", {}).get("parameters", [])
        
        # Si aucun paramètre n'est défini, utiliser le message complet comme query
        if not param_defs:
            parameters["query"] = message
            return parameters
        
        # Pour l'exemple, utiliser simplement tout le message comme requête
        # Dans une implémentation réelle, un extracteur plus sophistiqué serait utilisé
        for param_def in param_defs:
            if param_def["name"] == "query":
                parameters["query"] = message
                
        return parameters


# Exemple d'utilisation
async def main():
    """Fonction principale pour démonstration."""
    # Créer le client Matrix et configurer l'analyse d'intention
    from app.config import env_config
    from app.services.webdav import WebDAVService
    from app.services.behavior_index import BehaviorIndex
    
    # Initialiser le client Matrix (simulé pour l'exemple)
    class MockMatrixClient:
        def __init__(self):
            self.albert_config = env_config
            
        async def send_markdown_message(self, room_id, message, **kwargs):
            print(f"[ROOM {room_id}] {message}")
    
    matrix_client = MockMatrixClient()
    
    # Initialiser le service WebDAV
    webdav_service = WebDAVService(env_config)
    await webdav_service.initialize()
    
    # Initialiser l'index de behavior
    behavior_index = BehaviorIndex(env_config, webdav_service)
    await behavior_index.initialize()
    
    # Créer le gestionnaire d'intention
    intention_handler = IntentionHandler(behavior_index, matrix_client)
    
    # Simuler quelques messages utilisateur
    messages = [
        "!aide",  # Commande explicite, sera ignorée par l'IntentionHandler
        "Cherche des informations sur les congés payés",  # Devrait exécuter !chercher
        "Je voudrais savoir ce que disent les documents à propos du RGPD",  # Devrait exécuter !chercher
        "Bonjour Albert, comment vas-tu ?",  # Pas de commande associée
        "Est-ce que tu peux m'aider à comprendre les procédures d'urgence ?"  # Pourrait exécuter !chercher
    ]
    
    # Simuler un EventParser et traiter les messages
    for msg in messages:
        # Créer un événement simulé
        from nio import RoomMessageText as NioRoomMessageText
        from matrix_bot.room import Room
        event = NioRoomMessageText(
            room_id="!room:example.com",
            sender="@user:example.com",
            body=msg,
            formatted_body=None,
            msgtype="m.room.message"
        )
        event.event_id = f"$event{hash(msg)}"
        
        # Créer un EventParser simulé
        room = Room("!room:example.com", "Test Room")
        ep = EventParser(
            event=event,
            room=room,
            sender="@user:example.com"
        )
        
        # Traiter le message
        print(f"\n--- Message: {msg}")
        result = await intention_handler.process_message(ep)
        
        if result:
            print(f"Réponse: {result}")
        else:
            print("Message traité par le système standard ou ignoré")


if __name__ == "__main__":
    # Exécuter la démo
    asyncio.run(main()) 