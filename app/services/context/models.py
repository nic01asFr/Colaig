from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Dict, Any, List, Optional, Union
import logging
import dataclasses

logger = logging.getLogger(__name__)

def get_synchronized_time() -> datetime:
    """Fournit un timestamp synchronisé pour tous les champs d'un même contexte"""
    return datetime.now()

@dataclass
class BaseContext:
    """Classe de base pour tous les contextes"""
    def __init__(self):
        self._last_activity = get_synchronized_time()
        self._context_refs: Dict[str, str] = {}  # Références vers d'autres contextes
        self._lock_token: Optional[str] = None   # Token pour la gestion des accès concurrents

    @property
    def last_activity(self) -> datetime:
        """Getter pour la dernière activité"""
        if isinstance(self._last_activity, str):
            try:
                return datetime.fromisoformat(self._last_activity)
            except ValueError:
                return get_synchronized_time()
        return self._last_activity

    @last_activity.setter
    def last_activity(self, value: Union[datetime, str]) -> None:
        """Setter pour la dernière activité"""
        if isinstance(value, str):
            try:
                self._last_activity = datetime.fromisoformat(value)
            except ValueError:
                self._last_activity = get_synchronized_time()
        else:
            self._last_activity = value

    def add_context_ref(self, context_type: str, context_id: str) -> None:
        """Ajoute une référence vers un autre contexte"""
        self._context_refs[context_type] = context_id

    def get_context_ref(self, context_type: str) -> Optional[str]:
        """Récupère l'ID d'un contexte référencé"""
        return self._context_refs.get(context_type)

    def set_lock_token(self, token: str) -> None:
        """Définit le token de verrouillage pour l'accès concurrent"""
        self._lock_token = token

    def clear_lock_token(self) -> None:
        """Supprime le token de verrouillage"""
        self._lock_token = None

    def to_dict(self) -> Dict[str, Any]:
        """Convertit le contexte en dictionnaire"""
        data = asdict(self) if hasattr(self, '__dataclass_fields__') else self.__dict__.copy()
        data.pop('_last_activity', None)
        data.pop('_context_refs', None)
        data.pop('_lock_token', None)
        
        # Gérer le cas où _last_activity est déjà une chaîne
        if isinstance(self._last_activity, str):
            data['last_activity'] = self._last_activity
        elif isinstance(self._last_activity, datetime):
            data['last_activity'] = self._last_activity.isoformat()
        else:
            # Si c'est ni une chaîne ni un datetime, utiliser l'heure actuelle
            data['last_activity'] = get_synchronized_time().isoformat()
            
        if self._context_refs:
            data['context_refs'] = self._context_refs
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BaseContext':
        """Crée une instance depuis un dictionnaire"""
        data_copy = data.copy()
        
        # Extraire les champs spéciaux
        last_activity_str = data_copy.pop('last_activity', None)
        context_refs = data_copy.pop('context_refs', {})
        
        # Retirer les champs de type qui ne sont pas des attributs
        data_copy.pop('_type', None)
        data_copy.pop('context_type', None)
        
        # Filtrer les arguments inconnus pour éviter les erreurs
        # lors du chargement de contextes créés avec une version différente
        if hasattr(cls, '__dataclass_fields__'):
            valid_fields = cls.__dataclass_fields__.keys()
            # Conserver uniquement les champs valides
            filtered_data = {k: v for k, v in data_copy.items() if k in valid_fields}
        else:
            # Si ce n'est pas une dataclass, utiliser tous les champs
            filtered_data = data_copy
        
        # Créer l'instance
        try:
            instance = cls(**filtered_data)
        except TypeError as e:
            # En cas d'erreur, journaliser et essayer avec un ensemble minimal de paramètres
            logger.warning(f"Erreur lors de la création de {cls.__name__} depuis un dictionnaire: {str(e)}")
            # Déterminer les paramètres requis (ceux sans valeur par défaut)
            if hasattr(cls, '__dataclass_fields__'):
                required_fields = {
                    name: data_copy.get(name) 
                    for name, field in cls.__dataclass_fields__.items() 
                    if field.default == dataclasses.MISSING and 
                       field.default_factory == dataclasses.MISSING
                }
                instance = cls(**required_fields)
            else:
                # Si tout échoue, lever l'exception
                raise
        
        # Initialiser last_activity
        if last_activity_str:
            try:
                instance._last_activity = datetime.fromisoformat(last_activity_str)
            except ValueError:
                instance._last_activity = get_synchronized_time()
        
        # Restaurer les références
        instance._context_refs = context_refs
        
        return instance

