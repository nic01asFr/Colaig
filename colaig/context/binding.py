"""
Colaig — quel espace documentaire un salon désigne-t-il ?

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L3.1

Portage de `Plateforme_colaig/app/agent/workspace_binding.py` (MIT), avec deux
changements assumés — voir plus bas.

Ce que ce module décide
------------------------
Le rattachement décide **quels documents un salon peut lire**. D42/D43 ont établi que
le dossier partagé est l'unité de confidentialité et que le salon décide qui interroge :
un mauvais rattachement expose les documents d'un autre service.

Depuis L3.7, il décide aussi **où l'on écrit** — un document déposé dans le salon est
rangé dans l'espace résolu. Un rattachement erroné ne donne plus seulement de mauvaises
réponses : il dépose des fichiers dans le mauvais dossier.

Module **pur** : aucune I/O, donc entièrement éprouvable hors ligne.

Les six niveaux, rangés par force du consentement
---------------------------------------------------
    conversation        le salon figure dans `conversations`   l'utilisateur, explicitement
    user_id             espace personnel, en DM                l'utilisateur
    room_name           regex du descripteur                   le propriétaire de l'espace
    room_topic          regex du descripteur                   le propriétaire de l'espace
    name_convention     le nom du salon ressemble à l'espace    -- opt-in, voir ci-dessous
    default_workspace   repli configuré                        l'exploitant

CHANGEMENT 1 — LA CONVENTION DE NOM DEVIENT OPT-IN
----------------------------------------------------
La version déployée rattachait un salon nommé « Urbanisme » à l'espace « Urbanisme »
**sans que personne ne l'ait décidé**. D41 le formule ainsi :

    « Le liage automatique à l'invitation est séduisant et dangereux. La version
      déployée l'a fait ; il faut décider si le tronc le reprend, et si oui, en
      retirant la règle de convention de nom OU EN LA RENDANT OPT-IN COMME LES DEUX
      REGEX. »

C'est la seconde branche : le propriétaire déclare `match.name_convention: true` dans
son `config.yaml`. La commodité est gardée là où elle est voulue, et le consentement
devient explicite — exactement comme pour les deux regex.

CHANGEMENT 2 — LE MOTIF EST PORTÉ, PAS DÉDUIT DU SCORE
--------------------------------------------------------
La version déployée reconstituait le motif à partir du score (`_reason_for_score`). Or
le score inclut `priority`, qui vient du `config.yaml` : un espace par défaut (score 10)
avec `priority: 90` était annoncé comme rattaché **par convention de nom**.

Le motif ment donc dès que la priorité comble l'écart entre deux niveaux — et c'est ce
motif qu'on montre à l'utilisateur pour justifier le rattachement. Un motif faux sur une
décision de confidentialité vaut moins que pas de motif du tout.

`priority` conserve son rôle — départager deux espaces qui correspondent DE LA MÊME
FAÇON — sans pouvoir faire remonter un niveau faible au-dessus d'un niveau fort.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

# Les niveaux. Les écarts sont larges, mais `priority` ne les franchit plus : le motif
# et le rang sont désormais rendus séparément du score.
NIVEAUX: tuple[tuple[str, int], ...] = (
    ("conversation", 1000),
    ("user_id", 500),
    ("room_name", 300),
    ("room_topic", 200),
    ("name_convention", 100),
    ("default_workspace", 10),
)
_RANG = {motif: score for motif, score in NIVEAUX}


def _normaliser(s: str) -> str:
    """Minuscules, sans accents, alphanumérique — « PREFECTURE » == « Préfecture »."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", s.lower())


def _cherche(motif: str, texte: str) -> bool:
    """Recherche tolérante : faux si le motif est invalide ou le texte vide.

    Un `config.yaml` s'écrit à la main. Une regex fautive ne doit pas faire tomber la
    résolution de TOUS les salons — elle ne doit simplement pas correspondre.
    """
    if not motif or not texte:
        return False
    try:
        return re.search(motif, texte) is not None
    except re.error:
        return False


def _liste(v: Any) -> list[str]:
    if not v:
        return []
    return [v] if isinstance(v, str) else list(v)


def score_candidat(
    *,
    descripteur: dict | None,
    nom_dossier: str,
    chemin: str,
    room_id: str,
    room_name: str = "",
    room_topic: str = "",
    user_id: str = "",
    default_workspace: str = "",
) -> tuple[int, str]:
    """Rend `(score, motif)` pour un espace candidat. `(0, "")` = aucune correspondance.

    Le motif est RENDU, jamais reconstitué depuis le score — voir l'en-tête.
    """
    descripteur = descripteur or {}
    match = descripteur.get("match") or {}
    try:
        priorite = int(descripteur.get("priority", 0) or 0)
    except (TypeError, ValueError):
        # `priority: "haute"` ne doit ni lever, ni valoir zéro par accident silencieux.
        priorite = 0

    salons = _liste(descripteur.get("conversations")) + _liste(match.get("rooms"))
    if room_id and room_id in salons:
        return _RANG["conversation"] + priorite, "conversation"

    if user_id and user_id in _liste(descripteur.get("user_ids")):
        return _RANG["user_id"] + priorite, "user_id"

    if _cherche(match.get("room_name", ""), room_name):
        return _RANG["room_name"] + priorite, "room_name"

    if _cherche(match.get("room_topic", ""), room_topic):
        return _RANG["room_topic"] + priorite, "room_topic"

    # OPT-IN (changement 1) : sans déclaration du propriétaire, la ressemblance des noms
    # ne rattache rien. C'est une coïncidence de vocabulaire, pas une décision.
    if match.get("name_convention") is True and room_name:
        noms = (nom_dossier, descripteur.get("name", ""),
                descripteur.get("workspace_id", ""))
        if any(n and _normaliser(n) == _normaliser(room_name) for n in noms):
            return _RANG["name_convention"] + priorite, "name_convention"

    if default_workspace and chemin.strip("/") == default_workspace.strip("/"):
        # Pas de priorité sur le repli : il est le dernier recours, et lui en donner
        # une permettrait de capter des salons par simple configuration.
        return _RANG["default_workspace"], "default_workspace"

    return 0, ""


def selectionner_espace(
    candidats: list[dict],
    *,
    room_id: str,
    room_name: str = "",
    room_topic: str = "",
    user_id: str = "",
    default_workspace: str = "",
) -> dict | None:
    """Le meilleur espace pour ce salon, ou `None` si aucun ne correspond.

    NE RIEN RATTACHER EST UNE RÉPONSE. Rattacher au hasard n'en est pas une : le salon
    lirait — et depuis L3.7, écrirait — dans un dossier que personne n'a désigné.

    Le départage se fait d'abord sur le NIVEAU, ensuite sur le score. Une priorité de
    10 000 sur un repli ne peut donc pas battre un rattachement explicite : sinon
    `priority` deviendrait un moyen, pour le propriétaire d'un espace, de capter les
    salons rattachés à d'autres.
    """
    meilleur, meilleur_cle = None, (-1, -1)
    for c in candidats:
        score, motif = score_candidat(
            descripteur=c.get("descriptor"),
            nom_dossier=c.get("name", ""),
            chemin=c.get("path", ""),
            room_id=room_id,
            room_name=room_name,
            room_topic=room_topic,
            user_id=user_id,
            default_workspace=default_workspace,
        )
        if not motif:
            continue
        cle = (_RANG[motif], score)      # niveau d'abord, priorité ensuite
        if cle > meilleur_cle:
            meilleur_cle, meilleur = cle, {**c, "score": score, "motif": motif}
    return meilleur
