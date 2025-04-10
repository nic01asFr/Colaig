"""
Version améliorée de la commande pj utilisant la classe BaseThreadCommand.

Cette version montre comment implémenter une commande avec thread en héritant
de la classe BaseThreadCommand pour simplifier la gestion des threads.
"""

import os
import re
import time
import tempfile
import logging
import traceback
from typing import Dict, Any, Optional, List, Tuple

from app.commands.base_thread_command import BaseThreadCommand
from app.commands import get_unified_session_context
from app.commands.registry import CommandThread
from app.tchap_utils import get_decrypted_file, isa_reply_to

logger = logging.getLogger(__name__)

def decode_path_for_display(path: str) -> str:
    """Décode les caractères URL-encodés dans un chemin pour l'affichage à l'utilisateur."""
    if not path:
        return path
    
    # Décode les séquences comme %c3%a9 en é
    import urllib.parse
    return urllib.parse.unquote(path)

class AttachmentCommand(BaseThreadCommand):
    """
    Commande de gestion des pièces jointes utilisant le système de threads.
    Cette commande permet d'analyser un document et de proposer des options de classement.
    """
    
    # Attributs de classe requis
    COMMAND_NAME = "pj-new"
    COMMAND_DESCRIPTION = "Analyser une pièce jointe et proposer un classement intelligent"
    THREAD_NAME = "pj-new"
    COMMAND_TIMEOUT = 120.0  # 2 minutes pour l'analyse initiale
    RESPONSE_TIMEOUT = 900.0  # 15 minutes pour la réponse utilisateur
    
    def execute_command(self, user_id: str, thread_id: str, 
                      message_text: str, attachments: List[Dict[str, Any]] = None, 
                      **kwargs) -> Dict[str, Any]:
        """
        Exécute la commande initiale pour analyser une pièce jointe.
        
        Args:
            user_id: ID de l'utilisateur
            thread_id: ID du thread
            message_text: Texte du message
            attachments: Liste des pièces jointes
            **kwargs: Arguments additionnels
            
        Returns:
            Dict[str, Any]: Réponse à envoyer à l'utilisateur
        """
        logger.info(f"[PJ-NEW] Démarrage de la commande pj-new")
        logger.info(f"[PJ-NEW] User ID: {user_id}, Thread ID: {thread_id}")
        
        # Vérifier si c'est une réponse à un message
        is_reply = kwargs.get("is_reply", False)
        logger.info(f"[PJ-NEW] Est une réponse: {is_reply}")
        
        if not is_reply:
            # Si ce n'est pas une réponse, demander à l'utilisateur de répondre à un message avec une pièce jointe
            return {
                "message": """📎 **Classement intelligent de document**

Pour classer intelligemment un document, veuillez utiliser cette commande en **réponse** à un message contenant une pièce jointe.

**Exemple d'utilisation:**
1. Trouvez un message avec une pièce jointe
2. Répondez à ce message avec `!pj-new`
3. Je vais analyser le document et vous proposer des options de classement
"""
            }
        
        # TODO: Implémenter la logique d'analyse du document et de génération des suggestions
        # Cette partie doit être adaptée selon l'implémentation dans attachment_adapted.py
        
        # Pour cet exemple, nous renvoyons simplement des suggestions factices
        formatted_suggestions = [
            "𝟭 📁 dossiers-espace-demonstration-2/Documents :\nDossier adapté pour les documents textuels",
            "𝟮 📁 dossiers-espace-demonstration-2/Projets :\nDossier pour les documents de projet",
            "𝟯 +📁 dossiers-espace-demonstration-2/Documents/Techniques :\nDossier spécifique pour les documents techniques"
        ]
        
        # Mettre à jour le contexte du thread
        # TODO: Implémenter la mise à jour du contexte
        
        # Déterminer le nombre d'options affiché
        num_options = len(formatted_suggestions)
        options_numbers = [str(i+1) for i in range(num_options)]
        options_text = ", ".join(options_numbers)
        
        # Préparer le message de suggestions
        suggestions_message = f"""📋 **Analyse du document** : *nom_du_document.pdf*

Synthèse du document: Il s'agit d'un exemple de document technique.

Voici les suggestions de classement pour ce document:

{chr(10).join(formatted_suggestions)}

Pour faire votre choix, répondez simplement avec le numéro de l'option ({options_text}).
Si vous souhaitez annuler, répondez "annuler".
"""
        
        return {
            "message": suggestions_message
        }
    
    def validate_response(self, user_id: str, thread_id: str, 
                        message_text: str, **kwargs) -> Tuple[bool, Optional[str]]:
        """
        Valide la réponse de l'utilisateur pour vérifier si c'est un choix valide.
        
        Args:
            user_id: ID de l'utilisateur
            thread_id: ID du thread
            message_text: Texte du message
            **kwargs: Arguments additionnels
            
        Returns:
            Tuple[bool, Optional[str]]: (est_valide, message_erreur)
        """
        # Log pour déboguer
        logger.info(f"[PJ-NEW-VALIDATE] Validation de la réponse: '{message_text}'")
        
        # Vérifier si la réponse est un chiffre entre 1 et 9 ou "annuler"
        if re.match(r"^\s*[1-9]\s*$", message_text) or message_text.lower().strip() in ["annuler", "cancel"]:
            return True, None
        
        return False, "Veuillez répondre avec un numéro entre 1 et 9 ou 'annuler'."
    
    def get_expected_format_description(self) -> str:
        """
        Retourne une description du format de réponse attendu.
        
        Returns:
            str: Description du format attendu
        """
        return "Répondez avec un numéro (ex: 1, 2, 3...) ou 'annuler'"
    
    def process_response(self, user_id: str, thread_id: str, 
                       message_text: str, **kwargs) -> Dict[str, Any]:
        """
        Traite la réponse de l'utilisateur et exécute l'action correspondante.
        
        Args:
            user_id: ID de l'utilisateur
            thread_id: ID du thread
            message_text: Texte du message
            **kwargs: Arguments additionnels
            
        Returns:
            Dict[str, Any]: Réponse à envoyer à l'utilisateur
        """
        # Logs de début
        logger.info(f"[PJ-NEW-RESPONSE] Traitement de la réponse au thread pj-new")
        logger.info(f"[PJ-NEW-RESPONSE] User ID: {user_id}, Thread ID: {thread_id}")
        
        # Extraire la réponse de l'utilisateur
        choice_text = message_text.strip()
        logger.info(f"[PJ-NEW-RESPONSE] Réponse utilisateur: '{choice_text}'")
        
        # TODO: Implémenter la logique de traitement de la réponse
        # Cette partie doit être adaptée selon l'implémentation dans attachment_adapted.py
        
        # Pour cet exemple, nous renvoyons un message de confirmation factice
        if choice_text.lower() == "annuler":
            return {
                "message": "❌ Classement du document annulé."
            }
        
        try:
            # Traiter le choix de l'utilisateur
            choice_index = int(choice_text) - 1
            
            # Vérifier que le choix est valide
            if choice_index < 0 or choice_index > 2:  # 3 options factices dans cet exemple
                return {
                    "message": "⚠️ Choix invalide. Veuillez répondre avec un numéro entre 1 et 3."
                }
            
            # Message de confirmation
            return {
                "message": f"""✅ **Document classé avec succès**

Le document a été classé dans le dossier choisi.
Le document a également été indexé et pourra être recherché avec `!docquery-new`.
"""
            }
        
        except Exception as e:
            # Gestion globale des erreurs
            logger.error(f"[PJ-NEW-RESPONSE] Erreur lors du traitement de la réponse: {str(e)}")
            logger.error(f"[PJ-NEW-RESPONSE] Traceback: {traceback.format_exc()}")
            
            return {
                "message": f"⚠️ Une erreur est survenue: {str(e)}"
            }

# Enregistrer la commande au démarrage
def register_command():
    """Enregistre la commande AttachmentCommand."""
    return AttachmentCommand.register() 