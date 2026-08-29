"""
Colaig — auto-découverte de la clé LLM dans un pod Onyxia / SSP Cloud.

STATUT: COMPLET
VERSION: 2026-08-29 - v1.0
LOT: L3.6

Ce que ce module fait
-----------------------
Sur Onyxia, l'utilisateur renseigne sa clé LLM dans son espace. Un pod portant le rôle
`edit` peut explorer le namespace et l'y retrouver — plutôt que d'exiger qu'on la lui
repasse par `--set llm.apiKey` à chaque lancement.

L'ordre est délibéré :

    1. `LLM_API_KEY` renseignée      -> on s'arrête là, sans toucher au cluster
    2. dans un pod, rôle `edit`      -> on lit les secrets du namespace
    3. rien                           -> on rend la RAISON, jamais une exception

Une clé passée explicitement gagne toujours : c'est l'opérateur qui a décidé, et une
découverte ne doit pas pouvoir remplacer un choix délibéré.

LE RISQUE, ET POURQUOI LA SÉLECTION EST ÉTROITE
--------------------------------------------------
Le rôle `edit` donne au pod la lecture de **tous les secrets du namespace** — mots de
passe de bases, jetons S3, identifiants des services voisins.

Une découverte qui prendrait « le premier secret ressemblant à une clé » enverrait donc
un jour un mot de passe PostgreSQL à un endpoint LLM tiers. C'est une exfiltration, même
si personne ne l'a voulue.

**Seuls les secrets dont le NOM désigne le LLM sont regardés.** `postgres-password`
n'est jamais lu, quoi qu'il contienne.

**Et aucun essai spéculatif.** On ne teste pas des candidats contre l'endpoint pour voir
lequel authentifie : ce serait envoyer les identifiants des autres services au LLM, un
par un, jusqu'à en trouver un bon — pire que le problème qu'on résout. Un test refuse
la présence même d'un appel sortant dans ce fichier.

CE QUE JE N'AI PAS PU VÉRIFIER
--------------------------------
Le nom exact sous lequel Onyxia range la clé d'un espace. `_NOMS_CANDIDATS` est un
**point de départ**, surchargeable par `COLAIG_SSPCLOUD_SECRETS`.

C'est pourquoi la découverte **journalise ce qu'elle a trouvé et où** : un premier
déploiement doit pouvoir dire « j'ai vu ces secrets, aucun ne désigne le LLM » plutôt
que d'échouer sans un mot. Le nom de la source est journalisé ; **jamais la valeur**.
"""
from __future__ import annotations

import base64
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Emplacement conventionnel du compte de service, monté par Kubernetes dans tout pod.
_RACINE_COMPTE = Path("/var/run/secrets/kubernetes.io/serviceaccount")

# Noms de secrets qui DÉSIGNENT le LLM. Tout ce qui n'est pas dans cette liste est
# ignoré, quel qu'en soit le contenu — voir le risque expliqué en tête de module.
#
# Liste non verifiee contre un espace Onyxia reel : c'est un point de depart, et
# `COLAIG_SSPCLOUD_SECRETS` la remplace.
_NOMS_CANDIDATS = ("sspcloud-llm", "llm-api-key", "colaig-llm", "openai-api-key")

# Clés, à l'intérieur d'un secret retenu, qui portent la valeur.
_CLES_CANDIDATES = ("LLM_API_KEY", "ALBERT_API_KEY", "api_key", "apiKey", "token",
                    "OPENAI_API_KEY")


def _noms_admis() -> tuple[str, ...]:
    surcharge = os.environ.get("COLAIG_SSPCLOUD_SECRETS", "").strip()
    if surcharge:
        return tuple(n.strip() for n in surcharge.split(",") if n.strip())
    return _NOMS_CANDIDATS


def _dans_un_pod() -> bool:
    return (_RACINE_COMPTE / "token").is_file() and bool(
        os.environ.get("KUBERNETES_SERVICE_HOST"))


