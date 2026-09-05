"""
Un espace decouvert pendant que Colaig tourne doit finir par etre indexe.

CE QUI A ETE OBSERVE, le 01/09/2026, sur l'instance colaig-test
----------------------------------------------------------------
Un espace de 108 documents a ete depose dans le bucket. Colaig l'a decouvert en
deux minutes :

    workspace decouvert (config existante): /colaig-mesure-marches-publics
    workspace enregistre en cache: colaig-mesure-marches-publics (1 conversations)

Puis plus rien. Quarante minutes apres, aucun `index.faiss`, et le journal du pod
ne portait AUCUNE autre ligne — ni indexation, ni erreur. L'espace etait connu,
visible, et definitivement muet : interroge, il aurait repondu qu'il ne trouve rien.

LA CAUSE
--------
`run_indexation_loop` sautait tout espace dont l'index est absent du storage :

    loaded = await ws_indexer.load_from_storage(ws.index_path)
    if not loaded:
        logger.debug("index absent du storage, skip (initial_indexation en cours ?)")
        continue

Le motif invoque `initial_indexation`. Mais la boucle **attend** `initial_done`
avant son premier cycle : quand elle atteint cette ligne, l'indexation initiale est
terminee par construction. L'hypothese du commentaire ne peut donc jamais etre vraie
la ou elle est invoquee.

Pour un espace present au demarrage, le skip etait sans consequence — l'indexation
initiale lui avait deja fait un index. Pour un espace apparu ENSUITE, il n'y a pas
d'initial_indexation a attendre : le skip etait definitif, et repete a chaque cycle.

Le message etait en `debug`, donc invisible au niveau INFO du deploiement. Une
capacite — l'auto-decouverte — qui decouvre puis n'aboutit a rien, sans que rien ne
le signale.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from colaig import main as colaig_main
from colaig.models import UpdateSummary, WorkspaceConfig


def _espace() -> WorkspaceConfig:
    return WorkspaceConfig(workspace_id="marches-publics", name="Marches publics",
                           storage_path="/marches-publics/", rag_enabled=True,
                           index_path="/marches-publics/.colaig/indexes/")


async def _un_cycle(monkeypatch, index_present: bool) -> MagicMock:
    """Fait tourner un cycle de la vraie boucle et rend l'indexer utilise."""
    indexer = MagicMock()
    indexer.load_from_storage = AsyncMock(return_value=index_present)
    indexer.check_updates = AsyncMock(
        return_value=UpdateSummary(count=108, changed_paths=["/marches-publics/a.md"]))
    indexer.save_to_storage = AsyncMock()

    monkeypatch.setattr(colaig_main, "Indexer", lambda *a, **k: indexer)
    for inutile in ("BehaviorIndexer", "SkillIndexer"):
        faux = MagicMock()
        faux.return_value.index_workspace = AsyncMock()
        monkeypatch.setattr(colaig_main, inutile, faux)

    resolver = MagicMock()
    resolver.workspaces = [_espace()]
    embedding = MagicMock()
    embedding.dimension = 8

    arret = asyncio.Event()
    initial = asyncio.Event()
    initial.set()

    tache = asyncio.create_task(colaig_main.run_indexation_loop(
        MagicMock(), MagicMock(), embedding, resolver, {}, {},
        3600, arret, initial_done=initial))
    for _ in range(200):                      # laisse un cycle se derouler
        await asyncio.sleep(0.005)
        if indexer.load_from_storage.await_count:
            break
    arret.set()
    tache.cancel()
    try:
        await tache
    except asyncio.CancelledError:
        pass
    return indexer


@pytest.mark.asyncio
async def test_un_espace_sans_index_est_indexe_et_non_saute(monkeypatch):
    """Le cas vecu : espace decouvert a chaud, donc sans index en storage.

    Il ne doit pas etre saute. `check_updates` est ce qui l'indexe reellement ;
    ne pas l'appeler, c'est laisser l'espace muet pour toujours.
    """
    indexer = await _un_cycle(monkeypatch, index_present=False)
    assert indexer.load_from_storage.await_count, "la boucle n'a pas atteint le workspace"
    assert indexer.check_updates.await_count, (
        "espace sans index saute : il ne sera jamais indexe, et rien ne le signale")


@pytest.mark.asyncio
async def test_un_espace_deja_indexe_reste_traite(monkeypatch):
    """L'elargissement ne doit pas se payer d'une regression sur le cas courant."""
    indexer = await _un_cycle(monkeypatch, index_present=True)
    assert indexer.check_updates.await_count
    assert indexer.save_to_storage.await_count, "108 documents changes doivent etre persistes"
