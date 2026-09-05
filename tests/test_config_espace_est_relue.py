"""
Un `config.yaml` modifié doit être repris sans redémarrer l'instance.

CE QUI A ÉTÉ OBSERVÉ, le 04/09/2026, sur `colaig-test`
-------------------------------------------------------
Le `system_prompt` de l'espace des marchés publics a été renseigné dans son
`config.yaml` — 2506 caractères imposant notamment de commencer un refus par
« Cette information ne figure pas dans les passages fournis ».

Deux campagnes de 135 questions plus tard, **zéro** réponse portait cette formule.
Et le journal du pod ne montrait **aucun** rafraîchissement de cache en 90 minutes.

La cause est dans `resolve()` :

    workspace = self._conversation_mapping.get(message.conversation_id)
    if workspace is None:                    # ← seulement si ABSENT
        await self._ensure_cache_fresh()

Le TTL n'est consulté que lorsque la conversation est **inconnue**. Dès qu'un salon
est mappé une fois, son `WorkspaceConfig` est servi depuis le cache indéfiniment :
le TTL n'est jamais atteint parce que plus personne ne le regarde.

CE QUE CELA EMPÊCHE
--------------------
Toute configuration d'espace devient inerte après le premier message : prompt
système, `max_results`, `garde_fou_provenance`, `format_citation`. Il faut
redémarrer le pod pour qu'un réglage soit pris en compte.

C'est le principe fondateur du projet qui est en cause — « un espace de stockage +
un dossier `.colaig` = une instance Colaig complète ». Le dossier ne fait autorité
que s'il est relu.
"""
from __future__ import annotations

import pytest
import yaml

from colaig.context.resolver import ContextResolver
from colaig.models import ConversationType, IncomingMessage, StorageFile

SALON = "!salon:test.local"


def _config(prompt: str, nom: str = "Espace") -> bytes:
    return yaml.safe_dump({
        "workspace_id": "espace", "name": nom, "system_prompt": prompt,
        "conversations": [SALON],
    }, allow_unicode=True).encode()


def _declarer_le_dossier(storage) -> None:
    """`list_workspaces` scanne la racine : sans entree de dossier, rien n'est vu."""
    storage.metadata["/espace/"] = StorageFile(
        path="/espace/", name="espace", is_directory=True)


def _message() -> IncomingMessage:
    return IncomingMessage(message_id="$m", conversation_id=SALON,
                           user_id="@u:test.local", body="une question",
                           conversation_type=ConversationType.CHANNEL)


@pytest.mark.asyncio
async def test_le_prompt_modifie_est_repris_apres_le_ttl(mock_storage, monkeypatch):
    """Le cas vécu : un prompt posé dans le config.yaml doit finir par agir."""
    mock_storage.add_file("/espace/.colaig/config.yaml", _config("PREMIER"))
    _declarer_le_dossier(mock_storage)
    resolver = ContextResolver(mock_storage, cache_ttl=60)

    ctx = await resolver.resolve(_message())
    assert "PREMIER" in ctx.system_prompt

    mock_storage.add_file("/espace/.colaig/config.yaml", _config("SECOND"))

    # Le TTL s'est écoulé — sans avancer l'horloge, le test passerait même sur le
    # code fautif, la première résolution ayant rempli le cache à l'instant.
    import time as _t
    depart = _t.monotonic()
    monkeypatch.setattr(_t, "monotonic", lambda: depart + 120)

    ctx = await resolver.resolve(_message())
    assert "SECOND" in ctx.system_prompt, (
        "un config.yaml modifie reste ignore : il faut redemarrer l'instance")


@pytest.mark.asyncio
async def test_avant_le_ttl_le_cache_sert_encore(mock_storage):
    """L'autre moitié : on ne relit pas le storage à chaque message.

    Sans cette borne, chaque question déclencherait un scan complet du stockage —
    la raison d'être du cache.
    """
    mock_storage.add_file("/espace/.colaig/config.yaml", _config("PREMIER"))
    _declarer_le_dossier(mock_storage)
    resolver = ContextResolver(mock_storage, cache_ttl=60)

    await resolver.resolve(_message())
    mock_storage.add_file("/espace/.colaig/config.yaml", _config("SECOND"))
    ctx = await resolver.resolve(_message())
    assert "PREMIER" in ctx.system_prompt
