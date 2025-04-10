"""
Exemple de commande utilisant le système de thread.

Ce module sert d'exemple pour montrer comment implémenter une commande
qui utilise le nouveau système de thread de façon simple et claire.
"""

import asyncio
from typing import Dict, Any, Optional

from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser
from nio import RoomMessageText

from app.commands.registry import register_feature, only_allowed_user, threaded_command, thread_response, CommandThread


@register_feature(
    group="example",
    onEvent=RoomMessageText,
    command="sondage",
    help="!sondage [question] - Crée un sondage simple avec 3 choix"
)
@only_allowed_user
@threaded_command("sondage")  # Déclare que cette commande utilise un thread nommé "sondage"
async def create_poll_command(ep: EventParser, matrix_client: MatrixClient):
    """Crée un sondage simple avec 3 options."""
    # Utiliser albert_config si disponible, sinon utiliser la config standard
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Extraire la question du texte du message
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    command_parts = message_text.split(maxsplit=1)
    
    # Si le message est seulement "!sondage" sans arguments
    if len(command_parts) <= 1:
        await matrix_client.send_markdown_message(
            room_id,
            "❓ **Comment utiliser !sondage**\n\n"
            "```\n!sondage Votre question pour le sondage\n```\n"
            "Les participants pourront répondre en tapant 1, 2 ou 3.",
            msgtype="m.notice"
        )
        
        # Marquer la fin du thread car la commande est terminée (cas d'usage sans argument)
        await CommandThread.end(
            room_id, 
            sender, 
            "sondage", 
            config, 
            action="aide affichée"
        )
        return
    
    # Extraire la question (tout ce qui suit après le premier mot)
    question = command_parts[1]
    
    # Enregistrer la question dans l'état du thread
    await CommandThread.update_state(
        room_id,
        sender,
        config,
        question=question,
        action="attente_votes",
        votes={'1': 0, '2': 0, '3': 0},
        voters=[]
    )
    
    # Envoyer le message du sondage
    await matrix_client.send_markdown_message(
        room_id,
        f"📊 **Sondage**: {question}\n\n"
        "1️⃣ Option 1\n"
        "2️⃣ Option 2\n"
        "3️⃣ Option 3\n\n"
        "Répondez avec un chiffre de 1 à 3 pour voter.",
        msgtype="m.notice"
    )
    
    # Note: Le thread reste actif pour attendre les réponses


@register_feature(
    group="example",
    onEvent=RoomMessageText,
    command="",  # Pas de commande spécifique, ce handler est pour les réponses
    help=""
)
@only_allowed_user
@thread_response("sondage")  # Déclare que cette fonction gère les réponses au thread "sondage"
async def handle_poll_response(ep: EventParser, matrix_client: MatrixClient):
    """Gère les réponses au sondage."""
    # Utiliser albert_config si disponible, sinon utiliser la config standard
    config = getattr(matrix_client, "albert_config", matrix_client.config)
    room_id = ep.room.room_id
    sender = ep.sender
    
    # Extraire la réponse de l'utilisateur
    choice_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    
    # Récupérer l'état actuel du sondage depuis le contexte
    from app.commands import get_unified_session_context
    session_context = await get_unified_session_context(config, room_id, sender)
    conversation_state = session_context.conversation_state
    
    # Extraire les données du sondage
    question = conversation_state.get("question", "Question inconnue")
    current_votes = conversation_state.get("votes", {'1': 0, '2': 0, '3': 0})
    voters = conversation_state.get("voters", [])
    
    # Vérifier si la réponse est un choix valide (1, 2 ou 3)
    if choice_text not in ["1", "2", "3"]:
        # Si c'est "fin", terminer le sondage
        if choice_text.lower() == "fin":
            # Calculer les résultats
            total_votes = sum(current_votes.values())
            results = "\n".join([
                f"Option {opt}: {votes} votes ({int(votes/total_votes*100) if total_votes else 0}%)"
                for opt, votes in current_votes.items()
            ])
            
            # Envoyer les résultats finaux
            await matrix_client.send_markdown_message(
                room_id,
                f"📊 **Résultats du sondage**: {question}\n\n"
                f"{results}\n\n"
                f"Total: {total_votes} votes",
                msgtype="m.notice"
            )
            
            # Marquer la fin du thread
            await CommandThread.end(
                room_id, 
                sender, 
                "sondage", 
                config,
                action="sondage_terminé",
                question=question,
                final_votes=current_votes,
                total_votes=total_votes
            )
            
            return
        
        # Sinon, informer l'utilisateur que sa réponse n'est pas valide
        await matrix_client.send_markdown_message(
            room_id,
            "⚠️ Veuillez répondre avec un chiffre de 1 à 3 pour voter, ou 'fin' pour terminer le sondage.",
            msgtype="m.notice"
        )
        return
    
    # Enregistrer le vote
    if sender in voters:
        # L'utilisateur a déjà voté, informer qu'on ne peut voter qu'une fois
        await matrix_client.send_markdown_message(
            room_id,
            "⚠️ Vous avez déjà voté dans ce sondage.",
            msgtype="m.notice"
        )
        return
    
    # Ajouter le vote
    current_votes[choice_text] = current_votes.get(choice_text, 0) + 1
    voters.append(sender)
    
    # Mettre à jour l'état du sondage
    await CommandThread.update_state(
        room_id,
        sender,
        config,
        votes=current_votes,
        voters=voters
    )
    
    # Confirmer le vote
    await matrix_client.send_markdown_message(
        room_id,
        f"✅ Votre vote pour l'option {choice_text} a été enregistré.",
        msgtype="m.notice"
    )
    
    # Montrer les votes actuels
    total_votes = sum(current_votes.values())
    results = "\n".join([
        f"Option {opt}: {votes} votes"
        for opt, votes in current_votes.items()
    ])
    
    await matrix_client.send_markdown_message(
        room_id,
        f"📊 **État actuel du sondage**: {question}\n\n"
        f"{results}\n\n"
        f"Total: {total_votes} votes\n"
        f"Tapez 'fin' pour terminer le sondage.",
        msgtype="m.notice"
    )

# Note: pas besoin d'enregistrer les fonctions manuellement - 
# le décorateur @register_feature s'en charge automatiquement 