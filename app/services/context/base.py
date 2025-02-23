from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, Any, Optional
from enum import Enum

class ContextType(Enum):
    """Types de contexte disponibles"""
    USER = "user"
    SESSION = "session"
    REQUEST = "request"
    INTENT = "intent"
    WORKFLOW = "workflow"
    EXECUTION = "execution"
    RESPONSE = "response"

@dataclass
class BaseContext:
    """Classe de base pour tous les contextes"""
    created_at: datetime = field(default_factory=datetime.now, init=False)
    updated_at: datetime = field(default_factory=datetime.now, init=False)
    metadata: Dict[str, Any] = field(default_factory=dict, init=False)

    def update(self, data: Dict[str, Any]) -> None:
        """Met à jour le contexte avec de nouvelles données"""
        for key, value in data.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.updated_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """Convertit le contexte en dictionnaire"""
        return {
            k: str(v) if isinstance(v, datetime) else v
            for k, v in self.__dict__.items()
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BaseContext':
        """Crée une instance depuis un dictionnaire"""
        if 'created_at' in data:
            data['created_at'] = datetime.fromisoformat(data['created_at'])
        if 'updated_at' in data:
            data['updated_at'] = datetime.fromisoformat(data['updated_at'])
        return cls(**data) 