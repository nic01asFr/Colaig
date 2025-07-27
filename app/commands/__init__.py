"""
Package de gestion des commandes Albert.

Ce package contient toutes les commandes disponibles dans Albert.
Les commandes sont organisées par modules thématiques.
"""

import time

# Exporter uniquement les classes et fonctions essentielles du registre
from .registry import (
    CommandRegistry,
    register_feature,
    only_allowed_user,
    command_registry,
)

# Import du logger pour les logs
from app.matrix_bot.config import logger

# Importer le gestionnaire de contexte directement
from app.services.context.instance import get_context_manager, ensure_initialized, get_or_create_session_context
from app.services.context.types import ContextType
from app.services.context.models import SessionContext, RoomContext

# Fonction utilitaire pour mettre à jour l'historique des conversations
async def update_conversation_history(config, room_id, sender, role, user_message, timestamp=None):
    """
    Ajoute un message à l'historique de conversation.
    
    Args:
        config: Configuration Albert
        room_id: ID de la salle
        sender: ID de l'expéditeur
        role: Rôle ('user' ou 'assistant')
        user_message: Contenu du message
        timestamp: Horodatage du message (optionnel)
    """
    try:
        # Utiliser la nouvelle méthode pour obtenir le contexte de session
        session_context = await get_or_create_session_context(config, room_id, sender)
        
        # Ajouter le message à l'historique
        if session_context:
            session_context.add_message(role, user_message, timestamp)
            logger.debug(f"Message ajouté à l'historique pour {sender} dans {room_id}")
    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour de l'historique: {str(e)}")

# Fonction pour obtenir le contexte de session unifié
async def get_unified_session_context(config, room_id, user_id):
    """
    Récupère le contexte de session unifié pour un utilisateur dans une salle.
    
    Args:
        config: Configuration
        room_id: ID de la salle
        user_id: ID de l'utilisateur
        
    Returns:
        Le contexte de session pour cet utilisateur et cette salle
    """
    try:
        # Utiliser la nouvelle fonction unifiée
        return await get_or_create_session_context(config, room_id, user_id)
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du contexte unifié: {str(e)}")
        # Créer un contexte vide en cas d'erreur
        return SessionContext(
            session_id=f"{room_id}_{user_id}",
            room_id=room_id,
            user_id=user_id
        )

# Fonction pour mettre à jour le contexte de session unifié
async def update_unified_session_context(room_id, user_id, context):
    """
    Met à jour le contexte de session unifié pour un utilisateur dans une salle.
    
    Args:
        room_id: ID de la salle
        user_id: ID de l'utilisateur
        context: Le contexte de session à mettre à jour
        
    Returns:
        True si la mise à jour a réussi, False sinon
    """
    try:
        # Obtenir le gestionnaire de contexte
        context_manager = await get_context_manager()
        
        # Mettre à jour le contexte
        session_id = f"{room_id}_{user_id}"
        await context_manager.update_context(session_id, ContextType.SESSION, context)
        return True
    except Exception as e:
        logger.error(f"Erreur lors de la mise à jour du contexte unifié: {str(e)}")
        return False

# Export des fonctions utiles
__all__ = [
    "register_feature",
    "only_allowed_user", 
    "command_registry",
    "get_context_manager",
    "ensure_initialized",
    "get_unified_session_context",
    "update_unified_session_context",
    "update_conversation_history",
    "get_room_context",
]

# NOTE IMPORTANTE:
# Les commandes individuelles ne sont PAS importées automatiquement ici.
# Cette approche évite les importations circulaires et les enregistrements en double.
# 
# Pour utiliser une commande, importez-la directement depuis son module:
# from app.commands.basic_commands import help
# from app.commands.document_commands.docquery_adapted import doc_query_adapted_command
# etc.

# Pour la phase de transition
def get_registry():
    """Retourne l'instance unique du registre de commandes."""
    return command_registry 

async def get_behavior_manager_for_context(config, room_id=None, user_id=None, context_path=None):
    """
    Récupère l'instance du BehaviorManager appropriée pour un contexte donné.
    
    Args:
        config: Configuration de l'application
        room_id: ID de la salle (optionnel)
        user_id: ID de l'utilisateur (optionnel)
        context_path: Chemin du contexte WebDAV (optionnel)
        
    Returns:
        Une instance de BehaviorManager adaptée au contexte
    """
    from app.services.behavior_manager import get_behavior_manager
    
    # Si un chemin de contexte est explicitement fourni, l'utiliser
    if context_path:
        return await get_behavior_manager(config, context_path=context_path)
    
    # Si room_id est fourni, essayer de déterminer le contexte à partir du salon
    if room_id:
        try:
            # Récupérer le contexte de la salle
            room_context = await get_room_context(config, room_id)
            if room_context and hasattr(room_context, 'webdav_context'):
                webdav_context = room_context.webdav_context
                if webdav_context:
                    return await get_behavior_manager(config, context_path=webdav_context)
        except Exception as e:
            logger.warning(f"Erreur lors de la récupération du contexte de la salle {room_id}: {str(e)}")
    
    # Par défaut, utiliser l'instance globale
    return await get_behavior_manager(config) 

# Fonction utilitaire pour obtenir le contexte d'une salle
async def get_room_context(config, room_id) -> RoomContext:
    """
    Récupère le contexte d'une salle.
    
    Args:
        config: Configuration
        room_id: ID de la salle
        
    Returns:
        Le contexte de la salle ou None si non trouvé
    """
    try:
        ctx_manager = await get_context_manager(config)
        return await ctx_manager.get_context(room_id, ContextType.ROOM)
    except Exception as e:
        logger.error(f"Erreur lors de la récupération du contexte de la salle {room_id}: {str(e)}")
        return None 