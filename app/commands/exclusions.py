"""
Module de configuration des exclusions pour le système de commandes.

Ce module définit les commandes et modules à exclure lors du chargement 
afin de ne garder que les commandes essentielles actives.
"""

# Liste des commandes spécifiques à exclure (par nom de fonction)
EXCLUDED_COMMANDS = [
    "handle_attachments_command",  # Version ancienne de pj
    "handle_doc_query_command",    # Version ancienne de docquery
    "faiss_index_command_old",     # Version ancienne d'index si elle existe
]

# Liste des modules à ne pas charger du tout
EXCLUDED_MODULES = [
    "app.commands.document_commands.attachment_command",  # Version classe de pj-new
]

# Liste des classes à ne pas instancier (pour l'approche orientée objet)
EXCLUDED_CLASSES = [
    "AttachmentCommand",
    "DocQueryCommand",
]

# Liste des commandes explicitement autorisées (seules ces commandes seront activées)
INCLUDED_COMMANDS = [
    "faiss_index_command",        # Commande index
    "doc_query_adapted_command",  # Commande docquery-new 
    "handle_attachments_adapted_command",  # Commande pj-new (décorateurs)
    "handle_attachments_adapted_response",  # Réponse pour pj-new
    "handle_conversation",        # Nécessaire pour le fonctionnement de base
]

# Définir si on utilise une liste d'inclusion stricte ou non
USE_STRICT_INCLUSION = True  # Si True, seules les commandes dans INCLUDED_COMMANDS seront chargées 