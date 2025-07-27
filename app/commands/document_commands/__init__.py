"""
Commandes de gestion des documents.

Ce module regroupe toutes les commandes liées à la gestion documentaire:
- chercher: interrogation des documents avec contexte de conversation (!chercher)
- index: gestion de l'index FAISS (status/verify/rebuild/clean) (!index)
- classer: gestion des pièces jointes et classement intelligent (!classer)
- synthese: génération de synthèses complètes sur un sujet (!synthese)

Ces commandes permettent d'interagir avec les documents stockés sur WebDAV.
"""

# NOTE: Les commandes sont importées directement depuis leurs modules respectifs
# par le fichier principal (bot.py) ou par d'autres modules qui en ont besoin.
# Cette approche évite les enregistrements en double des commandes.
# 
# Exemple d'importation directe:
# from app.commands.document_commands.docquery_adapted import doc_query_adapted_command
# from app.commands.document_commands.index import faiss_index_command
# from app.commands.document_commands.attachment_adapted import handle_attachments_adapted_command
# from app.commands.document_commands.synthesis import handle_synthesis_command

# Liste des noms exportés par ce module (utile pour les imports avec *)
__all__ = [
    "doc_query_adapted_command",
    "faiss_index_command",
    "handle_attachments_adapted_command",
    "handle_synthesis_command"
] 