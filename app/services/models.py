from typing import List, Dict, Any, Optional, Union
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class DocumentChunk:
    """Représente un morceau de document indexé"""
    id: str
    content: str
    document_path: str
    chunk_number: int
    page_number: Optional[int] = None
    metadata: Dict[str, Any] = None
    embedding: Optional[List[float]] = None
    last_updated: Optional[datetime] = None

@dataclass
class WebResult:
    """
    Résultat d'une extraction de contenu web.
    """
    success: bool
    url: str
    content_type: Optional[str] = None
    title: Optional[str] = None
    content: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """
        Convertit le résultat en dictionnaire.
        
        Returns:
            Dictionnaire contenant les données du résultat
        """
        return {
            "success": self.success,
            "url": self.url,
            "content_type": self.content_type,
            "title": self.title,
            "content": self.content,
            "metadata": self.metadata,
            "error": self.error
        }
    
    @classmethod
    def from_error(cls, url: str, error_message: str) -> "WebResult":
        """
        Crée un résultat d'erreur.
        
        Args:
            url: URL qui a causé l'erreur
            error_message: Message d'erreur
            
        Returns:
            Instance de WebResult avec l'erreur
        """
        return cls(
            success=False,
            url=url,
            error=error_message
        )
    
    @classmethod
    def from_success(cls, url: str, content_type: str, title: str, 
                     content: str, metadata: Dict[str, Any] = None) -> "WebResult":
        """
        Crée un résultat de succès.
        
        Args:
            url: URL extraite
            content_type: Type de contenu
            title: Titre du contenu
            content: Contenu extrait
            metadata: Métadonnées supplémentaires
            
        Returns:
            Instance de WebResult avec les données
        """
        return cls(
            success=True,
            url=url,
            content_type=content_type,
            title=title,
            content=content,
            metadata=metadata or {}
        ) 