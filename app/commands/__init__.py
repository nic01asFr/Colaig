"""
Package de gestion des commandes Albert.

Ce package contient toutes les commandes disponibles dans Albert.
Les commandes sont organisées par modules thématiques.
"""

# Exporter uniquement les classes et fonctions essentielles du registre
from .registry import (
    CommandRegistry,
    register_feature,
    only_allowed_user,
    command_registry,
)

# Import du logger pour les logs
from matrix_bot.config import logger

# Fonction utilitaire pour obtenir le gestionnaire de contexte
def get_context_manager(config):
    """Récupère le gestionnaire de contexte."""
    from app.services.context.instance import context_manager
    return context_manager

# Fonctions pour la gestion unifiée de l'historique des conversations
from app.services.context.instance import context_manager, ensure_initialized
from app.services.context.types import ContextType
from app.services.context.models import SessionContext

async def get_unified_session_context(config, room_id: str, sender: str) -> SessionContext:
    """
    Récupère ou crée un contexte de session unifié.
    Utilise une seule instance du gestionnaire de contexte.
    
    Args:
        config: Configuration 
        room_id: ID du salon
        sender: ID de l'expéditeur
        
    Returns:
        Le contexte de session récupéré ou créé
    """
    # S'assurer que le gestionnaire de contexte est initialisé
    await ensure_initialized()
    
    # Générer l'ID de session unique
    session_id = f"{room_id}_{sender}"
    
    # Récupérer le contexte existant
    session_context = await context_manager.get_context(session_id, ContextType.SESSION)
    
    # Créer le contexte s'il n'existe pas
    if not session_context:
        session_context = await context_manager.create_context(
            session_id, 
            ContextType.SESSION,
            {
                "session_id": session_id,
                "room_id": room_id,
                "user_id": sender,
                "conversation_state": {},
                "history": []
            }
        )
    
    return session_context

async def update_conversation_history(config, room_id, sender, user_message=None, bot_response=None):
    """
    Met à jour l'historique de conversation en ajoutant les messages.
    Préserve un historique suffisant pour maintenir le contexte.
    
    Args:
        config: Configuration 
        room_id: ID du salon
        sender: ID de l'expéditeur
        user_message: Message utilisateur à ajouter (optionnel)
        bot_response: Réponse du bot à ajouter (optionnel)
        
    Returns:
        Le contexte de session mis à jour
    """
    # Récupérer le contexte de session
    session_context = await get_unified_session_context(config, room_id, sender)
    
    # Ajouter le message utilisateur si fourni
    if user_message:
        session_context.add_message("user", user_message)
    
    # Ajouter la réponse du bot si fournie
    if bot_response:
        session_context.add_message("assistant", bot_response)
    
    # MODIFICATION: Assurer que l'historique ne devient pas trop grand
    # tout en préservant suffisamment de contexte
    if len(session_context.history) > 40:  # Limite conservatrice de messages
        # Conserver le début (contexte initial) et la fin (contexte récent)
        session_context.history = session_context.history[:5] + session_context.history[-30:]
        logger.debug(f"Historique de conversation tronqué à {len(session_context.history)} messages")
    
    # Mettre à jour le contexte
    session_id = f"{room_id}_{sender}"
    await context_manager.update_context(session_id, ContextType.SESSION, session_context.to_dict())
    
    return session_context

# NOTE IMPORTANTE:
# Les commandes individuelles ne sont PAS importées automatiquement ici.
# Cette approche évite les importations circulaires et les enregistrements en double.
# 
# Pour utiliser une commande, importez-la directement depuis son module:
# from app.commands.basic_commands import help
# from app.commands.document_commands.docquery import doc_query_command
# etc.

# Pour la phase de transition
def get_registry():
    """Retourne l'instance unique du registre de commandes."""
    return command_registry 