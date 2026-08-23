"""
Contrat du lot L1.3b — images OCRisables, et fin des documents silencieusement absents.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L1.3b

Deux défauts constatés sur un corpus réel de 59 documents :

1. **Le repli OCR ne couvrait que les PDF.** La condition était
   `filename.lower().endswith(".pdf")`, et de toute façon `is_supported()` écartait les
   images en amont : une image scannée déposée dans un espace n'atteignait **jamais**
   le chemin OCR, alors même qu'un modèle était disponible et configuré.

2. **Tous les échecs étaient muets.** `logger.debug(...)` puis `return False`. Sur le
   corpus mesuré, **8 documents sur 59 — 29 % du poids** — n'étaient pas indexés, et
   rien ne l'indiquait. Ni journal exploitable, ni indication à l'utilisateur qui
   interroge l'espace.

Silencieusement absent est le pire état possible : l'assistant répond « je n'ai rien
trouvé », ou pire, répond à partir d'un document voisin.
"""
from __future__ import annotations

import pytest

from colaig.rag.chunker import Chunker
from colaig.rag.indexer import Indexer
from colaig.utils.text import (
    OCR_EXTENSIONS,
    SUPPORTED_EXTENSIONS,
    is_indexable,
    is_supported,
    needs_ocr,
)
from tests.fakes import FakeStorage

pytestmark = pytest.mark.asyncio


# ── Prédicats ───────────────────────────────────────────────────────────────


async def test_les_images_ne_sont_pas_supportees_nativement():
    """`is_supported()` garde son sens : extraction **native**.

    Deux appelants s'y fient pour décider s'ils obtiendront du texte —
    `mcp/server.py` et `rag/document_index.py`. L'élargir aux images leur ferait
    recevoir des chaînes vides.
    """
    for ext in OCR_EXTENSIONS:
        assert not is_supported(f"scan{ext}"), ext
        assert needs_ocr(f"scan{ext}"), ext


async def test_les_images_sont_indexables():
    """`is_indexable()` réunit les deux familles — c'est le prédicat de l'indexeur."""
    for ext in OCR_EXTENSIONS | SUPPORTED_EXTENSIONS:
        assert is_indexable(f"document{ext}"), ext
    assert not is_indexable("archive.zip")
    assert not is_indexable("tableur.xlsx")
    assert not is_indexable("SANS_EXTENSION")


async def test_la_casse_ne_change_rien():
    assert needs_ocr("SCAN.PNG")
    assert is_indexable("Photo.JPEG")


# ── Harnais minimal ─────────────────────────────────────────────────────────


class _EmbeddingsFactices:
    """L'indexeur appelle `embed_texts` — pas `embed_batch`.

    Se tromper de nom donnait un `AttributeError` au milieu de l'indexation, ce que
    la doublure ne pouvait pas signaler à l'avance. C'est précisément ce que le
    contrat `LLMClientProtocol` du lot L1.3 doit empêcher.
    """

    async def embed_texts(self, textes):
        return [[0.1] * 8 for _ in textes]

    async def embed_batch(self, textes, batch_size=32):
        return [[0.1] * 8 for _ in textes]

    async def embed(self, texte):
        return [0.1] * 8


class _StoreFactice:
    def __init__(self):
        self.ajoutes = []
        self.count = 0

    def add(self, embeddings, chunks):
        self.ajoutes.extend(chunks)
        self.count = len(self.ajoutes)

    def delete_by_source(self, source):
        pass


class _OCRFactice:
    """Client LLM réduit à son `ocr()`. `texte` vide simule un OCR sans résultat."""

    def __init__(self, texte: str = "", leve: bool = False):
        self.texte = texte
        self.leve = leve
        self.appels: list[str] = []

    async def ocr(self, content: bytes, filename: str) -> str:
        self.appels.append(filename)
        if self.leve:
            raise RuntimeError("modèle indisponible")
        return self.texte


def _indexeur(storage, client=None):
    return Indexer(
        storage=storage,
        chunker=Chunker(chunk_size=800, chunk_overlap=100),
        embeddings=_EmbeddingsFactices(),
        store=_StoreFactice(),
        albert_client=client,
    )


# ── Le chemin OCR atteint désormais les images ──────────────────────────────


async def test_une_image_declenche_l_ocr():
    """C'est le cœur du lot : avant, l'OCR n'était jamais appelé sur une image."""
    storage = FakeStorage()
    storage.add_file("/ws/schema.png", b"\x89PNG\r\n\x1a\n" + b"x" * 200, "image/png")
    ocr = _OCRFactice("Consigne de sécurité : porter le gilet haute visibilité "
                      "sur toute intervention en bord de voie circulée.")

    indexeur = _indexeur(storage, ocr)
    indexe = await indexeur.index_document("/ws/schema.png", etag="e1")

    assert ocr.appels == ["schema.png"], "l'OCR n'a pas été tenté sur l'image"
    assert indexe is True
    assert indexeur.documents_ignores == {}


