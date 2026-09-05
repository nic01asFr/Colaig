"""
Contrat — le secret de signature des sessions n'est jamais une constante publique.

STATUT: TESTE
VERSION: 2026-08-24 - v1.0
LOT: L2.1f

Les trois casquettes de `COLAIG_PLATFORM_API_KEY`
-------------------------------------------------
Une seule valeur porte trois roles, avec trois profils d'exposition differents :

1. **mot de passe** du tableau de bord — `password == key`, tape dans un formulaire ;
2. **jeton Bearer** des routes de provisionnement — envoye dans un en-tete HTTP ;
3. **secret de signature du cookie de session** — ne doit jamais circuler.

Cumuler 1 et 3 signifie que **qui connait le mot de passe peut forger un cookie**.

Ce que ce test ferme
--------------------
Quand la cle est absente, le secret de signature retombait sur la chaine litterale
`colaig-dev-secret-change-in-production`, **ecrite dans un depot public**. N'importe qui
pouvait donc fabriquer un cookie `colaig_session` portant `admin=1`.

Aujourd'hui cela ne change rien — sans cle, tout est deja ouvert (D44). Mais le jour ou
l'echappatoire `if not key: return True` sera fermee, cette constante rouvrirait seule
ce que l'on croirait avoir verrouille. Un secret public n'est pas un secret.

Le repli devient donc un secret **aleatoire par processus**. Consequence assumee : les
sessions ne survivent pas a un redemarrage lorsqu'aucune cle n'est configuree — ce qui
est sans portee dans un mode ou rien n'est garde.
"""
from __future__ import annotations

import pathlib

SOURCE = (pathlib.Path(__file__).resolve().parent.parent
          / "colaig" / "web" / "routes.py")


def test_aucune_constante_de_secret_dans_le_code():
    """Une chaine de repli ecrite en dur est publique par construction."""
    from tests.conftest import code_seul

    source = code_seul(open(SOURCE, encoding="utf-8").read())
    assert "colaig-dev-secret-change-in-production" not in source, (
        "le secret de repli est une constante publique : n'importe qui peut forger "
        "un cookie de session signe avec elle"
    )


def test_deux_processus_ne_partagent_pas_le_secret_de_repli(monkeypatch):
    """Sans cle, le secret doit etre tire au hasard — donc different a chaque montage.

    C'est la propriete qui compte : un secret imprevisible. La verifier par deux
    montages successifs la mesure au lieu de la supposer.
    """
    from colaig.web.routes import _session_secret_pour_test

    monkeypatch.delenv("COLAIG_PLATFORM_API_KEY", raising=False)
    assert _session_secret_pour_test() != _session_secret_pour_test()


def test_avec_une_cle_le_secret_reste_stable(monkeypatch):
    """Une cle configuree doit donner un secret stable, sinon les sessions sautent
    a chaque redemarrage la ou l'exploitant a justement fait le necessaire.
    """
    from colaig.web.routes import _session_secret_pour_test

    monkeypatch.setenv("COLAIG_PLATFORM_API_KEY", "cle-de-test")
    assert _session_secret_pour_test() == _session_secret_pour_test()