@dataclass(kw_only=True)
class UserContext(BaseContext):
    """Contexte utilisateur"""
    user_id: str
    room_id: str
    config: Dict[str, Any]
    permissions: List[str] = field(default_factory=list)
    preferences: Dict[str, Any] = field(default_factory=dict)
    last_activity: datetime = field(init=False)

    def __post_init__(self):
        super().__init__()
        self.last_activity = self._last_activity

    @property
    def last_activity(self) -> datetime:
        """Getter pour la dernière activité"""
        if isinstance(self._last_activity, str):
            try:
                return datetime.fromisoformat(self._last_activity)
            except ValueError:
                return get_synchronized_time()
        return self._last_activity
        
    @last_activity.setter
    def last_activity(self, value: Union[datetime, str]) -> None:
        """Setter pour la dernière activité"""
        if isinstance(value, str):
            try:
                self._last_activity = datetime.fromisoformat(value)
            except ValueError:
                self._last_activity = get_synchronized_time()
        else:
            self._last_activity = value

@dataclass(kw_only=True)
class SessionContext(BaseContext):
    """Contexte de session"""
    session_id: str
    room_id: str
    user_id: str
    history: List[Dict[str, Any]] = field(default_factory=list)
    conversation_state: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Initialisation post-construction"""
        super().__init__()
        self.last_activity = self._last_activity
        
    @property
    def last_activity(self) -> datetime:
        """Getter pour la dernière activité"""
        if isinstance(self._last_activity, str):
            try:
                return datetime.fromisoformat(self._last_activity)
            except ValueError:
                return get_synchronized_time()
        return self._last_activity
        
    @last_activity.setter
    def last_activity(self, value: Union[datetime, str]) -> None:
        """Setter pour la dernière activité"""
        if isinstance(value, str):
            try:
                self._last_activity = datetime.fromisoformat(value)
            except ValueError:
                self._last_activity = get_synchronized_time()
        else:
            self._last_activity = value

    def add_message(self, role: str, content: str, timestamp: Optional[datetime] = None) -> None:
        """Ajoute un message à l'historique"""
        message_time = timestamp or get_synchronized_time()
        self.history.append({
            "role": role,
            "content": content,
            "timestamp": message_time.isoformat(),
            "user_id": self.user_id if role == "user" else None
        })
        self.last_activity = get_synchronized_time()

    def to_dict(self) -> Dict[str, Any]:
        """Convertit le contexte en dictionnaire"""
        data = super().to_dict()
        data.update({
            "session_id": self.session_id,
            "room_id": self.room_id,
            "user_id": self.user_id,
            "history": self.history,
            "conversation_state": self.conversation_state
        })
        return data

