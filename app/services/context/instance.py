"""Instance globale du gestionnaire de contexte"""
from typing import Any
from config import Config
from .manager import ContextManager

# Instance globale du gestionnaire de contexte
context_manager = ContextManager(Config())

__all__ = ['context_manager'] 