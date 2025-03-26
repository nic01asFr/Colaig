"""
Commandes de gestion des documents.

Ce module regroupe toutes les commandes liées à la gestion documentaire:
- docquery: interrogation des documents avec contexte de conversation
- index: gestion de l'index FAISS (status/verify/rebuild/clean)
- pj: gestion des pièces jointes

Ces commandes permettent d'interagir avec les documents stockés sur WebDAV.
"""

# NOTE: Les commandes sont importées directement depuis leurs modules respectifs
# par le fichier principal (bot.py) ou par d'autres modules qui en ont besoin.
# Cette approche évite les enregistrements en double des commandes.
# 
# Exemple d'importation directe:
# from app.commands.document_commands.docquery import doc_query_command
# from app.commands.document_commands.index import faiss_index_command
# from app.commands.document_commands.attachment import handle_attachments_command

# Liste des noms exportés par ce module (utile pour les imports avec *)
__all__ = [
    "doc_query_command",
    "faiss_index_command",
    "handle_attachments_command",
    "handle_attachments_response"
] 