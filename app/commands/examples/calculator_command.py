"""
Exemple de commande avec thread utilisant les nouveaux décorateurs.

Ce module montre comment créer facilement une commande de calculatrice interactive
avec gestion des réponses utilisateur.
"""

import re
from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser

from app.commands.decorators import albert_thread_command, albert_thread_response
from app.commands.registry import CommandThread

# Commande principale qui démarre un thread
@albert_thread_command(
    thread_name="calculatrice",
    group="utils",
    command="calculatrice",
    help_text="!calculatrice - Lance une calculatrice interactive",
    preserve_context=True
)
async def calculatrice_command(ep: EventParser, matrix_client: MatrixClient):
    """Lance une calculatrice interactive via un thread de conversation."""
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Initialiser l'état du thread avec les données nécessaires
    await CommandThread.update_state(
        room_id, sender, config,
        action="attente_expression",
        historique_calculs=[]
    )
    
    # Retourner les instructions d'utilisation
    return """🧮 **Calculatrice interactive**

Entrez une expression mathématique, et je la calculerai pour vous.
Exemples: `2+2`, `5*3`, `(10+5)/3`

Pour quitter, tapez `exit` ou `quit`.

Que souhaitez-vous calculer?"""

# Fonction de validation des réponses au thread calculatrice
def validate_calculatrice_input(text):
    """Vérifie si le texte est une expression mathématique ou une commande de sortie."""
    return re.match(r'^[\d\s\+\-\*\/\(\)\.\,\%\^]+$', text) is not None or text.lower() in ['exit', 'quit']

# Gestionnaire de réponses pour le thread calculatrice
@albert_thread_response(
    thread_name="calculatrice",
    validate_format=validate_calculatrice_input,
    preserve_context=True
)
async def calculatrice_response(ep: EventParser, matrix_client: MatrixClient):
    """Traite les réponses au thread calculatrice."""
    # Configuration de base
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Extraire l'expression à calculer
    expression = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    
    # Vérifier si l'utilisateur souhaite quitter
    if expression.lower() in ['exit', 'quit']:
        # Récupérer l'historique des calculs
        from app.commands import get_unified_session_context
        session_context = await get_unified_session_context(config, room_id, sender)
        conversation_state = session_context.conversation_state
        historique = conversation_state.get("historique_calculs", [])
        
        # Formater l'historique
        historique_text = ""
        if historique:
            historique_text = "\n".join([f"{i+1}. {calc}" for i, calc in enumerate(historique)])
            historique_text = f"\n\n**Historique des calculs**:\n{historique_text}"
        
        # Terminer le thread
        await CommandThread.end(
            room_id, sender, "calculatrice", config,
            action="terminé",
            historique=historique
        )
        
        # Message de fin
        return f"""✅ **Calculatrice fermée**{historique_text}

Vous pouvez relancer la calculatrice à tout moment avec `!calculatrice`"""
    
    # Calculer le résultat
    try:
        # Évaluer l'expression (avec vérification de sécurité)
        # Remplacer les virgules par des points pour le support international
        safe_expression = expression.replace(',', '.')
        
        # Vérifier que l'expression ne contient que des opérations mathématiques
        if not re.match(r'^[\d\s\+\-\*\/\(\)\.\%\^]+$', safe_expression):
            return "⚠️ Expression invalide. Veuillez n'utiliser que des opérations mathématiques."
        
        # Calculer le résultat
        try:
            # Utiliser eval de façon sécurisée pour calculer le résultat
            result = eval(safe_expression, {"__builtins__": {}})
            
            # Formater le résultat
            formatted_result = f"{expression} = {result}"
            
            # Mettre à jour l'historique des calculs
            from app.commands import get_unified_session_context
            session_context = await get_unified_session_context(config, room_id, sender)
            conversation_state = session_context.conversation_state
            historique = conversation_state.get("historique_calculs", [])
            historique.append(formatted_result)
            
            # Limiter l'historique à 10 entrées
            if len(historique) > 10:
                historique = historique[-10:]
            
            # Mettre à jour l'état du thread
            await CommandThread.update_state(
                room_id, sender, config,
                historique_calculs=historique
            )
            
            # Retourner le résultat
            return f"""🧮 **Résultat**: {result}

Entrez une autre expression ou tapez `exit` pour quitter."""
            
        except Exception as e:
            return f"⚠️ Erreur de calcul: {str(e)}"
        
    except Exception as e:
        return f"⚠️ Une erreur est survenue: {str(e)}" 