# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

from typing import Optional, Dict, Any
from app.services.tchap_context_resolver import TchapContext, TchapContextType
from app.matrix_bot.config import logger


class NotificationFormatter:
    """
    Formateur unifié pour les notifications Tchap.
    
    Harmonise l'affichage des messages selon le contexte :
    - Messages de notification adaptés au contexte
    - Références aux messages appropriées
    - Formatage cohérent
    """
    
    @staticmethod
    def format_notification(
        content: str,
        context: TchapContext,
        notification_type: str = "info",
        reference_event_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Formate une notification selon le contexte Tchap.
        
        Args:
            content: Le contenu du message
            context: Le contexte Tchap résolu
            notification_type: Type de notification (info, success, error, warning)
            reference_event_id: ID de l'événement de référence
            
        Returns:
            Dictionnaire avec les paramètres formatés pour l'envoi
        """
        
        # Icônes selon le type de notification
        icons = {
            "info": "ℹ️",
            "success": "✅",
            "error": "❌",
            "warning": "⚠️",
            "processing": "⏳"
        }
        
        # Préfixe selon le type
        prefix = icons.get(notification_type, "")
        formatted_content = f"{prefix} {content}" if prefix else content
        
        # Paramètres de base
        send_params = {
            "content": formatted_content,
            "msgtype": "m.notice"
        }
        
        # Gestion des références selon le contexte
        if context.context_type == TchapContextType.DIRECT_MESSAGE:
            # En DM, pas de thread, réponse directe
            if reference_event_id:
                send_params["reply_to"] = reference_event_id
                
        elif context.context_type == TchapContextType.THREAD:
            # En thread, continuer dans le thread
            send_params["thread_root"] = context.thread_root_id
            if reference_event_id:
                send_params["reply_to"] = reference_event_id
                
        elif context.context_type == TchapContextType.SALON_GENERAL:
            # En salon général, créer un thread depuis le message de référence
            if reference_event_id:
                send_params["thread_root"] = reference_event_id
                send_params["reply_to"] = reference_event_id
        
        logger.debug(f"[NOTIFICATION] Formatage: {notification_type}, "
                    f"contexte: {context.context_type.value}, "
                    f"thread_root: {send_params.get('thread_root')}")
        
        return send_params
    
    @staticmethod
    def format_command_response(
        content: str,
        context: TchapContext,
        command_name: str,
        is_success: bool = True,
        reference_event_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Formate une réponse de commande selon le contexte.
        
        Args:
            content: Le contenu de la réponse
            context: Le contexte Tchap résolu
            command_name: Nom de la commande
            is_success: Si la commande a réussi
            reference_event_id: ID de l'événement de référence
            
        Returns:
            Dictionnaire avec les paramètres formatés pour l'envoi
        """
        
        notification_type = "success" if is_success else "error"
        
        # Ajouter le contexte de la commande
        prefixed_content = f"**Commande {command_name}** : {content}"
        
        return NotificationFormatter.format_notification(
            prefixed_content,
            context,
            notification_type,
            reference_event_id
        )
    
    @staticmethod
    def format_progress_update(
        content: str,
        context: TchapContext,
        progress: Optional[str] = None,
        reference_event_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Formate une mise à jour de progression.
        
        Args:
            content: Le contenu de la mise à jour
            context: Le contexte Tchap résolu
            progress: Indicateur de progression (optionnel)
            reference_event_id: ID de l'événement de référence
            
        Returns:
            Dictionnaire avec les paramètres formatés pour l'envoi
        """
        
        if progress:
            formatted_content = f"**{progress}** : {content}"
        else:
            formatted_content = content
            
        return NotificationFormatter.format_notification(
            formatted_content,
            context,
            "processing",
            reference_event_id
        )
    
    @staticmethod
    async def send_formatted_message(
        matrix_client,
        room_id: str,
        content: str,
        context: TchapContext,
        notification_type: str = "info",
        reference_event_id: Optional[str] = None
    ):
        """
        Envoie un message formaté selon le contexte.
        
        Args:
            matrix_client: Client Matrix
            room_id: ID du salon
            content: Contenu du message
            context: Contexte Tchap résolu
            notification_type: Type de notification
            reference_event_id: ID de l'événement de référence
        """
        
        params = NotificationFormatter.format_notification(
            content, context, notification_type, reference_event_id
        )
        
        await matrix_client.send_markdown_message(
            room_id,
            params["content"],
            msgtype=params["msgtype"],
            reply_to=params.get("reply_to"),
            thread_root=params.get("thread_root")
        ) 