"""
Exemple de commande simple utilisant le nouveau décorateur albert_command.

Cette commande illustre comment créer facilement une nouvelle commande
en se concentrant uniquement sur la logique métier.
"""

import asyncio
from matrix_bot.client import MatrixClient
from matrix_bot.config import logger
from matrix_bot.eventparser import EventParser

from app.commands.decorators import albert_command

@albert_command(
    group="utils",
    command="echo",
    help_text="!echo [texte] - Répète le texte saisi",
    preserve_context=True
)
async def echo_command(ep: EventParser, matrix_client: MatrixClient):
    """Répète simplement le texte saisi par l'utilisateur."""
    # Extraire le texte du message
    message_text = ep.event.body.strip() if hasattr(ep.event, 'body') else ""
    command_parts = message_text.split(maxsplit=1)
    
    # Si le message est uniquement "!echo" sans arguments
    if len(command_parts) <= 1:
        return """❓ **Comment utiliser !echo**

```
!echo Votre texte à répéter
```

Je vais simplement répéter le texte que vous tapez après la commande."""
    
    # Extraire le texte à répéter (tout ce qui suit après le premier mot)
    text_to_echo = command_parts[1]
    
    # Simuler un traitement
    await asyncio.sleep(1)
    
    # Retourner la réponse
    return f"""🔊 **Echo**:

{text_to_echo}""" 