import abc
import logging
import re
from typing import Dict, Any, Optional, ClassVar, Callable, List, Tuple

from app.utils.decorators import register_feature, albert_thread_command, thread_response
from app.commands.registry import threaded_command, thread_response
from app.commands.registry import register_feature
from app.tools.matrix_client import MatrixClient
from app.tools.event_parser import EventParser
from app.commands.helpers import CommandThread

logger = logging.getLogger(__name__)

class BaseThreadCommand(abc.ABC):
    """
    Classe de base pour toutes les commandes utilisant le système de threads.
    Cette classe fournit une structure et des méthodes communes pour simplifier
    l'implémentation de nouvelles commandes.
    """
    
    # Attributs de classe à définir dans les sous-classes
    COMMAND_NAME: ClassVar[str] = ""  # Nom de la commande (e.g., "pj-new")
    COMMAND_DESCRIPTION: ClassVar[str] = ""  # Description pour l'aide
    THREAD_NAME: ClassVar[str] = ""  # Nom du thread (par défaut, identique à COMMAND_NAME)
    COMMAND_TIMEOUT: ClassVar[float] = 60.0  # Timeout de la commande en secondes
    RESPONSE_TIMEOUT: ClassVar[float] = 300.0  # Timeout de la réponse en secondes
    
    def __init__(self):
        """
        Initialise une nouvelle instance de BaseThreadCommand.
        """
        if not self.THREAD_NAME:
            # Si THREAD_NAME n'est pas défini, utiliser COMMAND_NAME
            self.__class__.THREAD_NAME = self.COMMAND_NAME
            
        # Référence à l'instance utilisée pour l'enregistrement des commandes
        self._instance = None
    
    @classmethod
    def register(cls) -> 'BaseThreadCommand':
        """
        Enregistre la commande et le gestionnaire de réponses auprès du système.
        
        Returns:
            BaseThreadCommand: Instance de la commande enregistrée
        """
        logger.info(f"Enregistrement de la commande thread {cls.COMMAND_NAME}")
        
        # Créer une instance de la classe
        instance = cls()
        instance._instance = instance
        
        # Créer les wrappers pour les méthodes avec les bons décorateurs
        exec_wrapper = register_feature(albert_thread_command(
            thread_name=instance.THREAD_NAME,
            timeout=instance.COMMAND_TIMEOUT
        )(instance.execute_command))
        
        resp_wrapper = register_feature(thread_response(
            thread_name=instance.THREAD_NAME,
            validate_format=instance.validate_response,
            timeout=instance.RESPONSE_TIMEOUT
        )(instance.process_response))
        
        # Enregistrer les wrappers au système de commandes
        instance._execute_wrapper = exec_wrapper
        instance._response_wrapper = resp_wrapper
        
        logger.info(f"Commande {cls.COMMAND_NAME} enregistrée avec succès")
        return instance
    
    @abc.abstractmethod
    def execute_command(self, user_id: str, thread_id: str, 
                        message_text: str, attachments: List[Dict[str, Any]] = None, 
                        **kwargs) -> Dict[str, Any]:
        """
        Exécute la commande initiale.
        
        Args:
            user_id: ID de l'utilisateur
            thread_id: ID du thread
            message_text: Texte du message
            attachments: Liste des pièces jointes
            **kwargs: Arguments additionnels
            
        Returns:
            Dict[str, Any]: Réponse à envoyer à l'utilisateur
        """
        pass
    
    @abc.abstractmethod
    def validate_response(self, user_id: str, thread_id: str, 
                        message_text: str, **kwargs) -> Tuple[bool, Optional[str]]:
        """
        Valide la réponse de l'utilisateur.
        
        Args:
            user_id: ID de l'utilisateur
            thread_id: ID du thread
            message_text: Texte du message
            **kwargs: Arguments additionnels
            
        Returns:
            Tuple[bool, Optional[str]]: (est_valide, message_erreur)
        """
        pass
    
    @abc.abstractmethod
    def get_expected_format_description(self) -> str:
        """
        Retourne une description du format de réponse attendu.
        
        Returns:
            str: Description du format attendu
        """
        pass
    
    @abc.abstractmethod
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
        pass

    @staticmethod
    async def end_thread(room_id: str, sender: str, matrix_client: MatrixClient) -> None:
        """
        Termine le thread actif pour un utilisateur dans une salle.
        
        Args:
            room_id: ID de la salle
            sender: ID de l'expéditeur
            matrix_client: Client Matrix
        """
        config = getattr(matrix_client, "albert_config", matrix_client.config)
        await CommandThread.end(room_id, sender, config)
        logger.info(f"Thread terminé pour {sender} dans {room_id}") 