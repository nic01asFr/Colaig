"""Vérification bornée de `MatrixMessaging.run()`, sans aucun effet sortant.

Pourquoi ce script existe, et pourquoi il est si prudent
---------------------------------------------------------
`run()` est le seul point du `MessagingProtocol` resté non vérifié contre un vrai
serveur. Deux obstacles l'ont bloqué, et il faut les distinguer :

**1. Un arbitrage.** `matrix.py::_on_invite` rejoint **automatiquement** toute invitation
reçue, et `run()` déclenche ce callback via `sync_forever`. Une invitation adressée au
compte bot est en attente depuis un autre ministère. Lancer `run()` la ferait accepter —
un effet sortant, sur un compte de production, dans l'espace d'une autre administration.

La consigne est explicite : **rien n'est accepté qui ne vienne de l'utilisateur ou de
l'agent**. Le callback est donc débranché avant tout démarrage, et le script vérifie
qu'il l'est. C'est la première assertion, avant tout appel réseau.

**2. Un environnement.** `python-olm` se compile contre libolm, indisponible sous
Windows. C'est ce que `_exiger_e2e()` signale désormais avec le paquet, la bibliothèque
et la plateforme en cause. Ce script tourne donc en conteneur Linux.

Ce qui est vérifié
------------------
Que la boucle démarre, charge l'état des salons, obtient un jeton de synchronisation et
survit — rien d'autre. Aucun callback de message n'est enregistré, donc rien ne peut
être émis : `_on_room_message` se termine par `for callback in self._message_callbacks`,
liste vide.

Ce qui n'est **pas** vérifié : l'auto-join lui-même, précisément parce qu'on le
débranche. Il reste couvert par les seuls tests à doublure.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from colaig.messaging.matrix import MatrixMessaging  # noqa: E402

DUREE = int(os.environ.get("COLAIG_VERIF_DUREE", "45"))


async def main() -> int:
    for cle in ("MATRIX_HOMESERVER", "MATRIX_USERNAME", "MATRIX_PASSWORD"):
        if not os.environ.get(cle):
            raise SystemExit(f"{cle} absent de l'environnement")

    store = Path(os.environ.get("COLAIG_VERIF_STORE", "/tmp/verif-matrix")) / "token.json"
    store.parent.mkdir(parents=True, exist_ok=True)

    messagerie = MatrixMessaging(
        os.environ["MATRIX_HOMESERVER"].rstrip("/"),
        os.environ["MATRIX_USERNAME"],
        os.environ["MATRIX_PASSWORD"],
        token_store=store,
    )
    await messagerie.connect()
    print("connecté")

    # DÉBRANCHEMENT DE L'AUTO-JOIN — avant tout, et vérifié.
    avant = len(messagerie._client.event_callbacks)
    messagerie._client.event_callbacks = [
        c for c in messagerie._client.event_callbacks
        if getattr(c, "func", None) != messagerie._on_invite
    ]
    apres = len(messagerie._client.event_callbacks)
    assert apres == avant - 1, f"auto-join NON débranché ({avant} → {apres}) — on s'arrête"
    assert messagerie._message_callbacks == [], "un callback message est enregistré"
    print(f"auto-join débranché ({avant} → {apres} callbacks), aucun callback message")

    tache = asyncio.create_task(messagerie.run())
    t0 = time.monotonic()
    await asyncio.sleep(DUREE)

    if tache.done():
        print("run() est SORTI :", repr(tache.exception()))
        vivante = False
    else:
        vivante = True
    salons = len(messagerie._client.rooms)
    lot = messagerie._client.next_batch

    tache.cancel()
    try:
        await tache
    except asyncio.CancelledError:
        pass

    print(f"boucle vivante après {time.monotonic() - t0:.0f} s : {vivante}")
    print(f"salons chargés   : {salons}")
    print(f"jeton de synchro : {'obtenu' if lot else 'absent'}")

    # `MessagingProtocol` ne déclare aucune fermeture — c'est le client nio qu'on
    # referme, et l'appareil qu'on révoque. Sur un compte de production, laisser
    # derrière soi un appareil de test qui reste autorisé n'est pas acceptable :
    # chaque exécution en créerait un de plus, tous porteurs de clés de déchiffrement.
    await messagerie._client.logout()
    await messagerie._client.close()
    print("déconnecté, appareil révoqué")
    return 0 if (vivante and salons > 0 and lot) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
