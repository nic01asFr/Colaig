"""
`process()` doit rendre la réponse, pas le premier message d'attente.

CE QUI A ÉTÉ OBSERVÉ, le 02/09/2026, en éprouvant le pipeline fraîchement activé
-------------------------------------------------------------------------------
Première question réelle posée à `POST /ask` sur l'espace des marchés publics :

    pipeline : phase2
    réponse  : *Analyse de votre demande...*

Le pipeline avait pourtant tout fait, et bien : analyse (confiance 0,90),
orchestration (4 étapes, 10 résultats, 3,2 s), synthèse (959 ms). La réponse
existait. C'est `process()` qui rendait le mauvais message.

    return captured[0] if captured else ""

En Phase 1, un seul message est envoyé : le premier EST la réponse, et le défaut
restait invisible. En Phase 2, le `ProgressReporter` envoie d'abord un accusé
d'avancement — « *Analyse de votre demande...* » — puis la réponse. `captured[0]`
est donc l'accusé.

Conséquence : l'endpoint de test et d'intégration devenait inutilisable
précisément pour le pipeline qu'il devait servir à éprouver. On aurait conclu que
le pipeline ne répond pas.
"""
from __future__ import annotations

from colaig.messaging.progress import _PHASE_MESSAGES, est_message_de_progression, reponse_finale

REPONSE = "L'allotissement est le principe, selon l'article L2113-10."


def test_les_quatre_accuses_sont_reconnus():
    """Ils sont quatre, et chacun peut arriver seul selon le chemin du pipeline."""
    for texte in _PHASE_MESSAGES.values():
        assert est_message_de_progression(texte), texte


def test_un_accuse_prive_de_markdown_reste_reconnu():
    """`report()` retire les `*` quand le canal ne fait pas de markdown.

    Comparer au template brut laisserait alors passer l'accusé pour une réponse.
    """
    assert est_message_de_progression("Analyse de votre demande...")


def test_une_vraie_reponse_n_est_pas_prise_pour_un_accuse():
    """L'autre moitié : un filtre trop large mangerait la réponse."""
    assert not est_message_de_progression(REPONSE)
    assert not est_message_de_progression("*Analyse de votre demande de dérogation*")


def test_la_reponse_est_rendue_malgre_les_accuses():
    """Le cas vécu : deux accusés, puis la réponse."""
    capture = ["*Analyse de votre demande...*", "*Rédaction de la réponse...*", REPONSE]
    assert reponse_finale(capture) == REPONSE


def test_un_seul_message_reste_la_reponse():
    """Phase 1 : rien ne doit changer pour le cœur, qui n'envoie qu'un message."""
    assert reponse_finale([REPONSE]) == REPONSE


def test_si_tout_est_accuse_on_rend_le_dernier_plutot_que_rien():
    """Un pipeline interrompu ne doit pas répondre le vide.

    Rendre `""` ferait croire à une absence de réponse ; rendre le dernier accusé
    dit au moins où le traitement s'est arrêté.
    """
    assert reponse_finale(["*Analyse de votre demande...*"]) == "*Analyse de votre demande...*"
    assert reponse_finale([]) == ""
