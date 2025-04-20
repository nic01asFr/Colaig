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
from app.matrix_bot.config import logger

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
    # LOGS DE DIAGNOSTIC
    import traceback
    try:
        logger.info(f"[CONTEXT DEBUG] get_unified_session_context: Démarrage pour room={room_id}, sender={sender}")
        
        # S'assurer que le gestionnaire de contexte est initialisé
        logger.info(f"[CONTEXT DEBUG] get_unified_session_context: Initialisation du gestionnaire de contexte")
        await ensure_initialized()
        logger.info(f"[CONTEXT DEBUG] get_unified_session_context: Gestionnaire initialisé avec succès")
        
        # Générer l'ID de session unique
        session_id = f"{room_id}_{sender}"
        logger.info(f"[CONTEXT DEBUG] get_unified_session_context: Session ID = {session_id}")
        
        # Récupérer le contexte existant
        logger.info(f"[CONTEXT DEBUG] get_unified_session_context: Tentative de récupération du contexte")
        session_context = await context_manager.get_context(session_id, ContextType.SESSION)
        logger.info(f"[CONTEXT DEBUG] get_unified_session_context: Contexte récupéré: {session_context is not None}")
        
        # Créer le contexte s'il n'existe pas
        if not session_context:
            logger.info(f"[CONTEXT DEBUG] get_unified_session_context: Création d'un nouveau contexte")
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
            logger.info(f"[CONTEXT DEBUG] get_unified_session_context: Nouveau contexte créé avec succès")
        
        logger.info(f"[CONTEXT DEBUG] get_unified_session_context: Terminé avec succès")
        return session_context
    
    except Exception as e:
        logger.error(f"[CONTEXT DEBUG] get_unified_session_context: ERREUR: {str(e)}")
        logger.error(f"[CONTEXT DEBUG] get_unified_session_context: Traceback: {traceback.format_exc()}")
        raise

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
    import traceback
    try:
        # Récupérer le contexte de session
        session_context = None
        try:
            session_context = await get_unified_session_context(config, room_id, sender)
        except Exception as context_err:
            logger.error(f"Erreur récupération contexte: {str(context_err)}")
            logger.error(traceback.format_exc())
            # Créer un contexte temporaire en mémoire si problème de récupération
            from app.services.context.models import SessionContext
            session_context = SessionContext(
                session_id=f"{room_id}_{sender}",
                room_id=room_id,
                user_id=sender,
                conversation_state={},
                history=[]
            )
            logger.warning("Utilisation d'un contexte temporaire suite à une erreur")
        
        # Vérifier que la structure history existe
        if not hasattr(session_context, 'history'):
            session_context.history = []
            
        # Si cette instance n'a pas de méthode add_message, ajouter une fonction d'aide
        if not hasattr(session_context, 'add_message'):
            def add_message(role, content):
                if not hasattr(session_context, 'history'):
                    session_context.history = []
                session_context.history.append({"role": role, "content": content})
            # Attacher la méthode à l'instance
            import types
            session_context.add_message = types.MethodType(add_message, session_context)
    
        # Ajouter le message utilisateur si fourni
        if user_message:
            session_context.add_message("user", user_message)
        
        # Ajouter la réponse du bot si fournie
        if bot_response:
            session_context.add_message("assistant", bot_response)
        
        # MODIFICATION: Assurer que l'historique ne devient pas trop grand
        # tout en préservant suffisamment de contexte
        if hasattr(session_context, 'history') and session_context.history and len(session_context.history) > 40:
            # Conserver le début (contexte initial) et la fin (contexte récent)
            session_context.history = session_context.history[:5] + session_context.history[-30:]
            logger.debug(f"Historique de conversation tronqué à {len(session_context.history)} messages")
        
        # Mettre à jour le contexte (si possible)
        session_id = f"{room_id}_{sender}"
        try:
            await context_manager.update_context(session_id, ContextType.SESSION, session_context.to_dict())
        except Exception as update_err:
            logger.error(f"Erreur mise à jour contexte: {str(update_err)}")
            logger.error(traceback.format_exc())
            logger.warning("Conservation du contexte en mémoire uniquement (pas de persistance)")
            # Continuer malgré l'erreur pour préserver la conversation
    
        return session_context
    except Exception as e:
        logger.error(f"Erreur globale update_conversation_history: {str(e)}")
        logger.error(traceback.format_exc())
        # En dernier recours, créer un contexte vide pour éviter un crash complet
        from app.services.context.models import SessionContext
        empty_context = SessionContext(
            session_id=f"{room_id}_{sender}",
            room_id=room_id,
            user_id=sender,
            conversation_state={},
            history=[]
        )
        # Ajouter quand même les messages si possible
        if user_message:
            empty_context.history.append({"role": "user", "content": user_message})
        if bot_response:
            empty_context.history.append({"role": "assistant", "content": bot_response})
            
        logger.warning("Utilisation d'un contexte vide suite à une erreur critique")
        return empty_context

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