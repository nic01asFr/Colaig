# SPDX-License-Identifier: MIT
"""
Intégration SSPCloud (Onyxia / datalab.sspcloud.fr).

Récupère automatiquement la clé d'API LLM de l'utilisateur SSPCloud sans qu'il
ait à la saisir : Onyxia persiste la configuration « AI Assistant » (compte
datalab → Profil) dans un Secret Kubernetes du namespace, nommé
`<service>-secretassistant`, clé `config.json` :

    {"api_keys": {"OPENAI_API_KEY": "sk-..."}, ...}

Au démarrage, si on tourne dans un pod SSPCloud (ServiceAccount monté) et que la
clé LLM n'est pas déjà fournie, on lit ce Secret via l'API Kubernetes en
utilisant le token du ServiceAccount du pod. Cela requiert le droit de lecture
des secrets du namespace (`kubernetes.role: edit` dans le chart Helm).

La base URL et le modèle LLM ne viennent PAS de ce Secret (le `model_provider_id`
du config.json datalab pointe souvent vers un modèle inexistant) : ils sont
fournis explicitement par la config (env `LLM_BASE_URL` / `LLM_MODEL`, valeurs par
défaut SSPCloud dans le chart).

Référence d'implémentation : projet qgis-sspcloud (hub/main.py).
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
from typing import List, Optional

import httpx

from app.matrix_bot.config import logger

# Emplacements standards des credentials du ServiceAccount dans un pod K8s.
_SA_TOKEN_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/token"
_SA_NAMESPACE_PATH = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"
_K8S_HOST = "https://kubernetes.default.svc"

# Valeurs par défaut SSPCloud (Open WebUI + Ollama). Surchargées par env.
DEFAULT_SSPCLOUD_LLM_BASE_URL = "https://llm.lab.sspcloud.fr/api"


def in_kubernetes() -> bool:
    """True si on tourne dans un pod Kubernetes (token ServiceAccount présent)."""
    return Path(_SA_TOKEN_PATH).exists()


def extract_llm_key_from_secrets(secret_items: List[dict]) -> Optional[str]:
    """Extrait la clé OPENAI_API_KEY du Secret `*-secretassistant`.

    Fonction pure (testable sans cluster). `secret_items` est la liste
    `items` renvoyée par l'API K8s `GET .../secrets`.

    Returns:
        La clé si trouvée et non vide, sinon None.
    """
    for secret in secret_items or []:
        name = (secret.get("metadata") or {}).get("name", "")
        if "secretassistant" not in name:
            continue
        raw = (secret.get("data") or {}).get("config.json")
        if not raw:
            continue
        try:
            cfg = json.loads(base64.b64decode(raw).decode("utf-8"))
        except Exception:
            continue
        key = (cfg.get("api_keys") or {}).get("OPENAI_API_KEY", "")
        if key:
            logger.info(f"[SSPCLOUD] Clé LLM trouvée dans le secret {name!r}")
            return key
    return None


async def read_sspcloud_llm_key(timeout: float = 15.0) -> Optional[str]:
    """Lit la clé LLM de l'utilisateur depuis les secrets du namespace K8s.

    Returns None si hors cluster, sans permission, ou clé absente.
    """
    token_file = Path(_SA_TOKEN_PATH)
    ns_file = Path(_SA_NAMESPACE_PATH)
    if not token_file.exists() or not ns_file.exists():
        logger.debug("[SSPCLOUD] Pas dans un pod K8s, lecture clé LLM ignorée")
        return None

    try:
        token = token_file.read_text().strip()
        namespace = ns_file.read_text().strip()
    except Exception as exc:
        logger.warning(f"[SSPCLOUD] Lecture credentials ServiceAccount échouée: {exc}")
        return None

    headers = {"Authorization": f"Bearer {token}"}
    url = f"{_K8S_HOST}/api/v1/namespaces/{namespace}/secrets"
    try:
        # verify=False : l'API in-cluster présente un certificat interne.
        async with httpx.AsyncClient(verify=False, timeout=timeout) as client:
            resp = await client.get(url, headers=headers, params={"fieldSelector": "type=Opaque"})
            if resp.status_code >= 400:
                logger.warning(
                    f"[SSPCLOUD] Lecture secrets refusée ({resp.status_code}). "
                    f"Vérifier kubernetes.role=edit. Body: {resp.text[:200]}"
                )
                return None
            items = resp.json().get("items") or []
    except Exception as exc:
        logger.warning(f"[SSPCLOUD] Appel API K8s échoué: {exc}")
        return None

    return extract_llm_key_from_secrets(items)


async def bootstrap_llm_config(config) -> bool:
    """Renseigne la clé LLM SSPCloud dans `config` si nécessaire.

    Ne fait rien si une clé chat est déjà fournie (`llm_api_key` ou
    `albert_api_token`) : la config explicite prime sur l'auto-découverte.
    Pose aussi une base URL SSPCloud par défaut si aucune n'est configurée.

    Returns:
        True si une clé a été récupérée et appliquée, False sinon.
    """
    if config.llm_api_key or config.albert_api_token:
        logger.debug("[SSPCLOUD] Clé LLM déjà fournie, auto-découverte ignorée")
        return False

    key = await read_sspcloud_llm_key()
    if not key:
        return False

    config.llm_api_key = key
    if not config.llm_base_url:
        config.llm_base_url = DEFAULT_SSPCLOUD_LLM_BASE_URL

    # Exporter dans l'environnement pour que les bots lancés en sous-process
    # (BotRunner → `python -m app`) héritent de la clé et de la base URL.
    os.environ["LLM_API_KEY"] = config.llm_api_key
    os.environ["LLM_BASE_URL"] = config.llm_base_url

    logger.info(
        f"[SSPCLOUD] LLM configuré automatiquement "
        f"(base={config.chat_api_base}, modèle={config.effective_llm_model})"
    )
    return True