@dataclass(kw_only=True)
class RoomContext(BaseContext):
    """Contexte global d'un salon"""
    room_id: str
    name: str
    is_direct: bool
    participants: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    shared_context: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        """Initialisation post-construction"""
        super().__init__()
        self.last_activity = self._last_activity
        
    @property
    def last_activity(self) -> datetime:
        """Getter pour la dernière activité"""
        if isinstance(self._last_activity, str):
            try:
                return datetime.fromisoformat(self._last_activity)
            except ValueError:
                return get_synchronized_time()
        return self._last_activity
        
    @last_activity.setter
    def last_activity(self, value: Union[datetime, str]) -> None:
        """Setter pour la dernière activité"""
        if isinstance(value, str):
            try:
                self._last_activity = datetime.fromisoformat(value)
            except ValueError:
                self._last_activity = get_synchronized_time()
        else:
            self._last_activity = value

    def add_participant(self, user_id: str, role: str = "member") -> None:
        """Ajoute un participant au salon"""
        if user_id not in self.participants:
            current_time = get_synchronized_time()
            self.participants[user_id] = {
                "role": role,
                "joined_at": current_time.isoformat()
            }
            self.last_activity = current_time

    def update_participant_activity(self, user_id: str) -> None:
        """Met à jour l'activité d'un participant"""
        if user_id in self.participants:
            self.last_activity = get_synchronized_time()

    def update_shared_context(self, key: str, value: Any) -> None:
        """Met à jour le contexte partagé du salon"""
        self.shared_context[key] = value
        self.last_activity = get_synchronized_time()

    def to_dict(self) -> Dict[str, Any]:
        """Convertit le contexte en dictionnaire"""
        data = super().to_dict()
        data.update({
            "room_id": self.room_id,
            "name": self.name,
            "is_direct": self.is_direct,
            "participants": self.participants,
            "shared_context": self.shared_context
        })
        return data

@dataclass(kw_only=True)
class RequestContext(BaseContext):
    """Contexte de requête"""
    query: str
    raw_input: Dict[str, Any]
    event_timestamp: datetime
    context_type: str = field(default="default")
    parameters: Dict[str, Any] = field(default_factory=dict)

    def link_intent(self, intent_id: str) -> None:
        """Lie une intention à cette requête"""
        self.add_context_ref('intent', intent_id)

    def link_workflow(self, workflow_id: str) -> None:
        """Lie un workflow à cette requête"""
        self.add_context_ref('workflow', workflow_id)

    def __post_init__(self):
        super().__init__()

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RequestContext':
        """Crée une instance depuis un dictionnaire"""
        # Gestion du timestamp de l'événement
        timestamp_str = data.pop('timestamp', None) or data.pop('event_timestamp', None)
        try:
            if timestamp_str and isinstance(timestamp_str, str):
                data['event_timestamp'] = datetime.fromisoformat(timestamp_str)
            else:
                # Si pas de timestamp fourni, utiliser le timestamp synchronisé
                data['event_timestamp'] = get_synchronized_time()
        except ValueError:
            logger.debug(f"Format de date invalide pour event_timestamp: {timestamp_str}, utilisation de la date actuelle")
            data['event_timestamp'] = get_synchronized_time()
        
        # Retirer les champs de type qui ne sont pas des attributs
        data_copy = data.copy()
        data_copy.pop('_type', None)
        data_copy.pop('context_type', None)
            
        return super().from_dict(data_copy)

    def to_dict(self) -> Dict[str, Any]:
        """Convertit le contexte en dictionnaire avec préservation des timestamps"""
        data = super().to_dict()
        # Assurer que le timestamp de l'événement est sérialisé
        data['event_timestamp'] = self.event_timestamp.isoformat()
        return data

@dataclass(kw_only=True)
class ResponseContext(BaseContext):
    """Contexte de réponse"""
    content: str  # Contenu de la réponse
    response_type: str = field(default="text")  # Type de réponse (text, markdown, etc.)
    metadata: Dict[str, Any] = field(default_factory=dict)  # Métadonnées additionnelles
    
    def __post_init__(self):
        super().__init__()
        self.last_activity = self._last_activity
        
        # Gérer la compatibilité des formats
        if hasattr(self, 'format'):
            self.response_type = self.format
        if hasattr(self, 'response_id'):
            self.response_type = self.response_id
            
    @property
    def last_activity(self) -> datetime:
        """Getter pour la dernière activité"""
        if isinstance(self._last_activity, str):
            try:
                return datetime.fromisoformat(self._last_activity)
            except ValueError:
                return get_synchronized_time()
        return self._last_activity
        
    @last_activity.setter
    def last_activity(self, value: Union[datetime, str]) -> None:
        """Setter pour la dernière activité"""
        if isinstance(value, str):
            try:
                self._last_activity = datetime.fromisoformat(value)
            except ValueError:
                self._last_activity = get_synchronized_time()
        else:
            self._last_activity = value

    def to_dict(self) -> Dict[str, Any]:
        """Convertit le contexte en dictionnaire"""
        data = super().to_dict()
        data.update({
            "response_type": self.response_type,
            "content": self.content,
            "metadata": self.metadata
        })
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ResponseContext':
        """Crée une instance depuis un dictionnaire"""
        data_copy = data.copy()
        
        # Unifier les différents formats possibles
        response_type = (
            data_copy.pop('response_type', None) or 
            data_copy.pop('format', None) or 
            data_copy.pop('response_id', None) or 
            "text"
        )
        data_copy['response_type'] = response_type
        
        # Retirer les champs de type qui ne sont pas des attributs
        data_copy.pop('_type', None)
        data_copy.pop('context_type', None)
        
        return super().from_dict(data_copy)

