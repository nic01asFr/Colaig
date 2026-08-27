"""
Contrat — les secrets sont masqués dans les logs de TOUS les modules.

STATUT: TESTE
VERSION: 2026-08-25 - v1.0
LOT: L2.6

Le défaut, mesuré
------------------
`utils/logging.py` posait le filtre ainsi :

    logging.getLogger().addFilter(SecretsMaskingFilter())

Un filtre attaché à un **Logger** ne s'applique qu'aux enregistrements émis
**directement sur ce logger**. Les enregistrements **propagés** depuis ses enfants
atteignent les *handlers* de la racine, mais **pas ses filtres**.

Or toute l'application journalise depuis des loggers nommés — `colaig.rag.generator`,
`colaig.messaging.matrix`… **Aucun n'était masqué.** Vérifié le 25/08/2026 :

    depuis un logger enfant : token=abcdef0123456789     ← en clair
    depuis la racine        : token=***

Le filtre existait, était installé, et ne protégeait rien.

C'est la variante la plus retorse du motif « écrit et non branché » que ce chantier a
trouvé cinq fois : ici, c'était **branché au mauvais endroit**. Une couverture de 43 %
sur ce module signalait déjà que personne ne l'avait exercé.

Le correctif
-------------
Le filtre s'attache aux **handlers**, qui voient les enregistrements propagés.
"""
from __future__ import annotations

import io
import logging

import pytest

from colaig.security.secrets_filter import SecretsMaskingFilter, mask_secrets


@pytest.fixture
def journal():
    """Un handler de capture, monté comme le fait `setup_logging`."""
    flux = io.StringIO()
    handler = logging.StreamHandler(flux)
    racine = logging.getLogger()
    anciens, ancien_niveau = racine.handlers, racine.level
    anciens_filtres = list(racine.filters)
    racine.handlers = [handler]
    racine.setLevel(logging.INFO)
    racine.filters = []
    yield flux, handler
    racine.handlers, racine.level = anciens, ancien_niveau
    racine.filters = anciens_filtres


# ── Le défaut lui-même ──────────────────────────────────────────────────────


def test_un_secret_journalise_par_un_module_est_masque(journal):
    """LE défaut. C'est de là que viennent tous les logs de l'application."""
    from colaig.utils.logging import installer_masquage_secrets

    flux, _ = journal
    installer_masquage_secrets()

    logging.getLogger("colaig.rag.generator").info("token=abcdef0123456789")

    sortie = flux.getvalue()
    assert "abcdef0123456789" not in sortie, (
        "un secret journalisé par un module est sorti EN CLAIR — le filtre est posé "
        "au mauvais endroit"
    )
    assert "***" in sortie


def test_les_arguments_de_formatage_sont_masques_aussi(journal):
    """`logger.info("cle %s", secret)` est la forme la plus courante."""
    from colaig.utils.logging import installer_masquage_secrets

    flux, _ = journal
    installer_masquage_secrets()

    logging.getLogger("colaig.messaging.matrix").info(
        "connexion avec %s", "password=motdepasse123",
    )
    assert "motdepasse123" not in flux.getvalue()


def test_installer_deux_fois_ne_double_pas_le_filtre(journal):
    """`setup_logging` peut être appelé plusieurs fois — en test, au rechargement.

    Deux exemplaires masqueraient deux fois, ce qui est inoffensif, mais s'accumuleraient
    sans fin. Un mécanisme qui se réinstalle doit rester idempotent.
    """
    from colaig.utils.logging import installer_masquage_secrets

    _, handler = journal
    installer_masquage_secrets()
    installer_masquage_secrets()

    masquants = [f for f in handler.filters if isinstance(f, SecretsMaskingFilter)]
    assert len(masquants) == 1, f"{len(masquants)} filtres installés"


# ── Ce que le filtre reconnaît ──────────────────────────────────────────────


@pytest.mark.parametrize("texte,secret", [
    ("api_key=sk_abcdefgh1234", "sk_abcdefgh1234"),
    ("API-KEY: sk_abcdefgh1234", "sk_abcdefgh1234"),
    ("password = motdepasse123", "motdepasse123"),
    ("Authorization: Bearer eyJhbGciOiJIUzI1", "eyJhbGciOiJIUzI1"),
    ("jeton colaig_espace_0123456789abcdef", "colaig_espace_0123456789abcdef"),
    ("cle ark_abcdefghij123", "ark_abcdefghij123"),
    ("https://user:motdepasse@nextcloud.exemple.fr/dav", "motdepasse"),
    ("MATRIX_PASSWORD=quelquechose", "quelquechose"),
])
def test_les_formes_connues_sont_masquees(texte, secret):
    assert secret not in mask_secrets(texte)


def test_un_texte_ordinaire_traverse_intact():
    """Un filtre qui mange le texte utile se fait retirer.

    C'est le pendant de la règle du balisage : une garde trop zélée est désactivée, et
    ne protège alors plus rien du tout.
    """
    ordinaire = "Article L2113-10 : les marchés sont passés en lots séparés."
    assert mask_secrets(ordinaire) == ordinaire


# ── Les limites, écrites plutôt que découvertes ─────────────────────────────


def test_une_trace_d_exception_n_est_PAS_masquee(journal):
    """Limite connue, épinglée.

    `logging.Filter` agit sur `record.msg` et `record.args`. Une trace remontée par
    `exc_info` est formatée par le handler APRÈS le filtre : un secret figurant dans le
    message d'une exception sort en clair.

    Ce test ne réclame pas de correctif — il rend la limite visible, pour qu'on ne
    croie pas le masquage plus complet qu'il n'est. Le corriger demanderait un
    `logging.Formatter`, ce qui est un autre lot.
    """
    from colaig.utils.logging import installer_masquage_secrets

    flux, _ = journal
    installer_masquage_secrets()

    try:
        raise ValueError("echec avec token=abcdef0123456789")
    except ValueError:
        logging.getLogger("colaig.rag.generator").exception("appel en echec")

    assert "abcdef0123456789" in flux.getvalue(), (
        "si ce test échoue, c'est que le masquage couvre désormais les traces — "
        "bonne nouvelle, mettre à jour cette docstring"
    )