async def _lire_secrets(namespace: str, jeton: str) -> dict:
    """Interroge l'API Kubernetes pour les secrets du namespace.

    Isolé pour que les tests n'aient pas à simuler un cluster : c'est la seule fonction
    du module qui parle au réseau.

    Lève `PermissionError` sur un 403 — le cas le plus probable d'un premier
    déploiement, quand le rôle `edit` manque. L'appelant le dit à l'opérateur.
    """
    import httpx

    hote = os.environ.get("KUBERNETES_SERVICE_HOST", "")
    port = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
    url = f"https://{hote}:{port}/api/v1/namespaces/{namespace}/secrets"

    ca = _RACINE_COMPTE / "ca.crt"
    verify = str(ca) if ca.is_file() else True

    async with httpx.AsyncClient(timeout=10.0, verify=verify) as http:
        reponse = await http.get(url, headers={"Authorization": f"Bearer {jeton}"})
        if reponse.status_code == 403:
            raise PermissionError(
                "secrets is forbidden — le pod n'a pas le rôle `edit` sur le namespace")
        reponse.raise_for_status()
        return reponse.json()


def _extraire(secrets: dict) -> tuple[str, str]:
    """Cherche la clé parmi les secrets DONT LE NOM désigne le LLM.

    Rend `(valeur, nom_du_secret)`, ou `("", "")`. La valeur n'est jamais journalisée.
    """
    admis = _noms_admis()
    vus: list[str] = []

    for secret in secrets.get("items") or []:
        nom = ((secret.get("metadata") or {}).get("name") or "")
        vus.append(nom)
        if nom not in admis:
            continue
        donnees = secret.get("data") or {}
        for cle in _CLES_CANDIDATES:
            brut = donnees.get(cle)
            if not brut:
                continue
            try:
                valeur = base64.b64decode(brut).decode("utf-8").strip()
            except Exception:                                   # noqa: BLE001
                logger.warning("secret %s : champ %s illisible", nom, cle)
                continue
            if valeur:
                return valeur, nom

    # Ce que l'on a VU, pour qu'un premier déploiement soit diagnosticable. Des noms,
    # jamais des valeurs.
    logger.info("sspcloud : aucun secret ne désigne le LLM parmi %s "
                "(noms admis : %s)", vus or "(aucun)", list(admis))
    return "", ""


async def decouvrir_cle() -> tuple[str, str]:
    """Rend `(cle, source)`. `cle` vide si rien n'est trouvé — `source` dit pourquoi.

    NE LÈVE JAMAIS. Colaig doit démarrer sur un poste de développement comme dans un
    pod, et un défaut de découverte n'est pas un défaut de démarrage : c'est la
    validation de configuration qui décidera si l'absence de clé est bloquante.
    """
    explicite = (os.environ.get("LLM_API_KEY") or "").strip()
    if explicite:
        return explicite, "LLM_API_KEY explicite"

    if not _dans_un_pod():
        return "", ("hors d'un pod Kubernetes — aucune découverte possible "
                    "(renseignez LLM_API_KEY)")

    try:
        namespace = (_RACINE_COMPTE / "namespace").read_text(encoding="utf-8").strip()
        jeton = (_RACINE_COMPTE / "token").read_text(encoding="utf-8").strip()
    except OSError as erreur:
        return "", f"compte de service illisible : {erreur}"

    try:
        secrets = await _lire_secrets(namespace, jeton)
    except PermissionError as erreur:
        return "", str(erreur)
    except Exception as erreur:                                 # noqa: BLE001
        return "", f"API Kubernetes injoignable : {erreur}"

    valeur, nom = _extraire(secrets)
    if valeur:
        logger.info("sspcloud : clé LLM découverte dans le secret %s "
                    "du namespace %s", nom, namespace)
        return valeur, f"secret {nom} (namespace {namespace})"

    return "", (f"aucun secret désignant le LLM dans {namespace} — "
                "renseignez la clé dans votre espace Onyxia, ou "
                "COLAIG_SSPCLOUD_SECRETS si elle porte un autre nom")
