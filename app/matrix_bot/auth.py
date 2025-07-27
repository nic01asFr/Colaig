# SPDX-FileCopyrightText: 2021 - 2022 Isaac Beverly <https://github.com/imbev>
# SPDX-FileCopyrightText: 2023 Pôle d'Expertise de la Régulation Numérique <contact.peren@finances.gouv.fr>
#
# SPDX-License-Identifier: MIT

import base64
import json
import secrets
import os
from dataclasses import dataclass
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from filelock import FileLock
from app.matrix_bot.config import bot_lib_config, logger


def _get_key_from_password(password: str, salt):
    """calculate a cryptographic key from the password"""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=100000,
        backend=default_backend(),
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode(encoding="utf-8")))


def encrypt(data: str, password: str, salt=bot_lib_config.salt) -> str:
    """encrypt the data given with the password"""
    fernet_encoder = Fernet(_get_key_from_password(password, salt))
    return fernet_encoder.encrypt(data.encode(encoding="utf-8")).decode()


def decrypt(encrypted_data: str, password: str, salt=bot_lib_config.salt) -> str:
    """decrypt the data given with the password"""
    fernet_encoder = Fernet(_get_key_from_password(password, salt))
    return fernet_encoder.decrypt(encrypted_data.encode(encoding="utf-8")).decode()


@dataclass(frozen=True)
class Credentials:
    """
    A class to store and handle login credentials.
    """

    homeserver: str
    """The homeserver for the bot to connect to. Begins with "https://"."""
    username: str
    """The username of the bot to connect to"""
    password: str
    """The password of the bot to connect to"""
    session_stored_file_path: Path = bot_lib_config.session_path
    """Path to the file that will be used to store the session informations"""


@dataclass
class AuthLogin:
    """
    A class to store and handle login credentials.
    Store the session information in a file encrypted with the password
    """

    credentials: Credentials
    device_id: str = ""
    access_token: str = ""
    device_name: str = ""

    def __post_init__(self):
        self.device_name = f"Bot Client using Matrix-Bot id {secrets.token_urlsafe(20)}"
        self.read_session_file()

    def read_session_file(self):
        """Reads and decrypts the device_id and access_token from file with safer handling"""
        if not self.credentials.session_stored_file_path:
            logger.warning("Aucun chemin de fichier de session défini")
            return
            
        logger.info(f"Lecture du fichier de session: {self.credentials.session_stored_file_path}")
        
        if not self.credentials.session_stored_file_path.exists():
            logger.info(f"Fichier de session non trouvé: {self.credentials.session_stored_file_path}")
            return
            
        lock_path = str(self.credentials.session_stored_file_path) + ".lock"
        lock = FileLock(lock_path, timeout=30)
        
        try:
            logger.info("Acquisition du verrou sur le fichier de session...")
            with lock:
                logger.info("Verrou acquis, lecture du fichier...")
                with open(self.credentials.session_stored_file_path, "r") as store_file:
                    encrypted_session_data = store_file.read()
                
                logger.info("Tentative de déchiffrement des données de session...")
                data = json.loads(decrypt(encrypted_session_data, self.credentials.password))
                if len(data) >= 3:
                    self.device_id, self.access_token, self.device_name = data
                    logger.info(f"Session chargée avec succès: device_id={self.device_id}")
                else:
                    logger.error("Format de session invalide")
        except InvalidToken as invalid_token:
            logger.error(f"Session invalide: {str(invalid_token)}")
            # Ne pas lever d'exception pour permettre une nouvelle connexion si nécessaire
        except Exception as e:
            logger.error(f"Erreur lors de la lecture du fichier de session: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

    def write_session_file(self):
        """Encrypts and writes to file the device_id and access_token safely."""
        if not self.credentials.session_stored_file_path:
            logger.info("device_id and access_token will not be saved (no path)")
            return

        if not (self.device_id and self.access_token):
            logger.warning(f"Can't save credentials: {self.device_id=} or {self.access_token=}")
            return
            
        # Créer le répertoire parent si nécessaire
        os.makedirs(os.path.dirname(self.credentials.session_stored_file_path), exist_ok=True)
        
        lock_path = str(self.credentials.session_stored_file_path) + ".lock"
        lock = FileLock(lock_path, timeout=30)
        
        try:
            with lock:
                encrypted_session = encrypt(
                    json.dumps([self.device_id, self.access_token, self.device_name]),
                    self.credentials.password,
                )
                
                with open(self.credentials.session_stored_file_path, "w") as store_file:
                    store_file.write(encrypted_session)
                    
                logger.info(f"Session sauvegardée: {self.device_name} (device_id: {self.device_id})")
        except Exception as e:
            logger.error(f"Erreur lors de l'écriture du fichier de session: {str(e)}")
