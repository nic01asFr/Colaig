"""
Types et interfaces pour l'indexation et la recherche de documents.

Ce module fournit des classes et fonctions réutilisables pour interagir
avec les services d'indexation de documents.
"""

from typing import Dict, List, Any, Optional

# Importer les classes et fonctions nécessaires depuis le service existant
from app.services.index_service import (
    IndexService,
    get_index_service
)

# Réexporter ces classes et fonctions pour maintenir la compatibilité
__all__ = [
    'IndexService',
    'get_index_service'
] 