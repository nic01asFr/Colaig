"""
Harnais de test — doublures déterministes des trois Protocols d'I/O.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L0.4

`FakeStorage`, `FakeMessaging` et `FakeLLM` implémentent respectivement
`StorageProtocol`, `MessagingProtocol` et `LLMClientProtocol`, entièrement en mémoire
et **sans aucune source de non-déterminisme** : pas d'horloge murale, pas de hasard non
semé, pas de hachage randomisé.

Pourquoi le déterminisme n'est pas un luxe
------------------------------------------
La version précédente calculait l'etag ainsi :

    etag = f'"{hash(content)}"'

`hash()` sur des `bytes` est **randomisé par processus** (PYTHONHASHSEED). Deux
exécutions de la même suite produisaient donc des etags différents — mesuré :

    exécution 1 : 2598434101455927999
    exécution 2 : -123023570338129182

Or l'indexation incrémentale de Colaig repose entièrement sur la comparaison d'etags
(`.colaig/indexes/etags.json`). Une doublure dont les etags bougent d'un run à l'autre
ne peut pas servir à tester ce mécanisme, et fabrique des tests intermittents dont on
finit par accuser « la CI ».

Ici l'etag est le SHA-256 du contenu : stable entre processus, et **identique pour un
contenu identique** — ce qui est le comportement des vrais backends, et donc ce qu'on
veut éprouver.

Compatibilité
-------------
`MockStorage`, `MockWebDAVClient` et `MockAlbertClient` restent disponibles comme alias.
Les 78 fichiers de tests existants continuent de fonctionner sans modification, et
héritent au passage du déterminisme.
"""
from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from colaig.exceptions import StorageFileNotFoundError
from colaig.models import IncomingMessage, StorageFile

# Instant de référence fixe. Aucune doublure ne lit l'horloge : deux exécutions de la
# suite doivent produire exactement les mêmes objets.
INSTANT_ZERO = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)


def etag_deterministe(contenu: bytes) -> str:
    """Etag stable entre processus, dérivé du seul contenu.

    Ne jamais revenir à `hash()` : son résultat est randomisé par processus.
    """
    return f'"{hashlib.sha256(contenu).hexdigest()[:16]}"'


class FakeStorage:
    """`StorageProtocol` en mémoire, déterministe.

    Le compteur `_tick` donne un `last_modified` croissant sans lire l'horloge :
    l'ordre des écritures est reproductible, les dates aussi.
    """

    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.metadata: dict[str, StorageFile] = {}
        self._tick = 0
        # Journal des appels — permet d'assertionner les I/O sans mock.
        self.appels: list[tuple[str, str]] = []

    # ── Fabrique ────────────────────────────────────────────────────────────

    def add_file(self, path: str, content: bytes, content_type: str = "text/plain") -> None:
        """Dépose un fichier sans passer par l'API async (montage de fixture)."""
        self._tick += 1
        self.files[path] = content
        self.metadata[path] = StorageFile(
            path=path,
            name=path.split("/")[-1],
            size=len(content),
            etag=etag_deterministe(content),
            last_modified=INSTANT_ZERO + timedelta(seconds=self._tick),
            content_type=content_type,
        )

    # ── StorageProtocol ─────────────────────────────────────────────────────

    async def list_files(self, path: str, recursive: bool = False) -> list[StorageFile]:
        self.appels.append(("list_files", path))
        prefixe = path.rstrip("/") + "/"
        resultats = []
        for p, m in self.metadata.items():
            if not p.startswith(prefixe):
                continue
            if not recursive:
                relatif = p[len(prefixe):].rstrip("/")
                if "/" in relatif:
                    continue
            resultats.append(m)
        # Ordre stable : un dict Python conserve l'ordre d'insertion, mais un test ne
        # doit pas dépendre de l'ordre d'écriture des fixtures.
        return sorted(resultats, key=lambda m: m.path)

    async def download(self, path: str) -> bytes:
        self.appels.append(("download", path))
        if path not in self.files:
            # `StorageFileNotFoundError`, comme les **sept** implémentations réelles —
            # et non le `FileNotFoundError` natif, qui n'en est pas un parent
            # (`issubclass(...)` vaut False). Une doublure qui lève une autre exception
            # que la vraie laisse passer en test un `except` qui ne se déclenchera pas
            # en production. La trace de ce piège est encore visible dans
            # `agents/tasks.py`, qui attrape les deux « au cas où ».
            raise StorageFileNotFoundError(f"Storage 404: {path}")
        return self.files[path]

    async def download_if_changed(self, path: str, known_etag: str) -> bytes | None:
        meta = self.metadata.get(path)
        if meta and meta.etag == known_etag:
            return None
        return await self.download(path)

    async def upload(self, path: str, content: bytes) -> None:
        self.appels.append(("upload", path))
        self.add_file(path, content)

    async def mkdir(self, path: str) -> None:
        self.appels.append(("mkdir", path))
        self._tick += 1
        self.metadata[path] = StorageFile(
            path=path,
            name=path.rstrip("/").split("/")[-1],
            is_directory=True,
            last_modified=INSTANT_ZERO + timedelta(seconds=self._tick),
        )

    async def exists(self, path: str) -> bool:
        return path in self.files or path in self.metadata

    async def get_etag(self, path: str) -> str | None:
        meta = self.metadata.get(path)
        return meta.etag if meta else None

    async def delete(self, path: str) -> None:
        self.appels.append(("delete", path))
        self.files.pop(path, None)
        self.metadata.pop(path, None)


