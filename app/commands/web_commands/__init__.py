"""
Module contenant les commandes pour la recherche web.
"""

# Import des commandes web depuis le fichier web_search.py
from app.commands.web_commands.web_search import (
    web_search_command,
    add_link_command, 
    list_links_command,
    explore_link_command
)

# Import des commandes webhook depuis le fichier n8n_webhook.py
from app.commands.web_commands.n8n_webhook import (
    webhook_command,
    webhook_response
)

__all__ = [
    'web_search_command',
    'add_link_command',
    'list_links_command',
    'explore_link_command',
    'webhook_command',
    'webhook_response'
] 