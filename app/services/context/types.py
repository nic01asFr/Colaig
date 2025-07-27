from enum import Enum
from typing import Any

class ContextType(Enum):
    """Types de contexte disponibles"""
    # Contextes de base
    ROOM = "room"        # Contexte global d'un salon
    USER = "user"        # Préférences utilisateur
    SESSION = "session"  # Session de conversation
    
    # Contextes de traitement
    REQUEST = "request"  # Requête utilisateur et métadonnées
    INTENT = "intent"    # Intention détectée et paramètres
    
    # Contextes d'exécution
    WORKFLOW = "workflow"   # État du workflow en cours
    EXECUTION = "execution" # Résultats d'exécution
    RESPONSE = "response"   # Réponse finale générée 