"""
Contrat — le quota s'applique à TOUS les fournisseurs LLM, pas au seul Albert.

STATUT: TESTE
VERSION: 2026-08-25 - v1.0
LOT: L2.6

Le défaut
---------
`docs/SECURITE.md` §8 annonce comme mitigation du déni de service et du coût : « quotas
journaliers par tenant (requêtes/tokens) ». Mesuré en D46, `check_quota` n'existait que
dans `integrations/albert.py` :

    albert.py         4 occurrences
    openai_client.py  0
    azure_client.py   0
    ollama_client.py  0

Or la cible de production est **SSPCloud, endpoint OpenAI-compatible** (`CLAUDE.md` §3),
donc `openai_client`. **Le quota ne s'appliquait pas là où il compte.**

Quatrième occurrence dans ce dépôt du motif « écrit et non branché », après
`sanitize_description`, `storage_readonly` et `TaskExecutor`.

Pourquoi un point de passage unique
-------------------------------------
Recopier le contrôle dans les quatre clients produirait quatre versions qui divergeront —
ce chantier a mesuré cinq fois ce que coûte une fonction dupliquée. `metrics/quota.py`
porte l'unique implémentation, et le dernier test de ce fichier refuse qu'un client LLM
existe sans y passer.
"""
from __future__ import annotations

import pytest

from colaig.exceptions import QuotaExceededError
from colaig.metrics.quota import enregistrer_usage, verifier_quota


class _TrackerRefusant:
    def check_quota(self, client_id):
        return False, "quota journalier dépassé (1000 requêtes)"

    def record_from_usage(self, client_id, usage):
        raise AssertionError("ne doit pas être appelé quand le quota refuse")


class _TrackerAcceptant:
    def __init__(self) -> None:
        self.enregistres = []

    def check_quota(self, client_id):
        return True, ""

    def record_from_usage(self, client_id, usage):
        self.enregistres.append((client_id, usage))


def test_un_quota_depasse_leve():
    with pytest.raises(QuotaExceededError) as leve:
        verifier_quota(_TrackerRefusant(), "client-a")
    assert "client-a" in str(leve.value), "le tenant doit être nommé"
    assert "1000" in str(leve.value), "le motif du refus doit remonter"


def test_sans_tracker_rien_ne_bloque():
    """Un déploiement sans suivi d'usage doit continuer de fonctionner.

    C'est une échappatoire, et elle est ASSUMÉE : contrairement aux quatre gardes
    recensées en D44, celle-ci ne protège pas un accès mais un coût. Un déploiement qui
    n'a pas configuré de quota n'a pas exprimé de limite à faire respecter.
    """
    verifier_quota(None, "client-a")


def test_l_usage_est_enregistre():
    tracker = _TrackerAcceptant()
    enregistrer_usage(tracker, "client-a", {"usage": {"total_tokens": 42}})
    assert tracker.enregistres == [("client-a", {"total_tokens": 42})]


def test_une_metrique_en_echec_ne_casse_jamais_l_appel():
    """Un compteur qui tombe ne doit pas faire tomber la réponse à l'utilisateur.

    L'inverse — laisser remonter — transformerait un incident de métrique en panne de
    service, ce qui est un mauvais échange.
    """
    class _TrackerCasse:
        def record_from_usage(self, client_id, usage):
            raise RuntimeError("disque plein")

    enregistrer_usage(_TrackerCasse(), "client-a", {"usage": {}})


def test_une_reponse_sans_usage_ne_casse_rien():
    tracker = _TrackerAcceptant()
    enregistrer_usage(tracker, "client-a", {})
    assert tracker.enregistres == [("client-a", None)]


# ── Le point de passage doit être unique ────────────────────────────────────


def test_tout_client_llm_passe_par_le_point_unique():
    """Régression : le quota doit valoir pour le fournisseur de PRODUCTION.

    Ce test échoue si un client LLM applique le quota à sa façon, ou pas du tout.
    Écrire un nouveau client force donc la question.
    """
    import pathlib

    from tests.conftest import code_seul

    racine = pathlib.Path(__file__).resolve().parent.parent / "colaig" / "integrations"
    clients = [
        racine / "albert.py",
        racine / "llm" / "openai_client.py",
        racine / "llm" / "azure_client.py",
        racine / "llm" / "ollama_client.py",
    ]
    fautifs = []
    for chemin in clients:
        source = code_seul(chemin.read_text(encoding="utf-8"))
        if "metrics.quota" not in source and "metrics import quota" not in source:
            fautifs.append(chemin.name)

    assert not fautifs, (
        "ces clients LLM n'appliquent pas le quota par `colaig/metrics/quota.py` — "
        f"la mitigation annoncée dans docs/SECURITE.md y est inerte : {fautifs}"
    )


def test_aucun_client_ne_garde_sa_propre_copie():
    """Une copie privée diverge, et c'est ce qui a produit le defaut d'origine."""
    import pathlib

    from tests.conftest import code_seul

    racine = pathlib.Path(__file__).resolve().parent.parent / "colaig" / "integrations"
    fautifs = []
    for chemin in list(racine.glob("*.py")) + list((racine / "llm").glob("*.py")):
        source = code_seul(chemin.read_text(encoding="utf-8"))
        if "def _check_quota" in source or "def _record_usage" in source:
            fautifs.append(chemin.name)

    assert not fautifs, (
        f"copies privées du contrôle de quota — les retirer : {fautifs}"
    )