@dataclass(kw_only=True)
class IntentContext(BaseContext):
    """Contexte d'intention"""
    intent_type: str
    confidence: float
    parameters: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        super().__init__()
        self.last_activity = self._last_activity
        
    @property
    def last_activity(self) -> datetime:
        """Getter pour la dernière activité"""
        if isinstance(self._last_activity, str):
            try:
                return datetime.fromisoformat(self._last_activity)
            except ValueError:
                return get_synchronized_time()
        return self._last_activity
        
    @last_activity.setter
    def last_activity(self, value: Union[datetime, str]) -> None:
        """Setter pour la dernière activité"""
        if isinstance(value, str):
            try:
                self._last_activity = datetime.fromisoformat(value)
            except ValueError:
                self._last_activity = get_synchronized_time()
        else:
            self._last_activity = value

@dataclass(kw_only=True)
class WorkflowContext(BaseContext):
    """Contexte de workflow"""
    workflow_type: str
    state: Dict[str, Any] = field(default_factory=dict)
    steps: List[Dict[str, Any]] = field(default_factory=list)
    
    def __post_init__(self):
        super().__init__()
        self.last_activity = self._last_activity
        
    @property
    def last_activity(self) -> datetime:
        """Getter pour la dernière activité"""
        if isinstance(self._last_activity, str):
            try:
                return datetime.fromisoformat(self._last_activity)
            except ValueError:
                return get_synchronized_time()
        return self._last_activity
        
    @last_activity.setter
    def last_activity(self, value: Union[datetime, str]) -> None:
        """Setter pour la dernière activité"""
        if isinstance(value, str):
            try:
                self._last_activity = datetime.fromisoformat(value)
            except ValueError:
                self._last_activity = get_synchronized_time()
        else:
            self._last_activity = value

@dataclass(kw_only=True)
class ExecutionContext(BaseContext):
    """Contexte d'exécution"""
    execution_id: str
    results: Dict[str, Any] = field(default_factory=dict)
    errors: List[Dict[str, Any]] = field(default_factory=list)
    temporary_data: Dict[str, Any] = field(default_factory=dict)
    performance_metrics: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self):
        super().__init__()
        self.last_activity = self._last_activity
        
    @property
    def last_activity(self) -> datetime:
        """Getter pour la dernière activité"""
        if isinstance(self._last_activity, str):
            try:
                return datetime.fromisoformat(self._last_activity)
            except ValueError:
                return get_synchronized_time()
        return self._last_activity
        
    @last_activity.setter
    def last_activity(self, value: Union[datetime, str]) -> None:
        """Setter pour la dernière activité"""
        if isinstance(value, str):
            try:
                self._last_activity = datetime.fromisoformat(value)
            except ValueError:
                self._last_activity = get_synchronized_time()
        else:
            self._last_activity = value

    def to_dict(self) -> Dict[str, Any]:
        """Convertit le contexte en dictionnaire"""
        data = super().to_dict()
        data.update({
            "execution_id": self.execution_id,
            "results": self.results,
            "errors": self.errors,
            "temporary_data": self.temporary_data,
            "performance_metrics": self.performance_metrics
        })
        return data 