class FakeMessaging:
    """`MessagingProtocol` en mémoire.

    Remplace les `AsyncMock()` bruts des tests actuels, qui acceptent n'importe quel
    appel et ne vérifient donc **rien** du contrat. Ici, un appel hors protocole
    échoue, et les envois sont observables.

    `injecter()` déclenche le callback enregistré par `on_message` : c'est ce qui
    permet de piloter la boucle de réception sans réseau ni horloge.
    """

    def __init__(self) -> None:
        self.envois: list[dict[str, Any]] = []
        self.frappes: list[tuple[str, bool]] = []
        self.connecte = False
        self.demarre = False
        self._callback = None
        self._jamais = asyncio.Event()

    # ── MessagingProtocol ───────────────────────────────────────────────────

    async def connect(self) -> None:
        self.connecte = True

    async def run(self) -> None:
        """Boucle d'écoute, **comme le déclare le Protocol** : elle ne rend pas la main.

        Une doublure qui retournerait immédiatement serait plus commode, mais elle ne
        se comporterait pas comme `NoopMessaging` ni `WebChatMessaging`, qui bouclent
        toutes deux sur `asyncio.sleep`. Un test écrit contre une doublure complaisante
        passerait, puis pendrait en production.

        L'attente porte sur un `Event` qui n'est jamais posé : la coroutine est donc
        annulable proprement, et `asyncio.wait_for()` la borne dans un test.
        """
        self.demarre = True
        await self._jamais.wait()

    async def send(
        self,
        conversation_id: str,
        text: str,
        formatted: str | None = None,
        is_status: bool = False,
    ) -> None:
        """Signature **exactement** celle du Protocol.

        La version précédente avait un `reply_to` qui n'existe nulle part — ni dans
        `MessagingProtocol`, ni dans `matrix.py`, ni dans `webchat.py` — et omettait
        `is_status`, qui sert à rendre un message en `m.notice`. Une doublure plus
        permissive que le contrat laisse passer des appels que la production refuse.
        """
        self.envois.append(
            {
                "conversation_id": conversation_id,
                "text": text,
                "formatted": formatted,
                "is_status": is_status,
            }
        )

    async def send_typing(
        self, conversation_id: str, typing: bool = True, timeout: int = 10000
    ) -> None:
        self.frappes.append((conversation_id, typing))

    def on_message(self, callback) -> None:
        self._callback = callback

    # ── Pilotage depuis le test ─────────────────────────────────────────────

    async def injecter(self, message: IncomingMessage) -> None:
        """Simule la réception d'un message entrant."""
        if self._callback is None:
            raise AssertionError("aucun callback enregistré — on_message() non appelé")
        await self._callback(message)

    @property
    def dernier_envoi(self) -> dict[str, Any] | None:
        return self.envois[-1] if self.envois else None

    def textes_envoyes(self, conversation_id: str | None = None) -> list[str]:
        return [
            e["text"]
            for e in self.envois
            if conversation_id is None or e["conversation_id"] == conversation_id
        ]


