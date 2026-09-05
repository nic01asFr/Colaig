"""
Colaig — l'historique ne doit pas être détruit par sa propre sauvegarde.

CE QUI A MIS LA PUCE À L'OREILLE, LE 30/08/2026
------------------------------------------------
En écrivant le relevé des retours, le taux se calculait sur **6 réponses** pour un
salon qui en comptait bien plus. Le dénominateur était faux — et il l'était pour une
raison qui n'a rien à voir avec le comptage.

LE MÉCANISME
--------------
Trois pièces, chacune correcte isolément :

1. `load_conversation_history()` rend `messages[-10:]` — une **fenêtre** ;
2. `build_context()` la retronque à `DEFAULT_HISTORY_LENGTH`, soit 10 ;
3. `_save_history()` sauvegarde `list(context.conversation_history)` + le tour courant,
   et `save_conversation_history()` **écrase** le fichier avec ce qu'on lui donne.

Le fichier est donc réécrit à chaque tour avec la fenêtre, jamais avec l'historique. Il
ne dépasse jamais une douzaine de messages. **La sauvegarde détruit ce qu'elle croit
conserver.**

ET UN RÉGLAGE QUI NE FAIT RIEN
--------------------------------
`COLAIG_CONVERSATION_MEMORY_MAX_STORED` vaut 100 et n'a aucun effet sur ce chemin :
la borne réelle est la fenêtre de lecture, dix fois plus petite. C'est la dix-septième
« capacité déclarée qui ne fait rien » relevée dans ce dépôt.

LA PROPRIÉTÉ FIGÉE ICI
------------------------
**Ce qu'on donne au modèle et ce qu'on garde sur le disque sont deux choses.** Le modèle
reçoit une fenêtre — c'est un budget de contexte. Le disque garde l'historique — c'est
une trace. La fenêtre ne doit jamais devenir la borne du disque.
"""

from __future__ import annotations

import json

import pytest

from colaig.context.layers import (
    load_conversation_history,
    save_conversation_history,
)


class _Storage:
    def __init__(self):
        self.fichiers: dict[str, bytes] = {}

    async def upload(self, chemin: str, contenu: bytes) -> None:
        self.fichiers[chemin] = contenu

    async def download(self, chemin: str) -> bytes:
        return self.fichiers[chemin]

    async def mkdir(self, chemin: str) -> None:
        return None


def _tours(n: int) -> list[dict]:
    messages = []
    for i in range(n):
        messages.append({"role": "user", "content": f"question {i}"})
        messages.append({"role": "assistant", "content": f"reponse {i}"})
    return messages


@pytest.mark.asyncio
async def test_la_lecture_rend_une_fenetre_et_c_est_voulu():
    """Le modèle reçoit un budget de contexte, pas toute la conversation."""
    s = _Storage()
    await save_conversation_history(s, "/espace/", "!salon", _tours(30))

    fenetre = await load_conversation_history(s, "/espace/", "!salon")

    assert len(fenetre) == 10, "la fenêtre de lecture doit rester bornée"


@pytest.mark.asyncio
async def test_le_disque_garde_plus_que_la_fenetre():
    """LE défaut. Ce qui est relu et réécrit ne doit pas raboter la trace."""
    s = _Storage()
    await save_conversation_history(s, "/espace/", "!salon", _tours(30))

    # Le cycle réel : on relit une fenêtre, on ajoute un tour, on sauvegarde.
    fenetre = await load_conversation_history(s, "/espace/", "!salon")
    fenetre.append({"role": "user", "content": "question 30"})
    fenetre.append({"role": "assistant", "content": "reponse 30"})
    await save_conversation_history(s, "/espace/", "!salon", fenetre)

    sur_disque = json.loads(next(iter(s.fichiers.values())))

    assert len(sur_disque) > 12, (
        f"la sauvegarde a rabote l'historique a {len(sur_disque)} messages : "
        "elle detruit ce qu'elle croit conserver"
    )


@pytest.mark.asyncio
async def test_le_plus_ancien_tour_survit_a_vingt_cycles():
    """La propriété qui compte, éprouvée sur la durée d'une vraie conversation."""
    s = _Storage()
    await save_conversation_history(s, "/espace/", "!salon",
                                    [{"role": "user", "content": "LE PREMIER"},
                                     {"role": "assistant", "content": "reponse 0"}])

    for i in range(1, 21):
        fenetre = await load_conversation_history(s, "/espace/", "!salon")
        fenetre.append({"role": "user", "content": f"question {i}"})
        fenetre.append({"role": "assistant", "content": f"reponse {i}"})
        await save_conversation_history(s, "/espace/", "!salon", fenetre)

    sur_disque = json.loads(next(iter(s.fichiers.values())))
    contenus = [m["content"] for m in sur_disque]

    assert "LE PREMIER" in contenus, (
        f"le premier tour a ete perdu apres 20 cycles ; il reste {len(sur_disque)} "
        "messages sur 42"
    )


@pytest.mark.asyncio
async def test_la_trace_reste_bornee():
    """LA borne. Une conversation infinie ne doit pas produire un fichier infini.

    La borne est `conversation_memory_max_stored` (100 par defaut) — dix fois la
    fenetre de lecture, et c'est ce rapport qui fait la difference entre une memoire
    et un tampon.
    """
    s = _Storage()
    await save_conversation_history(s, "/espace/", "!salon", _tours(400))

    sur_disque = json.loads(next(iter(s.fichiers.values())))

    assert len(sur_disque) <= 100, (
        f"{len(sur_disque)} messages conserves : la trace n'est pas bornee"
    )
