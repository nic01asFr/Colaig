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


def _empreinte(message_id: str) -> str:
    return hashlib.sha256((message_id or "").encode("utf-8")).hexdigest()[:32]


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
) -> None:
    """Ecrit la trace d'un echange. NE LEVE JAMAIS.

    La reponse est le produit ; sa trace est un confort. Un stockage en defaut ne doit
    pas faire echouer un tour de conversation qui vient d'aboutir — meme regle que pour
    les gestes de retour.
    """
    if not espace:
        return
    try:
        contenu = {
            "message_id": message_id,
            "horodatage": horodatage or str(int(time.time() * 1000)),
            "question": question,
            "reponse": reponse,
            "sources": list(sources or []),
            "confiance": confiance,
            "temps_ms": temps_ms,
        }
        await storage.mkdir(paths.echanges_dir(espace))
        await storage.upload(
            paths.echange_file(espace, _empreinte(message_id)),
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
