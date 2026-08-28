"""
Contrat — un document déposé dans un salon arrive jusqu'à Colaig.

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L3.7

Le défaut
---------
Quatre rappels sont enregistrés auprès de `matrix-nio` : invitation, message texte,
audio clair, audio chiffré. **Aucun pour les fichiers.**

Un collègue qui dépose un PDF dans le salon ne produit donc rien — pas d'erreur, pas
de trace, rien. Colaig est aveugle aux documents dans le canal même où on lui en parle,
alors que le classement documentaire est sa raison d'être.

Ce qui existe déjà, et qu'il suffit de rejoindre
--------------------------------------------------
`ClassificationEngine` est complet et **branché** dans `document_index.py` : règles
YAML dans `.colaig/rules/`, `classify()` rendant un `virtual_path`. Le chaînon manquant
est la RÉCEPTION, pas le classement.

De même, `_download_audio` gère déjà le téléchargement `mxc://` **et** le déchiffrement
E2E. Rien dans son corps n'est propre à l'audio — seul son nom le laisse croire.

Pourquoi une pièce jointe n'exige pas de mention
--------------------------------------------------
Le texte en salon demande une mention (L3.2). Le fichier, non : **déposer un document
EST l'intention**. Personne n'écrit « @colaig » en glissant un PDF, et l'audio suit
déjà cette règle.

La contrepartie est explicite : Colaig ne RÉPOND pas à un fichier, il le **classe**.
Répondre à chaque dépôt inonderait un salon où l'on partage des documents entre
collègues.
"""
from __future__ import annotations

import pytest

from colaig.models import ConversationType


class _EvenementFichier:
    """Ce que `matrix-nio` délivre pour un fichier — non chiffré."""

    def __init__(self, nom: str = "marche-2026.pdf", mime: str = "application/pdf",
                 taille: int = 4096, expediteur: str = "@alice:tchap.gouv.fr") -> None:
        self.body = nom
        self.sender = expediteur
        self.event_id = "$fichier"
        self.server_timestamp = 10**13
        self.url = "mxc://tchap.gouv.fr/abc123"
        self.source = {"content": {"body": nom, "url": self.url,
                                   "info": {"mimetype": mime, "size": taille}}}


class _Salon:
    def __init__(self, room_id: str = "!salon:tchap.gouv.fr") -> None:
        self.room_id = room_id

    def user_name(self, uid):
        return "Alice"


@pytest.fixture
def messagerie(monkeypatch):
    from colaig.messaging.matrix import MatrixMessaging

    m = MatrixMessaging(homeserver="https://exemple.invalid",
                        username="@colaig:tchap.gouv.fr", password="x")
    m._start_time = 0.0
    m._client = object()

    async def _salon(_room_id):
        return ConversationType.PUBLIC

    monkeypatch.setattr(m, "_resolve_conversation_type", _salon)

    async def _telecharger(_event):
        return b"%PDF-1.4 contenu du marche"

    monkeypatch.setattr(m, "_download_audio", _telecharger)

    recus: list = []
    m.on_message(lambda msg: recus.append(msg) or _rien())
    m._recus = recus
    return m


async def _rien():
    return None


async def _injecter(m, evenement, salon=None):
    await m._on_room_file(salon or _Salon(), evenement)
    return m._recus


# ── La réception ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_un_fichier_depose_arrive_jusqu_au_handler(messagerie):
    """LE défaut : aujourd'hui, déposer un PDF ne produit rien du tout."""
    recus = await _injecter(messagerie, _EvenementFichier())
    assert len(recus) == 1, "un fichier déposé n'a produit aucun message"


@pytest.mark.asyncio
async def test_la_piece_jointe_porte_son_nom_son_type_et_son_contenu(messagerie):
    """Sans le nom ni le type, le classement n'a rien sur quoi s'appuyer."""
    recus = await _injecter(messagerie, _EvenementFichier("CCAP-lot3.pdf"))
    pj = recus[0].attachments[0]
    assert pj.filename == "CCAP-lot3.pdf"
    assert pj.content_type == "application/pdf"
    assert pj.content == b"%PDF-1.4 contenu du marche"
    assert pj.size == len(pj.content)


@pytest.mark.asyncio
async def test_une_piece_jointe_N_EXIGE_PAS_de_mention(messagerie):
    """Déposer un document EST l'intention.

    Personne n'écrit « @colaig » en glissant un PDF, et l'audio suit déjà cette règle.
    """
    recus = await _injecter(messagerie, _EvenementFichier())
    assert len(recus) == 1


@pytest.mark.asyncio
async def test_le_corps_du_message_reste_VIDE(messagerie):
    """Le nom du fichier n'est pas une question.

    Le mettre dans `body` ferait traiter « marche-2026.pdf » comme une requête par le
    pipeline de réponse — l'assistant chercherait dans le corpus ce que le fichier
    demande, alors que le fichier ne demande rien.
    """
    recus = await _injecter(messagerie, _EvenementFichier())
    assert recus[0].body == ""


# ── Ce qui doit être refusé ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ses_propres_fichiers_sont_ignores(messagerie):
    """Colaig produit des documents ; les réingérer ferait une boucle."""
    e = _EvenementFichier(expediteur="@colaig:tchap.gouv.fr")
    assert await _injecter(messagerie, e) == []


@pytest.mark.asyncio
async def test_un_fichier_trop_vieux_est_ignore(messagerie):
    """Au démarrage, le serveur rejoue l'historique.

    Sans ce garde, un redémarrage réingérerait tous les documents du salon.
    """
    e = _EvenementFichier()
    e.server_timestamp = 0
    messagerie._start_time = 10**12
    assert await _injecter(messagerie, e) == []


@pytest.mark.asyncio
async def test_un_fichier_DEMESURE_est_refuse(messagerie):
    """Le téléchargement se fait EN MÉMOIRE.

    Un fichier de plusieurs centaines de mégaoctets tuerait le processus, et un salon
    partagé en contient tôt ou tard un. Le refus est journalisé, pas silencieux.
    """
    from colaig.messaging.matrix import _MAX_PIECE_JOINTE_OCTETS

    e = _EvenementFichier(taille=_MAX_PIECE_JOINTE_OCTETS + 1)
    assert await _injecter(messagerie, e) == []


@pytest.mark.asyncio
async def test_un_telechargement_en_echec_ne_casse_pas_la_boucle(messagerie, monkeypatch):
    """Un fichier illisible ne doit pas arrêter la réception des suivants."""
    async def _echoue(_event):
        return None

    monkeypatch.setattr(messagerie, "_download_audio", _echoue)
    assert await _injecter(messagerie, _EvenementFichier()) == []


# ── Le branchement auprès de nio ────────────────────────────────────────────


def test_les_rappels_fichier_sont_enregistres():
    """Un traitement écrit et non branché ne traite rien.

    Douzième vérification explicite de ce motif dans ce dépôt — dont une commise
    trente minutes plus tôt sur `suivre_fil`, cinq minutes après l'avoir décrite.
    """
    import pathlib

    from tests.conftest import code_seul

    source = code_seul((pathlib.Path(__file__).resolve().parent.parent
                        / "colaig" / "messaging" / "matrix.py").read_text(encoding="utf-8"))
    assert "_on_room_file" in source
    for classe in ("RoomMessageFile", "RoomMessageImage"):
        assert classe in source, (
            f"le rappel doit être enregistré auprès de nio pour {classe}"
        )
