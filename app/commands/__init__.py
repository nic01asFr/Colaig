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

# Fonction utilitaire pour obtenir le gestionnaire de contexte
def get_context_manager(config):
    """Récupère le gestionnaire de contexte."""
    from app.services.context.instance import context_manager
    return context_manager

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