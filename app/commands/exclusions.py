"""
Module de configuration des exclusions pour le système de commandes.

Ce module définit les commandes et modules à exclure lors du chargement 
afin de ne garder que les commandes essentielles actives.
"""

# Liste des commandes spécifiques à exclure (par nom de fonction)
EXCLUDED_COMMANDS = [
    # Anciennes versions des commandes à conserver
    "handle_attachments_command",  # Version ancienne de pj/classer
    "handle_doc_query_command",    # Version ancienne de docquery/chercher
    "faiss_index_command_old",     # Version ancienne d'index
    
    # Commandes non essentielles à désactiver
    "albert_debug",               # Commande debug
    "albert_model",               # Commande model/models
    "albert_mode",                # Commande mode/modes
    "albert_sources",             # Commande sources
    "albert_collection",          # Commande collections
    "albert_document",            # Traitement de fichiers encryptés
    "albert_reset",               # Reset de conversation
    "albert_welcome",             # Message de bienvenue
    "handle_attachments_response" # Réponse obsolète pour pj
]

# Liste des modules à ne pas charger du tout
EXCLUDED_MODULES = [
    "app.commands.document_commands.attachment_command",  # Version classe de classer
    "app.commands.document_commands.attachment_example",  # Exemple de commande
    "app.commands.example_command",                       # Exemple de commande
]

# Liste des classes à ne pas instancier (pour l'approche orientée objet)
EXCLUDED_CLASSES = [
    "AttachmentCommand",
    "DocQueryCommand",
]

# Liste des commandes explicitement autorisées (seules ces commandes seront activées)
INCLUDED_COMMANDS = [
    # Commandes documentaires essentielles
    "faiss_index_command",                # Commande !index 
    "doc_query_adapted_command",          # Commande !chercher
    "handle_attachments_adapted_command", # Commande !classer
    "handle_attachments_adapted_response",# Réponse pour !classer
    "handle_synthesis_command",           # Commande !synthese
    "space_management_command",           # Commande !space (link/unlink/list/info/index)

    # Commandes web (nouvelles)
    "web_search_command",                # Commande !recherche_web
    "add_link_command",                  # Commande !ajouter_lien
    "list_links_command",                # Commande !liste_liens
    "explore_link_command",              # Commande !explorer_lien
    
    # Commandes webhook (nouvelles)
    "webhook_command",                   # Commande !webhook
    "webhook_response",                  # Réponse pour !webhook
    
    # Commandes de base et conversation
    "handle_conversation",                # Conversation contextuelle
    "help",                               # Commande d'aide
]

# Définir si on utilise une liste d'inclusion stricte ou non
# En mode strict, seules les commandes explicitement listées dans INCLUDED_COMMANDS sont activées
USE_STRICT_INCLUSION = True 