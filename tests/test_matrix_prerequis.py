"""
Contrat — le backend Matrix dit ce qui lui manque.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L1.2

`requirements.txt` déclare `matrix-nio[e2e]` depuis toujours. L'extra apporte
`python-olm`, qui se compile contre **libolm** ; sous Windows aucune roue n'est publiée
et l'installation échoue — **sans empêcher `matrix-nio` de s'installer**. On obtient un
environnement où la dépendance paraît satisfaite et où le chiffrement est absent.

Le défaut ne se manifestait qu'à la première connexion, sous la forme d'un
`ImportWarning` remonté de `nio/client/base_client.py`. Il ne nomme ni le paquet, ni la
bibliothèque système en cause, ni le fait que Tchap chiffre tous ses salons — donc que
couper le chiffrement n'est pas une option.

Constaté en tentant de lever le `skip` de `run()` : la vérification s'est arrêtée avant
d'atteindre le réseau, sur une erreur qui ne disait pas quoi faire.
"""
from __future__ import annotations

import builtins

import pytest

from colaig.exceptions import MessagingError
from colaig.messaging import matrix


def _sans_olm(monkeypatch):
    vrai_import = builtins.__import__

    def faux_import(nom, *args, **kwargs):
        if nom == "olm":
            raise ImportError("No module named 'olm'")
        return vrai_import(nom, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", faux_import)


def test_l_absence_d_olm_leve_une_erreur_colaig(monkeypatch):
    """Une `MessagingError`, pas un `ImportWarning` venu d'une dépendance tierce."""
    _sans_olm(monkeypatch)
    with pytest.raises(MessagingError):
        matrix._exiger_e2e()


def test_le_message_dit_quoi_installer(monkeypatch):
    """Un diagnostic qui n'indique pas l'action laisse l'exploitant devant un mur."""
    _sans_olm(monkeypatch)
    with pytest.raises(MessagingError) as excinfo:
        matrix._exiger_e2e()
    message = str(excinfo.value)
    assert "matrix-nio[e2e]" in message, "le message doit nommer l'extra à installer"
    assert "libolm" in message, "le message doit nommer la dépendance système en cause"
    assert "Windows" in message, "le message doit nommer la plateforme où ça échoue"


def test_le_message_explique_pourquoi_c_est_non_negociable(monkeypatch):
    """Sans cette phrase, quelqu'un coupera `encryption_enabled` pour avancer.

    Sur Tchap, tous les salons sont chiffrés : le client démarrerait et ne lirait
    aucun message — une panne bien plus coûteuse à diagnostiquer que l'erreur d'origine.
    """
    _sans_olm(monkeypatch)
    with pytest.raises(MessagingError) as excinfo:
        matrix._exiger_e2e()
    assert "chiffre" in str(excinfo.value).lower()


def test_le_controle_sait_se_taire():
    """Un garde-fou dont on n'a pas vu le vert ne prouve rien non plus.

    Là où olm est installé — la cible de déploiement — la vérification doit être
    transparente. `skip` sinon : ce test ne vaut que là où la condition est réunie.
    """
    pytest.importorskip("olm", reason="olm absent : rien à vérifier côté passant")
    matrix._exiger_e2e()
