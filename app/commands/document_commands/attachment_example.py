import logging
import re
import os
from typing import Optional, Dict, Any

from app.commands.base_thread_command import BaseThreadCommand
from app.tools.matrix_client import MatrixClient
from app.tools.event_parser import EventParser

logger = logging.getLogger(__name__)

class AttachmentCommand(BaseThreadCommand):
    """
    Exemple de commande de gestion des pièces jointes utilisant BaseThreadCommand.
    Cette classe montre comment migrer une commande existante vers le nouveau modèle.
    """
    
    COMMAND_NAME = "pj-example"
    COMMAND_DESCRIPTION = "Exemple de commande de gestion des pièces jointes"
    COMMAND_TIMEOUT = 300.0  # 5 minutes
    RESPONSE_TIMEOUT = 600.0  # 10 minutes
    
    def __init__(self):
        self.temp_files = {}  # Pour stocker les fichiers temporaires en cours de traitement
    
    async def execute_command(self, ep: EventParser, matrix_client: MatrixClient) -> str:
        """
        Traite la commande initiale de gestion des pièces jointes.
        
        Args:
            ep: EventParser pour l'événement déclencheur
            matrix_client: Client Matrix
            
        Returns:
            str: Message à envoyer à l'utilisateur
        """
        logger.info(f"Exécution de la commande {self.COMMAND_NAME}")
        
        # Récupérer les pièces jointes du message
        event = ep.event
        room_id = event.get("room_id", "")
        sender = event.get("sender", "")
        
        # Vérifier s'il y a des pièces jointes
        if not ep.has_files():
            return "Aucune pièce jointe détectée. Veuillez envoyer votre commande avec un fichier joint."
        
        # Traiter les pièces jointes
        files = ep.get_files()
        file_info = files[0]  # Pour simplifier, on ne prend que le premier fichier
        
        # Stocker les informations du fichier pour le traitement ultérieur
        self.temp_files[f"{room_id}_{sender}"] = {
            "file_name": file_info.get("body", "fichier inconnu"),
            "file_url": file_info.get("url", ""),
            "content_type": file_info.get("content_type", "")
        }
        
        # Message à l'utilisateur avec options
        options = [
            "📁 Option 1: Classer dans le dossier 'Documents'",
            "📁 Option 2: Classer dans le dossier 'Images'",
            "📁 Option 3: Créer un nouveau dossier"
        ]
        
        options_text = "\n".join(options)
        
        return (
            f"J'ai bien reçu votre fichier '{file_info.get('body', 'fichier')}'. "
            f"Où souhaitez-vous le classer?\n\n"
            f"{options_text}\n\n"
            f"Répondez avec le numéro de l'option (1, 2, 3)."
        )
    
    @staticmethod
    def validate_response(message_text: str) -> bool:
        """
        Valide le format de la réponse pour s'assurer qu'elle correspond à une option valide.
        
        Args:
            message_text: Texte du message à valider
            
        Returns:
            bool: True si le format est valide, False sinon
        """
        # Vérifier si le message contient un chiffre entre 1 et 3
        pattern = r"^\s*[1-3]\s*$"
        return bool(re.match(pattern, message_text))
    
    @classmethod
    def get_expected_format_description(cls) -> str:
        """
        Retourne une description du format de réponse attendu.
        
        Returns:
            str: Description du format attendu
        """
        return "Un nombre entre 1 et 3 correspondant à l'option choisie."
    
    async def process_response(self, ep: EventParser, matrix_client: MatrixClient) -> str:
        """
        Traite la réponse de l'utilisateur pour la classification du document.
        
        Args:
            ep: EventParser pour l'événement déclencheur
            matrix_client: Client Matrix
            
        Returns:
            str: Message à envoyer à l'utilisateur
        """
        logger.info(f"Traitement de la réponse pour {self.COMMAND_NAME}")
        
        # Récupérer les informations de l'événement
        event = ep.event
        room_id = event.get("room_id", "")
        sender = event.get("sender", "")
        message_text = ep.get_command_text()
        
        # Récupérer les informations du fichier
        file_key = f"{room_id}_{sender}"
        file_info = self.temp_files.get(file_key, {})
        
        if not file_info:
            logger.warning(f"Aucune information de fichier trouvée pour {file_key}")
            return "Désolé, je ne trouve pas d'information sur votre fichier. Veuillez recommencer."
        
        # Traiter la réponse
        choice = message_text.strip()
        
        # Déterminer l'action en fonction du choix
        destination_folder = ""
        if choice == "1":
            destination_folder = "Documents"
        elif choice == "2":
            destination_folder = "Images"
        elif choice == "3":
            destination_folder = "Nouveau dossier"  # Dans un cas réel, on demanderait le nom du dossier
        
        # Simuler le classement du fichier
        file_name = file_info.get("file_name", "fichier inconnu")
        
        # Terminer le thread
        await self.end_thread(room_id, sender, matrix_client)
        
        # Message final
        return (
            f"✅ Votre fichier '{file_name}' a été classé dans le dossier '{destination_folder}'.\n"
            f"Merci d'avoir utilisé le service de classement de documents."
        )

# Enregistrer la commande
# Dans un environnement réel, cela serait fait lors du chargement du module
attachment_command = AttachmentCommand.register() 