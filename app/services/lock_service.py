from typing import Dict, Optional
from datetime import datetime, timedelta
import asyncio
from dataclasses import dataclass
import json

from app.matrix_bot.config import logger
from app.config import Config
from app.services.webdav import WebDAVService

@dataclass
class FileLock:
    """Représente un verrou sur un fichier"""
    path: str
    owner: str
    created_at: datetime
    expires_at: datetime
    operation: str

class LockService:
    """Service de gestion des verrous pour les opérations sur les fichiers"""
    
    LOCK_FILE = ".locks.json"
    LOCK_TIMEOUT = 300  # 5 minutes en secondes
    LOCK_EXTENSION_MAX = 3  # Nombre maximum d'extensions de verrou
    
    def __init__(self, config: Config, webdav_service: Optional[WebDAVService] = None):
        self.config = config
        self.webdav_service = webdav_service or WebDAVService(config)
        self.locks: Dict[str, FileLock] = {}
        self._lock = asyncio.Lock()  # Verrou pour la synchronisation interne
        self.lock_extensions: Dict[str, int] = {}  # Compteur d'extensions par verrou
        
    async def initialize(self) -> None:
        """Initialise le service de verrouillage"""
        try:
            await self._load_locks()
            # Nettoyage des verrous expirés au démarrage
            await self._cleanup_expired_locks()
        except Exception as e:
            logger.warning(f"Impossible de charger les verrous: {str(e)}")
            self.locks = {}
            
    async def acquire_lock(self, path: str, owner: str, operation: str) -> bool:
        """Tente d'acquérir un verrou sur un fichier"""
        try:
            async with self._lock:
                # Vérifier si le fichier est déjà verrouillé
                if path in self.locks:
                    lock = self.locks[path]
                    if datetime.now() < lock.expires_at:
                        logger.warning(f"Fichier {path} déjà verrouillé par {lock.owner}")
                        return False
                    # Si le verrou est expiré, on le supprime
                    del self.locks[path]
                
                # Créer un nouveau verrou
                now = datetime.now()
                lock = FileLock(
                    path=path,
                    owner=owner,
                    created_at=now,
                    expires_at=now + timedelta(seconds=self.LOCK_TIMEOUT),
                    operation=operation
                )
                self.locks[path] = lock
                
                # Sauvegarder l'état des verrous
                await self._save_locks()
                logger.info(f"Verrou acquis sur {path} par {owner}")
                return True
                
        except Exception as e:
            logger.error(f"Erreur lors de l'acquisition du verrou: {str(e)}")
            return False
            
    async def release_lock(self, path: str, owner: str) -> bool:
        """Libère un verrou sur un fichier"""
        try:
            async with self._lock:
                if path not in self.locks:
                    logger.warning(f"Tentative de libération d'un verrou inexistant: {path}")
                    return False
                    
                lock = self.locks[path]
                if lock.owner != owner:
                    logger.warning(f"Tentative de libération d'un verrou par un autre propriétaire: {path}")
                    return False
                    
                del self.locks[path]
                await self._save_locks()
                logger.info(f"Verrou libéré sur {path}")
                return True
                
        except Exception as e:
            logger.error(f"Erreur lors de la libération du verrou: {str(e)}")
            return False
            
    async def _load_locks(self) -> None:
        """Charge l'état des verrous depuis le fichier"""
        try:
            content = await self.webdav_service.read_document(self.LOCK_FILE)
            locks_data = json.loads(content)
            
            self.locks = {
                path: FileLock(
                    path=lock['path'],
                    owner=lock['owner'],
                    created_at=datetime.fromisoformat(lock['created_at']),
                    expires_at=datetime.fromisoformat(lock['expires_at']),
                    operation=lock['operation']
                )
                for path, lock in locks_data.items()
            }
            
        except Exception as e:
            logger.warning(f"Impossible de charger les verrous: {str(e)}")
            self.locks = {}
            
    async def _save_locks(self) -> None:
        """Sauvegarde l'état des verrous dans le fichier"""
        try:
            locks_data = {
                path: {
                    'path': lock.path,
                    'owner': lock.owner,
                    'created_at': lock.created_at.isoformat(),
                    'expires_at': lock.expires_at.isoformat(),
                    'operation': lock.operation
                }
                for path, lock in self.locks.items()
            }
            
            await self.webdav_service.write_file(
                self.LOCK_FILE,
                json.dumps(locks_data, indent=2)
            )
            
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde des verrous: {str(e)}")
            
    async def _cleanup_expired_locks(self) -> None:
        """Nettoie les verrous expirés"""
        try:
            async with self._lock:
                now = datetime.now()
                expired = [
                    path for path, lock in self.locks.items()
                    if now >= lock.expires_at
                ]
                
                for path in expired:
                    del self.locks[path]
                    
                if expired:
                    await self._save_locks()
                    logger.info(f"Nettoyage de {len(expired)} verrous expirés")
                    
        except Exception as e:
            logger.error(f"Erreur lors du nettoyage des verrous: {str(e)}")
            
    async def extend_lock(self, path: str, owner: str) -> bool:
        """Étend la durée d'un verrou existant"""
        try:
            async with self._lock:
                if path not in self.locks:
                    return False
                    
                lock = self.locks[path]
                if lock.owner != owner:
                    return False
                    
                # Vérifier le nombre d'extensions
                if self.lock_extensions.get(path, 0) >= self.LOCK_EXTENSION_MAX:
                    logger.warning(f"Nombre maximum d'extensions atteint pour {path}")
                    return False
                    
                # Étendre le verrou
                now = datetime.now()
                lock.expires_at = now + timedelta(seconds=self.LOCK_TIMEOUT)
                self.lock_extensions[path] = self.lock_extensions.get(path, 0) + 1
                
                await self._save_locks()
                logger.info(f"Verrou étendu sur {path} (extension {self.lock_extensions[path]}/{self.LOCK_EXTENSION_MAX})")
                return True
                
        except Exception as e:
            logger.error(f"Erreur lors de l'extension du verrou: {str(e)}")
            return False
            
    async def __aenter__(self):
        await self.initialize()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        # Sauvegarde finale des verrous
        try:
            await self._save_locks()
        except Exception as e:
            logger.error(f"Erreur lors de la sauvegarde finale des verrous: {str(e)}") 