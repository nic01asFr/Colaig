from typing import Optional, Tuple
from io import BytesIO
import os
from pathlib import Path

from app.matrix_bot.config import logger
from app.config import Config
from app.services.webdav import WebDAVService
from app.services.attachment_classifier import AttachmentClassifier, AttachmentClassificationResult

class AttachmentHandler:
    """Service dédié à la gestion des pièces jointes pour la commande !pj"""
    
    def __init__(self, config: Config):
        self.config = config
        self.webdav = WebDAVService(config)
        self.classifier = None
        
    async def initialize(self) -> None:
        """Initialise les services nécessaires"""
        self.classifier = AttachmentClassifier(self.config)
        await self.classifier.initialize()
        
    async def save_attachment(self, file_content: bytes, file_name: str, target_path: str) -> bool:
        """Sauvegarde une pièce jointe sur le WebDAV de manière sécurisée"""
        try:
            # write_file avec ensure_parents crée les dossiers parents automatiquement.
            # Le PUT retourne 201/204 en cas de succès — pas besoin de relire pour vérifier.
            await self.webdav.write_file(target_path, file_content, ensure_parents=True)
            logger.info(f"Fichier sauvegardé avec succès: {target_path}")
            return True

        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde du fichier: {str(e)}")
            return False
            
    async def process_attachment(
        self, 
        file_content: bytes,
        file_name: str,
        file_size: int
    ) -> Tuple[bool, Optional[AttachmentClassificationResult], Optional[BytesIO]]:
        """Traite une pièce jointe : classification et préparation"""
        try:
            # Créer un objet fichier
            file = BytesIO(file_content)
            file.name = file_name
            
            # Classifier le fichier
            result = await self.classifier.classify_file(file_name, file_content)
            
            return True, result, file
            
        except Exception as e:
            logger.error(f"Erreur lors du traitement de la pièce jointe: {str(e)}")
            return False, None, None
            
    async def cleanup(self) -> None:
        """Nettoie les ressources"""
        if self.classifier:
            await self.classifier.__aexit__(None, None, None)
            
    async def __aenter__(self):
        await self.initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup() 