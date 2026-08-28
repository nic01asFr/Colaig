"""
Contrat — un document déposé est rangé, pas pris pour un message vocal.

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L3.7

La régression que ce fichier empêche
--------------------------------------
Le handler décide ainsi, depuis l'origine :

    if message.attachments and not message.body.strip():
        await self._transcribe_audio(message)
        if not message.body.strip():
            envoyer("Je n'arrive pas à traiter ce message vocal…")

Tant que Matrix ne délivrait **que** de l'audio, l'équivalence « pièce jointe + corps
vide = message vocal » était vraie. En ouvrant la réception aux fichiers (L3.7), elle
devient fausse : **déposer un PDF répondrait « Je n'arrive pas à traiter ce message
vocal »**.

C'est le mode de défaillance le plus déroutant possible — l'assistant nomme une chose
que l'utilisateur n'a pas faite. Le défaut ne serait pas venu du code d'origine, qui
était juste sous son hypothèse, mais de mon élargissement qui l'a rendue fausse.

Le critère du lot
------------------
« Une PJ est classée dans le bon dossier. » Le classement lui-même existe déjà —
`ClassificationEngine`, branché dans `document_index.py`. Ce qui manquait était la
réception, puis **l'aiguillage** : audio d'un côté, document de l'autre.
"""
from __future__ import annotations

import pytest

from colaig.models import Attachment, ConversationType, IncomingMessage


def _message(pieces: list[Attachment], corps: str = "") -> IncomingMessage:
    return IncomingMessage(
        user_id="@alice:tchap.gouv.fr",
        conversation_id="!salon:tchap.gouv.fr",
        body=corps,
        conversation_type=ConversationType.PUBLIC,
        message_id="$evt",
        display_name="Alice",
        platform="matrix",
        attachments=pieces,
    )


PDF = Attachment(filename="CCAP-lot3.pdf", content_type="application/pdf",
                 size=26, content=b"%PDF-1.4 contenu du marche")
VOCAL = Attachment(filename="voice.ogg", content_type="audio/ogg",
                   size=10, content=b"0123456789")


# ── L'aiguillage ────────────────────────────────────────────────────────────


def test_un_document_n_est_PAS_un_audio():
    """Le prédicat qui sépare les deux chemins.

    Sans lui, « pièce jointe + corps vide » restait synonyme de vocal, et un PDF
    déclenchait la réponse « Je n'arrive pas à traiter ce message vocal ».
    """
    from colaig.messaging.handlers import est_audio, est_document

    assert est_audio(VOCAL) and not est_document(VOCAL)
    assert est_document(PDF) and not est_audio(PDF)


@pytest.mark.parametrize("mime,doc", [
    ("application/pdf", True),
    ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", True),
    ("text/plain", True),
    ("text/markdown", True),
    ("image/png", True),
    ("audio/ogg", False),
    ("audio/mpeg", False),
    ("video/mp4", False),
])
def test_ce_qui_compte_comme_document(mime, doc):
    """Les images comptent : un plan photographié, un devis scanné, un panneau relevé
    sur le terrain sont des documents. La vidéo non — Colaig ne sait pas la lire, et
    l'accepter promettrait un traitement qui n'existe pas.
    """
    from colaig.messaging.handlers import est_document

    assert est_document(
        Attachment(filename="x", content_type=mime, content=b"x")) is doc


def test_un_type_INCONNU_n_est_pas_un_document():
    """Un type absent ou exotique ne doit pas ouvrir le chemin d'écriture.

    C'est le sens sûr : mieux vaut ne pas ranger un fichier qu'on ne sait pas lire que
    l'écrire dans l'espace sur une supposition. Le même raisonnement que pour les
    annotations MCP absentes (L2.4a), en sens inverse : là, l'absence signifiait
    destructif ; ici, elle signifie « on s'abstient ».
    """
    from colaig.messaging.handlers import est_document

    assert est_document(
        Attachment(filename="x", content_type="", content=b"x")) is False
    assert est_document(
        Attachment(filename="x", content_type="application/x-msdos", content=b"x")) is False


def test_une_extension_ne_suffit_pas_a_faire_un_document():
    """Le type MIME fait foi, pas le nom.

    Un fichier nommé `rapport.pdf` mais annoncé `video/mp4` n'est pas un PDF. Se fier
    au nom, c'est se fier à ce que l'expéditeur a bien voulu écrire.
    """
    from colaig.messaging.handlers import est_document

    assert est_document(
        Attachment(filename="rapport.pdf", content_type="video/mp4",
                   content=b"x")) is False


# ── Ce que le mélange produirait ────────────────────────────────────────────


def test_un_message_MIXTE_privilegie_l_audio():
    """Audio + document dans le même message : l'audio est une PAROLE, il porte une
    intention et attend une réponse. Le document attend un rangement.

    Traiter l'audio d'abord garde la conversation ; l'inverse laisserait une question
    sans réponse.
    """
    from colaig.messaging.handlers import est_audio

    m = _message([PDF, VOCAL])
    assert any(est_audio(p) for p in m.attachments)


def test_une_piece_sans_contenu_n_est_pas_traitable():
    """Le téléchargement peut échouer sans que la pièce disparaisse du message.

    Écrire un document vide dans l'espace serait pire que ne rien faire : il occuperait
    une place, serait indexé, et répondrait du vide à une question.
    """
    from colaig.messaging.handlers import est_document

    vide = Attachment(filename="x.pdf", content_type="application/pdf", content=None)
    assert est_document(vide) is False
