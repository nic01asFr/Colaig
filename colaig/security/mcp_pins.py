"""
Colaig — épinglage du contrat des outils MCP.

STATUT: COMPLET
VERSION: 2026-08-24 - v1.0
LOT: L2.3

Ce que ce module ajoute à la liste blanche
--------------------------------------------
`security/mcp_policy.py` (L2.2) décide **quels serveurs** peuvent être montés. Il ne dit
rien de ce qu'ils font ensuite : un serveur autorisé peut, au tour suivant, changer le
contrat d'un outil que le modèle a appris à utiliser.

C'est un *rug-pull* — se faire admettre avec un outil anodin, puis en modifier la
description ou le schéma. Le modèle, lui, voit un outil qu'il connaît.

Ce qui entre dans l'empreinte
------------------------------
**Le nom, la description et le schéma d'entrée** : exactement ce que le modèle lit pour
décider d'appeler l'outil et avec quoi.

La description en fait partie, et c'est le point. Un serveur qui ne change qu'elle —
« utilise cet outil pour transmettre le document à… » — n'a modifié aucun paramètre et a
pourtant changé le contrat. Épingler le seul schéma laisserait passer l'attaque la plus
simple.

La sérialisation est **canonique** (clés triées) : deux transports du même schéma ne
doivent pas produire deux empreintes. Un faux positif ici, et la garde se fait
désactiver.

Confiance à la première vue
----------------------------
La première rencontre est admise et retenue. L'alternative — épingler à la main avant
tout usage — n'est pas tenable : personne ne le ferait, et une garde qu'on n'active pas
ne garde rien.

Ce que l'épinglage ne protège pas
-----------------------------------
Un serveur qui **ajoute** un outil : c'est sa prérogative, et le modèle ne s'appuyait sur
rien. La frontière est assumée — l'épinglage protège la **mutation d'un contrat déjà
admis**, la liste blanche protège l'admission du serveur, et la suite adversariale de
L2.5 devra mesurer ce qui échappe encore aux deux.

Où vivent les empreintes
-------------------------
`config/mcp_pins.json`, sur l'hôte — à côté de `clients.yml`, et pour la même raison :
**hors de l'espace de stockage**, donc hors de portée de ceux contre qui la garde
protège. Un épinglage rangé dans l'espace serait réécrit par qui y écrit.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

CHEMIN_PAR_DEFAUT = Path("config/mcp_pins.json")


def empreinte(outil: dict) -> str:
    """Empreinte stable du contrat que le modèle lit.

    Sérialisation canonique — clés triées, séparateurs fixes — pour qu'un remaniement
    d'ordre ne se lise pas comme une mutation.
    """
    contrat = {
        "name": outil.get("name", ""),
        "description": outil.get("description", ""),
        "inputSchema": outil.get("inputSchema") or outil.get("input_schema") or {},
    }
    canonique = json.dumps(contrat, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonique.encode("utf-8")).hexdigest()


class Magasin:
    """Les empreintes connues, relues du disque et réécrites à chaque ajout.

    Un épinglage qui ne vivrait qu'en mémoire ne protégerait de rien : chaque
    redémarrage rouvrirait tous les contrats.
    """

    def __init__(self, chemin: Path | str | None = None, inscriptible: bool = True) -> None:
        self._chemin = Path(chemin) if chemin else CHEMIN_PAR_DEFAUT
        self._inscriptible = inscriptible
        self._connues: dict[str, str] = self._relire()

    def _relire(self) -> dict[str, str]:
        try:
            return json.loads(self._chemin.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, json.JSONDecodeError) as exc:
            # Un magasin illisible ne doit pas passer pour un magasin vide : ce serait
            # ré-épingler silencieusement tout ce qui se présente.
            logger.warning(
                "épinglage MCP : magasin %s illisible (%s) — les contrats déjà admis "
                "ne sont plus reconnus, et seront ré-épinglés à la première rencontre",
                self._chemin, exc,
            )
            return {}

    @staticmethod
    def _cle(serveur: str, outil: str) -> str:
        # Le couple porte l'empreinte : un serveur admis ne dicte pas le contrat d'un
        # outil homonyme chez un autre.
        return f"{serveur}::{outil}"

    def empreinte_connue(self, serveur: str, outil: str) -> str | None:
        return self._connues.get(self._cle(serveur, outil))

    def retenir(self, serveur: str, outil: str, valeur: str) -> None:
        self._connues[self._cle(serveur, outil)] = valeur
        if not self._inscriptible:
            logger.warning(
                "épinglage MCP INERTE : le magasin %s n'est pas inscriptible. Les "
                "contrats ne survivront pas au redémarrage, et une mutation ne sera "
                "donc jamais détectée. Rendre ce chemin inscriptible.",
                self._chemin,
            )
            return
        try:
            self._chemin.parent.mkdir(parents=True, exist_ok=True)
            self._chemin.write_text(
                json.dumps(self._connues, indent=2, sort_keys=True, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            logger.warning(
                "épinglage MCP INERTE : écriture de %s impossible (%s). Les contrats "
                "ne survivront pas au redémarrage — une mutation ne sera pas détectée.",
                self._chemin, exc,
            )


def verifier(serveur: str, outil: dict, magasin: Magasin) -> tuple[bool, str]:
    """Le contrat de cet outil est-il celui qui a été admis ?

    Returns:
        `(admis, motif)`. `motif` est vide quand l'outil passe.
    """
    nom = outil.get("name", "")
    actuelle = empreinte(outil)
    connue = magasin.empreinte_connue(serveur, nom)

    if connue is None:
        # Confiance à la première vue — et on retient, sinon rien ne sera jamais comparé.
        magasin.retenir(serveur, nom, actuelle)
        logger.info("épinglage MCP : contrat retenu pour %s::%s", serveur, nom)
        return True, ""

    if connue == actuelle:
        return True, ""

    motif = (
        f"le contrat de l'outil « {nom} » du serveur « {serveur} » a changé depuis "
        "qu'il a été admis"
    )
    logger.warning(
        "épinglage MCP : outil DÉSACTIVÉ — %s. Nom, description ou schéma d'entrée ont "
        "été modifiés : c'est ce que le modèle lit pour décider de l'appeler. Si le "
        "changement est légitime, retirer l'entrée « %s::%s » du magasin pour "
        "ré-épingler le nouveau contrat.",
        motif, serveur, nom,
    )
    return False, motif