async def test_une_image_est_parcourue_par_index_workspace():
    """Le filtrage en amont écartait l'image avant même `index_document`."""
    storage = FakeStorage()
    storage.add_file("/ws/note.md", b"# Titre\n\n" + b"contenu utile. " * 40, "text/markdown")
    storage.add_file("/ws/scan.jpg", b"\xff\xd8\xff" + b"y" * 200, "image/jpeg")
    ocr = _OCRFactice("Texte restitue par l'OCR, suffisamment long pour produire "
                      "au moins un chunk exploitable dans l'index documentaire.")

    indexeur = _indexeur(storage, ocr)
    n = await indexeur.index_workspace("/ws/")

    assert "scan.jpg" in ocr.appels, "l'image n'a pas atteint le chemin OCR"
    assert n == 2


async def test_un_format_hors_perimetre_reste_ecarte():
    """Élargir aux images ne doit pas tout laisser entrer."""
    storage = FakeStorage()
    storage.add_file("/ws/archive.zip", b"PK\x03\x04" + b"z" * 100, "application/zip")
    ocr = _OCRFactice("du texte")

    indexeur = _indexeur(storage, ocr)
    assert await indexeur.index_workspace("/ws/") == 0
    assert ocr.appels == [], "aucun OCR ne doit être tenté sur une archive"


# ── Plus rien n'est silencieux ──────────────────────────────────────────────


async def test_ocr_sans_resultat_est_signale():
    storage = FakeStorage()
    storage.add_file("/ws/illisible.png", b"\x89PNG" + b"x" * 100, "image/png")

    indexeur = _indexeur(storage, _OCRFactice(""))
    assert await indexeur.index_document("/ws/illisible.png") is False
    assert indexeur.documents_ignores == {"/ws/illisible.png": "OCR sans résultat"}


async def test_ocr_en_echec_est_signale():
    storage = FakeStorage()
    storage.add_file("/ws/scan.png", b"\x89PNG" + b"x" * 100, "image/png")

    indexeur = _indexeur(storage, _OCRFactice(leve=True))
    assert await indexeur.index_document("/ws/scan.png") is False
    assert indexeur.documents_ignores == {"/ws/scan.png": "OCR en échec"}


async def test_absence_de_client_ocr_est_signalee_explicitement():
    """Le motif doit dire **pourquoi**, pas seulement qu'il manque quelque chose.

    « aucun texte extrait » enverrait chercher le problème dans le document, alors
    qu'il est dans la configuration.
    """
    storage = FakeStorage()
    storage.add_file("/ws/scan.png", b"\x89PNG" + b"x" * 100, "image/png")

    indexeur = _indexeur(storage, client=None)
    assert await indexeur.index_document("/ws/scan.png") is False
    motif = indexeur.documents_ignores["/ws/scan.png"]
    assert "aucun client OCR configuré" in motif


async def test_le_signalement_remonte_en_warning(caplog):
    """Niveau `warning`, pas `debug` : au niveau de log courant, rien n'apparaissait."""
    import logging

    storage = FakeStorage()
    storage.add_file("/ws/scan.png", b"\x89PNG" + b"x" * 100, "image/png")

    with caplog.at_level(logging.WARNING, logger="colaig.rag.indexer"):
        await _indexeur(storage, client=None).index_document("/ws/scan.png")

    messages = [enregistrement.getMessage() for enregistrement in caplog.records]
    assert any("non indexé" in m and "/ws/scan.png" in m for m in messages), messages


async def test_get_status_expose_la_couverture():
    """Ce qui manque compte autant que ce qui est là."""
    storage = FakeStorage()
    storage.add_file("/ws/bon.md", b"# Titre\n\n" + b"du contenu utile. " * 40, "text/markdown")
    storage.add_file("/ws/muet.png", b"\x89PNG" + b"x" * 100, "image/png")

    indexeur = _indexeur(storage, client=None)
    await indexeur.index_workspace("/ws/")
    etat = indexeur.get_status()

    assert etat["ignored_count"] == 1
    assert "/ws/muet.png" in etat["ignored_documents"]


async def test_un_document_repare_sort_de_la_liste():
    """Sinon le signalement s'accumule et perd toute valeur."""
    storage = FakeStorage()
    storage.add_file("/ws/scan.png", b"\x89PNG" + b"x" * 100, "image/png")

    indexeur = _indexeur(storage, client=None)
    await indexeur.index_document("/ws/scan.png")
    assert "/ws/scan.png" in indexeur.documents_ignores

    # L'OCR devient disponible : le même document s'indexe.
    indexeur._albert_client = _OCRFactice(
        "Le texte devient enfin lisible, et il est assez long pour produire un chunk."
    )
    assert await indexeur.index_document("/ws/scan.png", etag="neuf") is True
    assert indexeur.documents_ignores == {}


async def test_documents_ignores_est_une_copie():
    """L'appelant ne doit pas pouvoir modifier l'état interne de l'indexeur."""
    storage = FakeStorage()
    storage.add_file("/ws/scan.png", b"\x89PNG" + b"x" * 100, "image/png")

    indexeur = _indexeur(storage, client=None)
    await indexeur.index_document("/ws/scan.png")
    copie = indexeur.documents_ignores
    copie.clear()
    assert indexeur.documents_ignores, "documents_ignores expose son dictionnaire interne"
