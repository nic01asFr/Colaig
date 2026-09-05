"""
Colaig — le journal des echanges, relisible et durable.

POURQUOI CE MODULE EXISTE
---------------------------
La porte 1 demande « une semaine de dogfooding, releve des 👍👎 ». Ce protocole suppose
qu'un humain reagisse a chaque reponse. Le taux de retour mesure est de 17 % — un geste
sur six reponses — et l'utilisateur a dit qu'il ne le ferait pas.

Or les pouces n'etaient qu'un PROXY pour « la reponse etait-elle bonne ». Colaig produit
deja, a chaque echange et sans que personne n'intervienne : la question, les sources
retenues, la confiance, le temps de reponse. C'est plus riche qu'un pouce, et cela ne
demande rien.

CE QUI MANQUAIT
-----------------
Ces elements partaient dans le journal du POD, en une ligne formatee. Deux defauts :

1. ils meurent au redeploiement — seize pods se sont succede le 30/08/2026 ; une semaine
   d'observation aurait perdu ses donnees a chaque mise a jour ;
2. ils ne sont pas relisibles — une chaine formatee se relit a coups d'expression
   reguliere, qui casse au premier changement de formulation.

Meme lecon que le magasin de cles Matrix, meme correctif : CE QUI DOIT SURVIVRE A UN
REDEMARRAGE NE VIT PAS DANS LE POD.

CE QUE CE MODULE NE FAIT PAS
------------------------------
Il ne remplace pas les retours : un 👍 dit ce qu'un HUMAIN a pense, et rien ne le
deduit. Il enleve seulement au pouce le monopole de l'observation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import Any

from colaig import paths

logger = logging.getLogger(__name__)


def _empreinte(message_id: str, *, secours: str = "") -> str:
    """Nom de fichier d'un echange.

    UN IDENTIFIANT DE MESSAGE N'EST PAS TOUJOURS FOURNI.
    ------------------------------------------------------
    Il l'est sur Matrix (`event_id`), ou il sert aussi a DEDOUBLONNER un evenement
    redelivre apres reconnexion. Il ne l'est pas sur `/ask` — l'endpoint par lequel
    passe toute la mesure. Le nom derivant du seul `message_id`, les 135 questions
    d'une campagne ecrivaient 135 fois le meme fichier : le journal cense « survivre
    au redeploiement » gardait un echange sur 135 (releve du 04/09/2026).

    A defaut d'identifiant, on nomme d'apres ce qui distingue l'echange lui-meme
    (`secours` : horodatage + question + reponse). Le dedoublonnage reste entier la
    ou un identifiant existe.
    """
    graine = message_id or secours
    return hashlib.sha256(graine.encode("utf-8")).hexdigest()[:32]


async def consigner_echange(
    storage: Any,
    espace: str,
    *,
    question: str,
    reponse: str,
    sources: list[str],
    confiance: float | None,
    temps_ms: int,
    message_id: str,
    horodatage: str = "",
    passages: list[dict] | None = None,
) -> None:
    """Ecrit la trace d'un echange. NE LEVE JAMAIS.

    La reponse est le produit ; sa trace est un confort. Un stockage en defaut ne doit
    pas faire echouer un tour de conversation qui vient d'aboutir — meme regle que pour
    les gestes de retour.
    """
    if not espace:
        return
    try:
        quand = horodatage or str(int(time.time() * 1000))
        contenu = {
            "message_id": message_id,
            "horodatage": quand,
            "question": question,
            "reponse": reponse,
            "sources": list(sources or []),
            # LES PASSAGES, PAS SEULEMENT LES FICHIERS.
            #
            # Le decoupage etant par article, un fichier porte des dizaines de
            # passages. Avec les seuls noms de fichiers, on ne distingue pas « le
            # passage attendu a ete servi et le modele ne s'en est pas saisi » de
            # « c'est le passage VOISIN qui a ete servi » — deux constats qui
            # appellent des corrections opposees. Le 04/09/2026, il a fallu le
            # deduire de la lecture de 21 reponses.
            "passages": list(passages or []),
            "confiance": confiance,
            "temps_ms": temps_ms,
        }
        await storage.mkdir(paths.echanges_dir(espace))
        await storage.upload(
            paths.echange_file(
                espace,
                _empreinte(message_id, secours=f"{quand}|{question}|{reponse}"),
            ),
            json.dumps(contenu, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception:
        logger.debug("echange non consigne pour %s", espace, exc_info=True)


async def lire_echanges(storage: Any, espace: str) -> list[dict]:
    """Relit les echanges d'un espace, du plus ancien au plus recent."""
    try:
        fichiers = await storage.list_files(paths.echanges_dir(espace))
    except Exception:
        return []

    echanges: list[dict] = []
    for f in fichiers:
        chemin = getattr(f, "path", "") or ""
        if not chemin.endswith(".json"):
            continue
        try:
            echanges.append(json.loads(await storage.download(chemin)))
        except Exception:
            logger.warning("echange illisible, ignore: %s", chemin)

    echanges.sort(key=lambda e: (str(e.get("horodatage", "")), e.get("message_id", "")))
    return echanges
