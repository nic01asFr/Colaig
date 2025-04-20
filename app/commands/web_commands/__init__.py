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

__all__ = [
    'web_search_command',
    'add_link_command',
    'list_links_command',
    'explore_link_command'
] 