class FakeLLM:
    """`LLMClientProtocol` déterministe.

    - `chat()` : réponses scriptées via `chat_responses`, servies dans l'ordre puis
      répétées. Aucune génération, aucun aléa.
    - `embed()` : vecteur dérivé d'un SHA-256 du texte. Le **même texte donne toujours
      le même vecteur**, dans ce processus comme dans le suivant — condition nécessaire
      pour tester un index vectoriel.

    `embedding_dim` vaut 384 par défaut, pour la vitesse. Les endpoints réels mesurés
    servent du **4096** (Albert `qwen3-vl-embedding-8b`, SSPCloud `qwen3-embedding-8b`) :
    un test de dimensionnement mémoire doit donc fixer explicitement `embedding_dim`,
    pas hériter du défaut.
    """

    def __init__(self, embedding_dim: int = 384) -> None:
        self.embedding_dim = embedding_dim
        self.chat_responses: list[str] = [
            "D'après les documents, la procédure comporte 3 étapes. [guide.txt]"
        ]
        self.tool_call_responses: list = []
        self.appels_chat: list[list] = []
        # `priority` de chaque appel, dans l'ordre. Ce n'est pas un détail : les
        # appels « background » (OCR, indexation) prennent un sémaphore réduit pour
        # toujours laisser un créneau aux requêtes utilisateur. Un travail de fond
        # qui s'annoncerait « user » affamerait les conversations, sans erreur ni
        # trace. La doublure permet de l'assertionner.
        self.priorites: list[str] = []
        # `temperature` de chaque appel, pour la même raison que `priorites`. Un
        # contrôle — vérificateur de fidélité, classifieur — dont la température
        # ne serait pas nulle rendrait un verdict différent d'une exécution à
        # l'autre : il ne contrôlerait plus rien, sans qu'aucune erreur ne le dise.
        self.temperatures: list[float] = []
        self._chat_call_count = 0
        self._tool_call_count = 0

    async def chat(
        self, messages, model=None, temperature=0.3, max_tokens=2048, priority="user"
    ) -> str:
        self.appels_chat.append(messages)
        self.priorites.append(priority)
        self.temperatures.append(temperature)
        reponse = self.chat_responses[
            min(self._chat_call_count, len(self.chat_responses) - 1)
        ]
        self._chat_call_count += 1
        return reponse

    async def chat_stream(self, messages, model=None, temperature=0.3, max_tokens=2048,
                          priority="user"):
        reponse = await self.chat(messages, model, temperature, max_tokens, priority)
        for mot in reponse.split():
            yield mot + " "

    async def chat_with_tools(
        self,
        messages,
        tools,
        model=None,
        temperature=0.3,
        max_tokens=2048,
        tool_choice="auto",
        priority="user",
    ):
        from colaig.models import ChatCompletionResult

        if self.tool_call_responses:
            self.priorites.append(priority)
            idx = min(self._tool_call_count, len(self.tool_call_responses) - 1)
            resultat = self.tool_call_responses[idx]
            self._tool_call_count += 1
            return resultat
        texte = await self.chat(messages, model, temperature, max_tokens, priority)
        return ChatCompletionResult(content=texte, finish_reason="stop")

    async def embed(self, text: str) -> list[float]:
        import numpy as np

        # SHA-256 plutôt que MD5 : même déterminisme, sans le signal trompeur d'un
        # algorithme cassé dans un dépôt destiné à l'administration.
        graine = int(hashlib.sha256(text.encode()).hexdigest()[:8], 16)
        rng = np.random.RandomState(graine)
        vecteur = rng.randn(self.embedding_dim).astype(np.float32)
        vecteur = vecteur / np.linalg.norm(vecteur)
        return vecteur.tolist()

    async def embed_batch(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        return [await self.embed(t) for t in texts]


# ── Alias de compatibilité ──────────────────────────────────────────────────
# Les tests existants importent ces noms. Ils héritent du déterminisme sans
# modification. Le nom canonique est `Fake*` — cf. lot L0.4.

MockStorage = FakeStorage
MockWebDAVClient = FakeStorage
MockAlbertClient = FakeLLM
