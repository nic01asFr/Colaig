# SPDX-FileCopyrightText: 2024 Etalab <etalab@modernisation.gouv.fr>
#
# SPDX-License-Identifier: MIT

from enum import Enum
from dataclasses import dataclass
from typing import Optional, List
import re
from nio import MatrixRoom, RoomMessageText
from app.matrix_bot.eventparser import EventParser
from app.matrix_bot.config import logger


class TchapContextType(Enum):
    DIRECT_MESSAGE = "dm"
    SALON_GENERAL = "salon_general"
    THREAD = "thread"


@dataclass
class TchapContext:
    context_type: TchapContextType
    is_mentioned: bool = False
    is_bot_participating_in_thread: bool = False
    thread_root_id: Optional[str] = None
    reply_to_id: Optional[str] = None
    mentions: List[str] = None
    should_respond: bool = False
    response_thread_id: Optional[str] = None


class TchapContextResolver:
    """
    Résolveur de contexte Tchap pour une interaction naturelle et intelligente.
    
    Logique de comportement :
    - DM : Toujours répondre, pas de thread
    - Salon général : Répondre seulement si mentionné, créer un thread depuis le message
    - Thread : Répondre si mentionné OU si Colaig participe déjà (mentionné dans le message racine)
    """
    
    def __init__(self, matrix_client):
        self.matrix_client = matrix_client
        self.bot_user_id = matrix_client.user_id
    
    async def resolve_context(self, ep: EventParser) -> TchapContext:
        """Résout le contexte Tchap pour un événement donné"""
        
        # Détection du type de contexte de base
        if self._is_direct_message(ep.room):
            context_type = TchapContextType.DIRECT_MESSAGE
        elif self._is_in_thread(ep.event):
            context_type = TchapContextType.THREAD
        else:
            context_type = TchapContextType.SALON_GENERAL
            
        # Extraction des mentions du message courant
        mentions = self._extract_mentions(ep.event)
        is_mentioned = self.bot_user_id in mentions
        
        # Récupération des IDs de thread et réponse
        thread_root_id = self._get_thread_root(ep.event)
        reply_to_id = self._get_reply_to(ep.event)
        
        # Logique spécifique pour les threads
        is_bot_participating_in_thread = False
        if context_type == TchapContextType.THREAD and thread_root_id:
            is_bot_participating_in_thread = await self._is_bot_participating_in_thread(
                ep.room.room_id, thread_root_id
            )
        
        # Création du contexte
        context = TchapContext(
            context_type=context_type,
            is_mentioned=is_mentioned,
            is_bot_participating_in_thread=is_bot_participating_in_thread,
            thread_root_id=thread_root_id,
            reply_to_id=reply_to_id,
            mentions=mentions
        )
        
        # Calcul de la décision de réponse
        context.should_respond = self._should_respond(context)
        context.response_thread_id = self._get_response_thread_id(context, ep.event.event_id)
        
        return context
    
    def _should_respond(self, context: TchapContext) -> bool:
        """Détermine si le bot devrait répondre dans ce contexte"""
        
        # En DM, toujours répondre
        if context.context_type == TchapContextType.DIRECT_MESSAGE:
            logger.info("[TCHAP_CONTEXT] DM détecté - Réponse automatique")
            return True
        
        # En thread, répondre si mentionné OU si le bot participe déjà
        if context.context_type == TchapContextType.THREAD:
            if context.is_mentioned:
                logger.info("[TCHAP_CONTEXT] Thread - Mentionné explicitement")
                return True
            elif context.is_bot_participating_in_thread:
                logger.info("[TCHAP_CONTEXT] Thread - Bot déjà participant (mentionné dans le message racine)")
                return True
            else:
                logger.info("[TCHAP_CONTEXT] Thread - Bot non concerné")
                return False
        
        # En salon général, répondre seulement si mentionné
        if context.context_type == TchapContextType.SALON_GENERAL:
            if context.is_mentioned:
                logger.info("[TCHAP_CONTEXT] Salon général - Mentionné explicitement")
                return True
            else:
                logger.info("[TCHAP_CONTEXT] Salon général - Non mentionné, pas de réponse")
                return False
        
        return False
    
    def _get_response_thread_id(self, context: TchapContext, current_event_id: str) -> Optional[str]:
        """Détermine l'ID du thread pour la réponse"""
        
        # En DM, pas de thread
        if context.context_type == TchapContextType.DIRECT_MESSAGE:
            return None
        
        # Si on est déjà dans un thread, continuer dans ce thread
        if context.context_type == TchapContextType.THREAD:
            return context.thread_root_id
        
        # En salon général, si on répond à une mention, créer un thread depuis le message
        if (context.context_type == TchapContextType.SALON_GENERAL and 
            context.is_mentioned):
            return current_event_id
        
        return None
    
    def _is_direct_message(self, room: MatrixRoom) -> bool:
        """Détermine si un salon est un message direct"""
        return len(room.users) == 2
    
    def _is_in_thread(self, event) -> bool:
        """Détermine si un événement est dans un thread"""
        if not hasattr(event, 'source') or 'content' not in event.source:
            return False
        
        content = event.source['content']
        if 'm.relates_to' not in content:
            return False
        
        relates_to = content['m.relates_to']
        return relates_to.get('rel_type') == 'm.thread'
    
    def _get_thread_root(self, event) -> Optional[str]:
        """Récupère l'ID du message racine du thread"""
        if not self._is_in_thread(event):
            return None
        
        content = event.source['content']
        relates_to = content['m.relates_to']
        return relates_to.get('event_id')
    
    def _get_reply_to(self, event) -> Optional[str]:
        """Récupère l'ID du message auquel on répond"""
        if not hasattr(event, 'source') or 'content' not in event.source:
            return None
        
        content = event.source['content']
        if 'm.relates_to' not in content:
            return None
        
        relates_to = content['m.relates_to']
        if 'm.in_reply_to' in relates_to:
            return relates_to['m.in_reply_to'].get('event_id')
        
        return None
    
    def _extract_mentions(self, event) -> List[str]:
        """Extrait les mentions d'utilisateurs du formatted_body"""
        mentions = []
        
        if not hasattr(event, 'source') or 'content' not in event.source:
            return mentions
        
        content = event.source['content']
        formatted_body = content.get('formatted_body', '')
        
        if not formatted_body:
            return mentions
        
        # Pattern pour détecter les mentions Matrix: @user:domain
        # Format typique: <a href="https://matrix.to/#/@user:domain">@user:domain</a>
        mention_pattern = r'@([a-zA-Z0-9\._-]+:[a-zA-Z0-9\._-]+)'
        mentions = re.findall(mention_pattern, formatted_body)
        
        # Normaliser pour avoir les IDs complets
        full_mentions = []
        for mention in mentions:
            if not mention.startswith('@'):
                mention = f"@{mention}"
            full_mentions.append(mention)
        
        logger.debug(f"[TCHAP_CONTEXT] Mentions extraites: {full_mentions}")
        return full_mentions
    
    async def _is_bot_participating_in_thread(self, room_id: str, thread_root_id: str) -> bool:
        """
        Vérifie si le bot participe déjà dans ce thread en vérifiant 
        si il a été mentionné dans le message racine du thread.
        """
        try:
            # Récupérer le message racine du thread
            root_message_response = await self.matrix_client.room_get_event(room_id, thread_root_id)
            
            if not root_message_response or not hasattr(root_message_response, 'event'):
                logger.warning(f"[TCHAP_CONTEXT] Impossible de récupérer le message racine {thread_root_id}")
                return False
            
            root_event = root_message_response.event
            
            # Extraire les mentions du message racine
            root_mentions = self._extract_mentions(root_event)
            
            # Vérifier si le bot était mentionné dans le message racine
            is_participating = self.bot_user_id in root_mentions
            
            logger.info(f"[TCHAP_CONTEXT] Bot {'participe' if is_participating else 'ne participe pas'} dans le thread {thread_root_id}")
            return is_participating
            
        except Exception as e:
            logger.error(f"[TCHAP_CONTEXT] Erreur lors de la vérification de participation au thread: {str(e)}")
            return False 