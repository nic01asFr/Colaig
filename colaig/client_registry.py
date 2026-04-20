"""
Colaig — ClientRegistry

Gère N clients/tenants, chacun avec sa propre paire storage + messaging.
Chargé depuis config/clients.yml (opt-in : si absent → mode single-client).

Format clients.yml :

    clients:
      - id: acme
        storage:
          backend: webdav
          url: https://nextcloud.acme.com/...
          username: colaig
          password: secret
        messaging:
          backend: matrix
          homeserver: https://matrix.acme.com
          username: "@colaig:acme.com"
          password: secret
        # llm: absent → hérite de ColaigConfig (LLM partagé)

      - id: ministry-agri
        storage:
          backend: s3
          endpoint_url: https://minio.agri.gouv.fr
          access_key: xxx
          secret_key: yyy
          bucket: colaig-agri
        messaging:
          backend: telegram
          bot_token: "123456:ABC..."
        llm:
          backend: openai
          api_url: https://api.openai.com
          api_key: sk-xxx
          model_chat: gpt-4o
          model_embed: text-embedding-3-small
"""

from __future__ import annotations

import logging
import os
from typing import Iterator

import yaml

from colaig.models import ClientConfig

logger = logging.getLogger(__name__)


class ClientRegistry:
    """Registre de N clients/tenants Colaig.

    Chargé depuis un fichier YAML. Si le fichier est absent ou vide,
    le registre est vide (Colaig fonctionne en mode single-client via
    les variables d'env habituelles).

    Args:
        clients: Liste de ClientConfig.
    """

    def __init__(self, clients: list[ClientConfig]) -> None:
        self._clients: dict[str, ClientConfig] = {cc.client_id: cc for cc in clients}

    # --- Factories ---

    @classmethod
    def from_yaml(cls, path: str) -> "ClientRegistry":
        """Charge le registre depuis un fichier YAML.

        Returns un registre vide si le fichier n'existe pas.
        Lève ValueError si le fichier existe mais est malformé.
        """
        if not os.path.exists(path):
            logger.debug("clients.yml absent (%s) → mode single-client", path)
            return cls([])

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        raw_clients = data.get("clients", [])
        if not isinstance(raw_clients, list):
            raise ValueError(f"clients.yml: 'clients' doit être une liste ({path})")

        clients: list[ClientConfig] = []
        for entry in raw_clients:
            if not isinstance(entry, dict):
                raise ValueError(f"clients.yml: entrée invalide (doit être un dict) : {entry}")
            cc = ClientConfig.from_dict(entry)
            if not cc.client_id:
                raise ValueError(f"clients.yml: entrée sans 'id' : {entry}")
            clients.append(cc)

        logger.info("ClientRegistry: %d client(s) chargé(s) depuis %s", len(clients), path)
        return cls(clients)

    @classmethod
    def from_yaml_or_empty(cls, path: str) -> "ClientRegistry":
        """Comme from_yaml mais ne lève pas d'exception — log l'erreur et retourne vide."""
        try:
            return cls.from_yaml(path)
        except Exception as exc:
            logger.error("ClientRegistry: erreur chargement %s : %s", path, exc)
            return cls([])

    # --- Accès ---

    def get(self, client_id: str) -> ClientConfig | None:
        """Retourne le ClientConfig par client_id, ou None."""
        return self._clients.get(client_id)

    def all(self) -> list[ClientConfig]:
        """Retourne la liste de tous les clients."""
        return list(self._clients.values())

    def __iter__(self) -> Iterator[ClientConfig]:
        return iter(self._clients.values())

    def __len__(self) -> int:
        return len(self._clients)

    def __bool__(self) -> bool:
        return len(self._clients) > 0

    def __repr__(self) -> str:
        ids = list(self._clients.keys())
        return f"ClientRegistry({ids})"
