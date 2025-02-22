from typing import List, Dict, Any, Optional
from dataclasses import dataclass
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