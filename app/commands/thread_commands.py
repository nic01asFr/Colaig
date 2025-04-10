import logging
import importlib
import inspect
import os
from pathlib import Path
from typing import List, Type

from app.commands.base_thread_command import BaseThreadCommand

logger = logging.getLogger(__name__)

def discover_command_modules() -> List[str]:
    """
    Découvre tous les modules de commandes dans le dossier commands et ses sous-dossiers.
    
    Returns:
        List[str]: Liste des noms de modules découverts
    """
    modules = []
    commands_dir = Path(os.path.dirname(os.path.abspath(__file__)))
    
    for root, dirs, files in os.walk(commands_dir):
        for file in files:
            if file.endswith(".py") and not file.startswith("__"):
                rel_path = os.path.relpath(os.path.join(root, file), os.path.dirname(commands_dir))
                module_path = rel_path.replace(os.sep, ".").replace(".py", "")
                modules.append(module_path)
    
    return modules

def find_command_classes(module) -> List[Type[BaseThreadCommand]]:
    """
    Trouve toutes les classes dérivées de BaseThreadCommand dans un module.
    
    Args:
        module: Module à analyser
        
    Returns:
        List[Type[BaseThreadCommand]]: Liste des classes de commandes découvertes
    """
    command_classes = []
    
    for name, obj in inspect.getmembers(module):
        if (inspect.isclass(obj) and 
            issubclass(obj, BaseThreadCommand) and 
            obj is not BaseThreadCommand):
            command_classes.append(obj)
            
    return command_classes

def register_all_commands() -> List[BaseThreadCommand]:
    """
    Enregistre toutes les commandes basées sur BaseThreadCommand trouvées dans l'application.
    
    Returns:
        List[BaseThreadCommand]: Liste des instances de commandes enregistrées
    """
    registered_commands = []
    
    try:
        # Découvrir tous les modules
        modules = discover_command_modules()
        logger.info(f"Découverte de {len(modules)} modules potentiels de commandes")
        
        for module_name in modules:
            try:
                # Importer le module
                logger.debug(f"Importation du module {module_name}")
                module = importlib.import_module(module_name)
                
                # Trouver les classes de commandes
                command_classes = find_command_classes(module)
                
                for command_class in command_classes:
                    try:
                        # Vérifier si la commande est déjà enregistrée par le module
                        # Si c'est le cas, on ne la ré-enregistre pas
                        already_registered = False
                        for attribute_name in dir(module):
                            attribute = getattr(module, attribute_name)
                            if isinstance(attribute, command_class):
                                already_registered = True
                                registered_commands.append(attribute)
                                logger.info(f"Commande {command_class.COMMAND_NAME} déjà enregistrée par le module")
                                break
                        
                        # Si non enregistrée, on l'enregistre
                        if not already_registered:
                            logger.info(f"Enregistrement de la commande {command_class.COMMAND_NAME}")
                            command_instance = command_class.register()
                            registered_commands.append(command_instance)
                            
                    except Exception as e:
                        logger.error(f"Erreur lors de l'enregistrement de la classe {command_class.__name__}: {str(e)}")
                
            except Exception as e:
                logger.error(f"Erreur lors de l'importation du module {module_name}: {str(e)}")
                
    except Exception as e:
        logger.error(f"Erreur lors de la découverte des modules de commandes: {str(e)}")
        
    logger.info(f"Total de {len(registered_commands)} commandes basées sur BaseThreadCommand enregistrées")
    return registered_commands

# Fonction d'initialisation principale
def init_thread_commands():
    """
    Initialise et enregistre toutes les commandes basées sur BaseThreadCommand.
    Cette fonction doit être appelée au démarrage de l'application.
    """
    logger.info("Initialisation des commandes basées sur BaseThreadCommand")
    
    # Vérifier si le mode inclusion stricte est activé
    try:
        from app.commands.exclusions import USE_STRICT_INCLUSION
        if USE_STRICT_INCLUSION:
            logger.info("Mode inclusion stricte activé, pas d'initialisation des commandes BaseThreadCommand")
            return []
    except ImportError:
        # Continuer normalement si le module n'est pas trouvé
        pass
        
    commands = register_all_commands()
    logger.info(f"Initialisation terminée avec {len(commands)} commandes enregistrées")
    return commands 