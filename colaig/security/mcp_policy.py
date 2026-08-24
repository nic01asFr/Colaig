"""
Colaig — point de passage unique de la liste blanche des serveurs MCP.

STATUT: COMPLET
VERSION: 2026-08-24 - v1.0
LOT: L2.2

Ce que ce module ferme
-----------------------
`WorkspaceConfig.mcp_connectors` est lu depuis `.colaig/config.yaml`, dans l'espace de
stockage. **Quiconque écrit dans cet espace y branche donc un serveur MCP distant, dont
Colaig appellera les outils avec ses propres identifiants.** Le plan du chantier désigne
ce lot comme le plus urgent, et le recensement de D37 l'a confirmé sur pièces.

Le lot L2.1 a traité le champ `instructions` de ces serveurs : il entre désormais comme
donnée balisée, non comme instruction système. **Mais l'outil, lui, s'exécute.** Déclarer
n'est pas empêcher — c'est précisément ce que la suite adversariale du lot L2.5 devra
mesurer.

Le montage d'un serveur relève donc d'une décision d'**instance**, pas d'espace.

Pourquoi le défaut est REFUS
-----------------------------
`clients.yml.example` pose pour `platform_policy` la convention « listes vides ou section
absente = aucune contrainte ». Elle est juste pour les autres champs : ils bornent ce que
**l'opérateur** déclare lui-même, et un opérateur qui ne borne rien n'a rien ouvert à
personne.

`allowed_mcp_servers` borne ce que **l'utilisateur final écrit dans son espace**. Modèle
de menace différent : « absent = tout autorisé » y reproduirait le trou même que ce lot
doit fermer. D44 a recensé quatre gardes dont le défaut est ouvert, et ce qu'elles
coûtent.

La divergence est rendue **visible dans la valeur** plutôt que cachée dans le code :

    absent ou []        aucun serveur monté
    ["*"]               tous, explicitement
    ["https://…", …]    ceux-là

Ouvrir redevient un acte. Aucun déploiement déclaré n'utilise `mcp_connectors`
aujourd'hui : le refus par défaut n'enlève rien, il empêche.

Un seul point de passage
-------------------------
Un filtre appliqué à trois sites de lecture sur quatre ne filtre rien. Comme pour le
balisage, un test de portée dépôt refuse qu'un module lise `mcp_connectors` sans passer
par ici.
"""
from __future__ import annotations

import logging
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

TOUT = "*"


def _meme_autorite(url: str, autorise: str) -> bool:
    """L'URL relève-t-elle du préfixe autorisé, sur une frontière réelle ?

    Une comparaison par `startswith` nu laisserait passer un domaine qui imite
    l'autorisé : `https://mcp.interieur.gouv.fr.attaquant.fr/mcp` commence bien par
    `https://mcp.interieur.gouv.fr`. L'autorité est donc comparée entière, et le chemin
    par segment.
    """
    cible, reference = urlsplit(url), urlsplit(autorise)
    if (cible.scheme, cible.netloc) != (reference.scheme, reference.netloc):
        return False
    chemin_reference = reference.path.rstrip("/")
    if not chemin_reference:
        return True
    return cible.path == chemin_reference or cible.path.startswith(chemin_reference + "/")


def connecteurs_autorises(connecteurs, politique) -> list:
    """Ne retient que les serveurs MCP que la politique d'instance admet.

    Args:
        connecteurs: les `MCPConnectorConfig` déclarés par un espace — contenu
            **non fiable**, écrit dans le `config.yaml` de l'espace.
        politique: la `PlatformPolicy` de l'instance, chargée depuis `clients.yml`.

    Returns:
        La sous-liste admise. Vide si la politique ne déclare rien.
    """
    admis = list(getattr(politique, "allowed_mcp_servers", None) or [])
    declares = list(connecteurs or [])
    if not declares:
        return []

    if TOUT in admis:
        return declares

    retenus, ecartes = [], []
    for connecteur in declares:
        url = getattr(connecteur, "url", "") or ""
        if any(_meme_autorite(url, a) for a in admis):
            retenus.append(connecteur)
        else:
            ecartes.append(getattr(connecteur, "name", "") or url)

    if ecartes:
        # Un serveur écarté en silence produit un incident incompréhensible : l'agent
        # n'a pas l'outil, personne ne sait pourquoi.
        logger.warning(
            "serveur(s) MCP écarté(s) — %s. Ils sont déclarés dans le config.yaml d'un "
            "espace, mais pas dans `platform_policy.allowed_mcp_servers` de "
            "clients.yml. Monter un serveur MCP est une décision d'instance : "
            "ajoutez-y son URL, ou `\"*\"` pour tous les autoriser explicitement.",
            ", ".join(ecartes),
        )
    return retenus


_politique_en_cache = None


def politique_instance():
    """La `PlatformPolicy` de cette instance, lue une fois.

    `clients.yml` vit sur l'hote, pas dans l'espace de stockage — c'est ce qui fait sa
    valeur : la politique echappe a ceux qu'elle borne. Elle n'a donc pas a passer par
    `StorageProtocol`, et `config.load_platform_policy` la lit deja directement.

    Le cache evite de relire le fichier a chaque message. Le prix est qu'un changement
    de politique demande un redemarrage — acceptable pour une contrainte d'operateur, et
    coherent avec le reste de la configuration d'instance.
    """
    global _politique_en_cache
    if _politique_en_cache is None:
        from colaig.config import load_platform_policy
        _politique_en_cache = load_platform_policy()
    return _politique_en_cache


def oublier_la_politique() -> None:
    """Vide le cache — reserve aux tests, qui doivent pouvoir varier la politique."""
    global _politique_en_cache
    _politique_en_cache = None
