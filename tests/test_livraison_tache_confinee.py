"""
Contrat — une tâche de fond ne livre pas son résultat dans `.colaig/`.

STATUT: TESTE
VERSION: 2026-08-24 - v1.0
LOT: L2.1b

Le raisonnement
---------------
Le modèle de confiance de Colaig repose sur le **partage de stockage** : celui qui
administre l'espace décide qui peut y écrire, et écrire dans `.colaig/prompts/` revient
à administrer l'agent. C'est assumé — un espace configure son assistant.

Ce raisonnement suppose que **Colaig n'écrit pas à la place de l'utilisateur**. Or les
tâches de fond le font : `delivery_type="document"` fait écrire le résultat à un chemin
que la tâche désigne, **avec les identifiants de service de Colaig**.

Ce chemin n'était validé nulle part — ni à la création (`task_tools.py`), ni à la
livraison (`task_scheduler.py`). Un utilisateur autorisé à créer une tâche, ce qui est
un usage ordinaire, pouvait donc désigner `.colaig/prompts/synthesiser.md` : au tour
suivant, la réponse produite par le modèle **devenait le prompt système de l'agent**.

Cela contourne entièrement le partage de stockage, puisque l'écrivain n'est pas
l'utilisateur mais Colaig. Les droits du demandeur sur `.colaig/` ne sont jamais
consultés — ils ne peuvent pas l'être, `StorageProtocol` n'ayant aucune notion de droits.

Deux barrières, et pourquoi les deux
-------------------------------------
**À la création** : refus immédiat, avec un message qui dit pourquoi.

**À la livraison** : dernière ligne. Les tâches déjà enregistrées avant ce correctif
portent un `delivery_target` non validé, et le fichier `.colaig/tasks/{id}.json` est
lui-même modifiable par qui écrit sur l'espace. Valider seulement à la création
laisserait la porte ouverte à qui édite la tâche après coup.
"""
from __future__ import annotations

import pytest

from colaig.exceptions import StorageError
from colaig.security.path_validator import validate_storage_path

CHEMINS_INTERDITS = [
    "/espace/.colaig/prompts/synthesiser.md",
    "/espace/.colaig/prompts/orchestrator.md",
    "/espace/.colaig/config.yaml",
    "/espace/.colaig/skills/procedure.md",
    "/espace/.colaig-ignore",
]

CHEMINS_LEGITIMES = [
    "/espace/rapports/hebdo.md",
    "/espace/documents/synthese.txt",
]


@pytest.mark.parametrize("chemin", CHEMINS_INTERDITS)
def test_le_validateur_refuse_les_chemins_d_instance(chemin):
    """La brique de base : le validateur existait déjà et sait refuser."""
    with pytest.raises(StorageError):
        validate_storage_path(chemin, allow_dotcolaig=False, context="test")


@pytest.mark.parametrize("chemin", CHEMINS_LEGITIMES)
def test_le_validateur_laisse_passer_un_document_ordinaire(chemin):
    """Un garde qui refuse tout ne protège rien : il se fait désactiver."""
    assert validate_storage_path(chemin, allow_dotcolaig=False, context="test")


@pytest.mark.asyncio
@pytest.mark.parametrize("chemin", CHEMINS_INTERDITS)
async def test_la_livraison_refuse_d_ecrire_dans_l_instance(chemin, fake_storage):
    """La barrière qui compte : elle tient même pour une tâche déjà enregistrée.

    C'est ici que passait l'escalade. Le planificateur écrivait `delivery_target`
    verbatim, avec les identifiants de service — les droits du demandeur n'étaient
    jamais consultés, et ne pouvaient pas l'être.
    """
    from colaig.agents.task_scheduler import _deliver_result
    from colaig.agents.tasks import TaskDefinition

    tache = TaskDefinition(
        task_id="t-1",
        user_id="@quelqu-un:exemple.fr",
        source_conversation_id="conv-1",
        workspace_path="/espace/",
        name="rapport",
        query="résume",
        schedule_type="once",
        schedule_value="",
        delivery_type="document",
        delivery_target=chemin,
    )

    await _deliver_result(tache, "texte produit par le modèle", storage=fake_storage)

    ecrits = [a for a in fake_storage.appels if a[0] == "upload"]
    assert not ecrits, (
        f"le résultat a été écrit dans un chemin d'instance : {ecrits}"
    )


@pytest.mark.asyncio
async def test_la_livraison_ordinaire_fonctionne_toujours(fake_storage):
    """Le correctif ne doit pas casser l'usage légitime.

    Sans ce test, la façon la plus simple de faire passer les précédents serait de ne
    plus rien écrire du tout.
    """
    from colaig.agents.task_scheduler import _deliver_result
    from colaig.agents.tasks import TaskDefinition

    tache = TaskDefinition(
        task_id="t-2",
        user_id="@quelqu-un:exemple.fr",
        source_conversation_id="conv-1",
        workspace_path="/espace/",
        name="rapport",
        query="résume",
        schedule_type="once",
        schedule_value="",
        delivery_type="document",
        delivery_target="/espace/rapports/hebdo.md",
    )

    await _deliver_result(tache, "texte produit par le modèle", storage=fake_storage)

    ecrits = [a for a in fake_storage.appels if a[0] == "upload"]
    assert ecrits, "la livraison légitime doit continuer d'écrire"


@pytest.mark.asyncio
@pytest.mark.parametrize("chemin", CHEMINS_INTERDITS)
async def test_create_document_refuse_les_chemins_d_instance(chemin, fake_storage):
    """Le meme confinement, sur le chemin ou c'est LE MODELE qui choisit la cible.

    `create_document` est un outil de la boucle agentique : le chemin sort du modele,
    dont les entrees comprennent les documents de l'espace. Une consigne deposee dans un
    document pouvait donc faire ecrire l'agent dans son propre `.colaig/prompts/` --
    la chaine complete, de l'injection a la persistance.

    Pire que la livraison de tache, qui suppose au moins un utilisateur authentifie.
    """
    from colaig.agents.tools.task_tools import create_document_handler

    handler = create_document_handler(fake_storage)
    rendu = await handler(content="contenu injecte", path=chemin)

    ecrits = [a for a in fake_storage.appels if a[0] == "upload"]
    assert not ecrits, f"l'agent a ecrit dans un chemin d'instance : {ecrits}"
    assert '"success": false' in rendu.lower(), (
        "le refus doit etre annonce au modele, pas silencieux : sinon il reessaie"
    )


@pytest.mark.asyncio
async def test_create_document_ecrit_toujours_un_document_ordinaire(fake_storage):
    from colaig.agents.tools.task_tools import create_document_handler

    handler = create_document_handler(fake_storage)
    await handler(content="rapport", path="/espace/rapports/note.md")
    assert [a for a in fake_storage.appels if a[0] == "upload"]
