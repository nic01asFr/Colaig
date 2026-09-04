"""
La stratégie de découpage doit pouvoir être choisie — elle décide de la couverture.

CE QUI A ÉTÉ MESURÉ SUR LE SERVICE, les 03 et 04/09/2026
---------------------------------------------------------
Quatre montages, causes éliminées une à une :

    pod  fenêtre k=5  sans prompt    refus  5/22    cite l'attendu 53/113
    pod  fenêtre k=5  avec prompt    refus 20/22    cite l'attendu 52/113
    local article k=5 avec prompt    refus 22/22    cite l'attendu 97/113
    local article k=10 avec prompt   refus 22/22    cite l'attendu 97/113

Le prompt explique tout le refus, et rien de la couverture. Le `k` n'explique rien
— 97/113 à k=5 comme à k=10. La mémoire de conversation non plus — 53 isolé contre
61 en fil continu. **Reste le découpage : 45 points de couverture.**

POURQUOI CE RÉGLAGE DORMAIT
----------------------------
`Chunker(strategie="auto")` est écrit et testé depuis le 02/09. Il a été laissé en
sommeil sur la foi d'une mesure qui concluait « +4 points seulement » — mais celle-ci
comptait les *passages porteurs d'une identité d'article* (94 % contre 98 %), pas la
capacité à **retrouver le bon article**. Le mauvais indicateur a fait écarter la
bonne piste.

`main.py` construisait `Chunker(chunk_size, chunk_overlap)` sans passer la stratégie :
le paramètre existait, personne ne le renseignait.
"""
from __future__ import annotations

import pytest

from colaig.config import load_config
from colaig.rag.chunker import Chunker


def test_le_defaut_reste_la_fenetre(monkeypatch):
    """Changer le découpage change l'index : personne ne doit le subir sans le vouloir.

    Réindexer un espace est coûteux et déplace toute mesure en cours. Le défaut ne
    bouge donc pas ; c'est la déclaration qui décide.
    """
    monkeypatch.delenv("COLAIG_CHUNK_STRATEGIE", raising=False)
    assert load_config().chunk_strategie == "fenetre"
    assert Chunker(800, 100)._strategie == "fenetre"


@pytest.mark.parametrize("valeur", ["auto", "article", "fenetre"])
def test_les_trois_strategies_sont_acceptees(monkeypatch, valeur):
    monkeypatch.setenv("COLAIG_CHUNK_STRATEGIE", valeur)
    assert load_config().chunk_strategie == valeur


def test_une_strategie_inconnue_retombe_sur_le_defaut(monkeypatch):
    """La valeur vient d'un environnement ou d'un fichier : elle n'est pas sûre.

    Une faute de frappe ne doit pas produire un découpage indéfini — `chunk_document`
    ne connaît que trois stratégies, et un nom inconnu y ferait silencieusement
    retomber sur la fenêtre sans que personne le sache. Autant le dire ici.
    """
    monkeypatch.setenv("COLAIG_CHUNK_STRATEGIE", "par-paragraphe")
    assert load_config().chunk_strategie == "fenetre"
