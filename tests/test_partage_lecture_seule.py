"""
Contrat — ce qui arrive à un dossier partagé en LECTURE SEULE avec Colaig.

STATUT: TESTE
VERSION: 2026-08-24 - v1.0
LOT: L2.1b

La topologie d'origine
-----------------------
Colaig dispose de son propre WebDAV. Un collègue **partage un dossier depuis le sien** ;
ce dossier apparaît à la racine du WebDAV de Colaig, et devient un espace de travail. Le
collègue crée ensuite son salon Tchap et y invite qui de droit. Dans le Bureau numérique
du MTES, salon et dossier étaient créés ensemble et portaient le même nom.

Un partage porte donc **un niveau de droit choisi par celui qui partage** : lecture, ou
lecture et écriture. Les deux existent, et rien n'oblige un collègue à donner l'écriture.

Ce que ce test fixe
--------------------
Aujourd'hui, un partage en lecture seule **n'est pas un espace dégradé : il n'est pas un
espace du tout.** `run_workspace_discovery_loop` tente d'écrire `.colaig/config.yaml`,
prend un 403, et met le dossier dans `_perm_skip` — définitivement, sans nouvel essai.

Ce comportement est **volontaire et correct** : sans dossier d'instance, Colaig ne sait
ni indexer, ni se souvenir. Ce test l'inscrit noir sur blanc, parce que rien ne le disait
et que la surprise se paie à l'exploitation.

Ce qu'il exige en plus : que l'exploitant **puisse agir**. Un journal disant
« StorageAuthError: WebDAV 403 » est exact et inexploitable ; il faut qu'il nomme la
cause — le partage est en lecture — et le geste — accorder l'écriture, ou renoncer à cet
espace.

Voir `docs/FRONTIERE-DE-CONFIANCE.md` pour ce que « lecture seule » impliquerait si l'on
voulait le prendre en charge, et D37 pour l'arbitrage resté ouvert.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from colaig.exceptions import StorageAuthError
from tests.fakes import FakeStorage


class StockagePartageEnLecture(FakeStorage):
    """Un partage en lecture seule : tout se lit, rien ne s'écrit.

    C'est exactement ce que rend un WebDAV Nextcloud pour un dossier partagé sans droit
    d'écriture — un 403 sur PUT et sur MKCOL.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.tentatives_ecriture: list[str] = []
        self.arret: asyncio.Event | None = None

    async def list_files(self, path: str, recursive: bool = False):
        """Le balayage de la racine ouvre un cycle — et le referme.

        Poser l'arrêt AVANT de lancer la boucle la ferait sortir sans rien faire : la
        garde `while not shutdown_event.is_set()` est évaluée avant le corps. Le poser
        depuis le premier balayage laisse passer exactement un cycle, sans dormir ni
        regarder l'horloge.
        """
        if self.arret is not None and path == "/":
            self.arret.set()
        return await super().list_files(path, recursive)

    def ajouter_dossier(self, chemin: str) -> None:
        """Déclare un répertoire, ce que `FakeStorage` ne sait pas faire.

        La doublure du dépôt ne produit que des fichiers : `list_files` n'a jamais
        d'entrée `is_directory=True`. Aucun test ne peut donc exercer le code qui
        parcourt des dossiers — et la découverte d'espaces en est entièrement faite.

        # TODO-NORMALE : porter `ajouter_dossier` dans `tests/fakes.py`. Il n'y est pas
        # mis ici pour ne pas modifier la doublure commune au détour d'un autre lot.
        """
        from colaig.models import StorageFile

        chemin = "/" + chemin.strip("/")
        self.metadata[chemin] = StorageFile(
            path=chemin, name=chemin.rsplit("/", 1)[-1], is_directory=True,
        )

    async def upload(self, path: str, content: bytes) -> None:
        self.tentatives_ecriture.append(path)
        raise StorageAuthError(f"WebDAV 403 Forbidden: {path}")

    async def mkdir(self, path: str) -> None:
        self.tentatives_ecriture.append(path)
        raise StorageAuthError(f"WebDAV 403 Forbidden: {path}")


async def _un_cycle(storage, resolver) -> None:
    """Fait tourner la boucle de découverte exactement un cycle.

    `interval=0` fait expirer immédiatement l'attente initiale ; l'événement d'arrêt est
    posé aussitôt après, de sorte que la boucle sorte au lieu de tourner. Aucune horloge
    murale n'est consultée — le test ne dépend pas de la charge de la machine.
    """
    from colaig.main import run_workspace_discovery_loop

    arret = asyncio.Event()
    storage.arret = arret
    await asyncio.wait_for(
        run_workspace_discovery_loop(storage, resolver, 0, arret), timeout=10
    )


class _ResolverMinimal:
    def __init__(self) -> None:
        self.workspaces: list = []

    async def register_workspace(self, ws) -> None:
        self.workspaces.append(ws)


@pytest.mark.asyncio
async def test_un_partage_en_lecture_seule_ne_devient_pas_un_espace(caplog):
    """Le comportement d'aujourd'hui, inscrit pour qu'il cesse d'être une surprise."""
    storage = StockagePartageEnLecture()
    storage.ajouter_dossier("/dossier-du-collegue")
    storage.add_file("/dossier-du-collegue/note.md", b"contenu")
    resolver = _ResolverMinimal()

    with caplog.at_level(logging.WARNING):
        await _un_cycle(storage, resolver)

    assert not resolver.workspaces, (
        "un dossier non inscriptible ne peut pas devenir un espace : sans dossier "
        "d'instance, ni index ni mémoire"
    )
    assert storage.tentatives_ecriture, "le scaffold doit avoir été tenté"


@pytest.mark.asyncio
async def test_l_exploitant_apprend_quoi_faire(caplog):
    """Un journal exact mais inexploitable ne sert personne.

    « StorageAuthError: WebDAV 403 » dit ce qui s'est passé, pas ce qu'il faut faire.
    Celui qui exploite doit lire la cause — le partage est en lecture — et le geste.
    """
    storage = StockagePartageEnLecture()
    storage.ajouter_dossier("/dossier-du-collegue")
    storage.add_file("/dossier-du-collegue/note.md", b"contenu")

    with caplog.at_level(logging.WARNING):
        await _un_cycle(storage, _ResolverMinimal())

    journal = " ".join(r.getMessage() for r in caplog.records).lower()
    assert "lecture" in journal, (
        "le journal doit nommer la cause : le partage n'accorde que la lecture"
    )
    assert "écriture" in journal or "ecriture" in journal, (
        "le journal doit nommer le geste : accorder l'écriture à Colaig sur ce partage"
    )
