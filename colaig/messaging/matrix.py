"""
Colaig — MatrixMessaging (Tchap)

Implémente MessagingProtocol pour Matrix/Tchap.
Utilise matrix-nio (AsyncClient) pour la connexion au homeserver.
"""

from __future__ import annotations

import html as _html
import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path

from nio import (
    AsyncClient,
    AsyncClientConfig,
    DownloadError,
    InviteMemberEvent,
    JoinError,
    KeysQueryResponse,
    LoginResponse,
    MegolmEvent,
    ReactionEvent,
    RoomEncryptedAudio,
    RoomEncryptedFile,
    RoomEncryptedImage,
    RoomMessageAudio,
    RoomMessageFile,
    RoomMessageImage,
    RoomMessageText,
    SyncError,
)
from nio.crypto.device import TrustState

from colaig import paths
from colaig.exceptions import MessagingError
from colaig.models import Attachment, ConversationType, IncomingMessage, Reaction

logger = logging.getLogger(__name__)

# Messages de plus de 5 minutes ignorés au démarrage
_STALE_MESSAGE_SECONDS = 300

# Combien de fils l'instance retient. Un processus qui tourne des semaines ne peut pas
# se souvenir de tous les siens : même exigence que le verrou d'historique et que la
# retenue des messages indéchiffrables — une structure qui ne décroît jamais finit par
# tenir la mémoire.
#
# La purge retire les PLUS ANCIENS. Purger au hasard, ou purger le dernier, ferait
# perdre la conversation en cours — précisément celle que l'utilisateur écrit.
_MAX_FILS_SUIVIS = 1024

# Combien de salons dont on retient le dernier message émis (L3.3).
#
# Sert à poser une réaction sous SA PROPRE réponse, qui exige d'en connaître
# l'identifiant. Même raison de borner que ci-dessus : une entrée par salon jamais
# purgée croît avec le nombre de salons vus, pas avec l'activité.
_MAX_SALONS_SUIVIS = 512

# Taille maximale d'une piece jointe acceptee, en octets.
#
# Le telechargement se fait EN MEMOIRE — comme celui de l'audio, dont ce chemin
# reutilise le code. Un fichier de plusieurs centaines de megaoctets tuerait le
# processus, et un salon partage en contient tot ou tard un : une video, une archive,
# un plan. Le refus est journalise, jamais silencieux.
_MAX_PIECE_JOINTE_OCTETS = 25 * 1024 * 1024


# ── Helpers Markdown → HTML (SPEC-48 : Matrix utilise HTML sanitisé) ─────────

def _escape_html(text: str) -> str:
    """Échappe les caractères spéciaux HTML (&, <, >)."""
    return _html.escape(text, quote=False)


def _inline_markdown(text: str) -> str:
    """Transformations Markdown inline → HTML (gras, italique, code)."""
    text = _escape_html(text)
    # Code inline — avant gras/italique pour éviter les conflits de symboles
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    # Gras ** ou __
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"__(.+?)__", r"<strong>\1</strong>", text)
    # Italique * (mais pas **) ou _ entouré de non-alphanum
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\w)_(?!_)(.+?)(?<!_)_(?!\w)", r"<em>\1</em>", text)
    return text


def _markdown_to_html(text: str) -> str:
    """Convertit Markdown CommonMark → HTML sanitisé pour Matrix/Tchap.

    Couvre le sous-ensemble produit par le Synthétiseur :
    titres, listes, blocs de code, gras/italique, paragraphes.
    (SPEC-48 : Matrix a choisi HTML plutôt que Markdown natif.)
    """
    lines = text.split("\n")
    result: list[str] = []
    in_code = False
    code_buf: list[str] = []
    in_ul = False
    in_ol = False

    def _close_lists() -> None:
        nonlocal in_ul, in_ol
        if in_ul:
            result.append("</ul>")
            in_ul = False
        if in_ol:
            result.append("</ol>")
            in_ol = False

    for line in lines:
        # ── Blocs de code (``` ... ```) ────────────────────────────────────────
        if line.startswith("```"):
            if not in_code:
                _close_lists()
                in_code = True
                code_buf = []
            else:
                in_code = False
                code_content = "\n".join(_escape_html(l) for l in code_buf)
                result.append(f"<pre><code>{code_content}</code></pre>")
                code_buf = []
            continue
        if in_code:
            code_buf.append(line)
            continue

        # ── Titres (#, ##, ###, ...) ───────────────────────────────────────────
        h = re.match(r"^(#{1,6})\s+(.+)$", line)
        if h:
            _close_lists()
            n = len(h.group(1))
            result.append(f"<h{n}>{_inline_markdown(h.group(2))}</h{n}>")
            continue

        # ── Règle horizontale (---, ***, ___) ─────────────────────────────────
        if re.match(r"^[-*_]{3,}\s*$", line):
            _close_lists()
            result.append("<hr/>")
            continue

        # ── Listes non-ordonnées (- item, * item, + item) ─────────────────────
        ul = re.match(r"^\s*[-*+]\s+(.+)$", line)
        if ul:
            if in_ol:
                result.append("</ol>")
                in_ol = False
            if not in_ul:
                result.append("<ul>")
                in_ul = True
            result.append(f"<li>{_inline_markdown(ul.group(1))}</li>")
            continue

        # ── Listes ordonnées (1. item) ─────────────────────────────────────────
        ol = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if ol:
            if in_ul:
                result.append("</ul>")
                in_ul = False
            if not in_ol:
                result.append("<ol>")
                in_ol = True
            result.append(f"<li>{_inline_markdown(ol.group(1))}</li>")
            continue

        # ── Ligne vide → séparateur ────────────────────────────────────────────
        if not line.strip():
            _close_lists()
            result.append("")
            continue

        # ── Texte normal ───────────────────────────────────────────────────────
        _close_lists()
        result.append(_inline_markdown(line))

    _close_lists()
    # Fermer un bloc de code non terminé (robustesse)
    if in_code and code_buf:
        code_content = "\n".join(_escape_html(l) for l in code_buf)
        result.append(f"<pre><code>{code_content}</code></pre>")

    return "\n".join(result)


def _strip_markdown(text: str) -> str:
    """Supprime les marqueurs Markdown pour obtenir du texte brut (champ body Matrix)."""
    # Blocs de code → garder le contenu
    text = re.sub(r"```[^\n]*\n(.*?)```", r"\1", text, flags=re.DOTALL)
    # Code inline
    text = re.sub(r"`([^`]+)`", r"\1", text)
    # Titres
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # Gras
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    # Italique
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    text = re.sub(r"_(.+?)_", r"\1", text)
    # Règles horizontales
    text = re.sub(r"^[-*_]{3,}\s*$", "", text, flags=re.MULTILINE)
    return text.strip()


def _exiger_e2e() -> None:
    """Vérifie que le chiffrement de bout en bout est réellement disponible.

    ON TESTE LA CAPACITÉ, PAS SON IMPLÉMENTATION.

    La première version faisait `import olm`. C'était juste pour `matrix-nio` 0.25 et
    **faux à partir de 0.26**, qui a remplacé libolm par `vodozemac`, sa réimplémentation
    en Rust. Vérifié en conteneur : avec nio 0.26, `olm` est absent, `vodozemac` est là,
    et `AsyncClientConfig(encryption_enabled=True)` passe sans broncher.

    Le contrôle aurait donc **refusé de démarrer sur une installation parfaitement
    capable** — un garde-fou qui bloque ce qui fonctionne, et pour une raison que son
    message d'erreur aurait rendue incompréhensible : « installez python-olm » sur un
    système qui n'en a pas besoin.

    D'où la règle : on demande à nio s'il sait chiffrer, et on le laisse répondre. Quel
    que soit le paquet qui le lui permet, aujourd'hui ou demain.

    Sans cette vérification, l'échec survient à la première connexion sous la forme d'un
    `ImportWarning` remonté des entrailles de nio : il ne nomme ni le paquet à installer,
    ni la dépendance système en cause, ni le fait que Tchap chiffre tous ses salons —
    donc que désactiver le chiffrement n'est pas une échappatoire.

    Ce n'est pas une dépendance optionnelle qu'on pourrait contourner : **sur Tchap,
    sans chiffrement, il n'y a pas de lecture possible des messages.**
    """
    try:
        AsyncClientConfig(encryption_enabled=True)
    except Exception as exc:
        raise MessagingError(
            "Le backend Matrix exige le chiffrement de bout en bout (Tchap chiffre "
            f"tous ses salons), et matrix-nio ne peut pas l'activer : {exc}." + chr(10) +
            "Installer l'extra : pip install 'matrix-nio[e2e]'" + chr(10) +
            "Selon la version, il apporte python-olm — qui se compile contre libolm, "
            "soit apt install libolm-dev — ou vodozemac, qui n'a pas cette contrainte. "
            "Sous Windows aucune roue de python-olm n'est publiée : utiliser une "
            "version de nio >= 0.26, WSL, ou un conteneur."
        ) from exc


class MatrixMessaging:
    """Client Matrix/Tchap pour Colaig.

    Implémente MessagingProtocol.

    Args:
        homeserver: URL du homeserver Matrix.
        username: Identifiant Matrix (ex: @colaig:agent.tchap.gouv.fr).
        password: Mot de passe.
    """

    def __init__(
        self,
        homeserver: str,
        username: str,
        password: str,
        token_store: Path | None = None,
    ) -> None:
        self._homeserver = homeserver
        self._username = username
        self._password = password
        self._client: AsyncClient | None = None
        self._message_callbacks: list[Callable] = []
        self._start_time: float = 0.0
        # L'IDENTITE QUE LE HOMESERVER NOUS DONNE, et qui fait foi pour tout ce qui
        # decide. `_username` est ce qu'on a TAPE dans la configuration : il sert a se
        # connecter, plus a se reconnaitre.
        #
        # Matrix ne delivre que des MXID complets (`@smoke:agent.tchap.gouv.fr`). Une
        # configuration portant l'identifiant nu faisait echouer trois controles EN
        # SILENCE — dont celui qui empeche le bot de se repondre a lui-meme.
        self._identite: str = ""
        # Un salon n'est prévenu qu'une fois d'un message illisible (L2.6) : un appareil
        # mal apparié en produit des dizaines, et un message répété cesse d'être lu.
        self._salons_prevenus_indechiffrable: set[str] = set()
        # Les fils ouverts sur une réponse du bot (L3.2). Un `dict` et non un `set` :
        # il faut un ORDRE pour purger les plus anciens, et l'ordre d'insertion d'un
        # dict est garanti depuis Python 3.7.
        self._fils_suivis: dict[str, None] = {}
        # Le dernier message NON-STATUT émis par salon (L3.3) — pour poser une réaction
        # sous sa propre réponse. Même structure ordonnée, même raison de purge.
        self._derniers_envois: dict[str, str] = {}
        self._reaction_callback: Callable | None = None
        # Fichier de persistance du token (évite de créer une nouvelle session à chaque démarrage)
        self._token_store = token_store or paths.local_file("matrix_token.json")
        # Répertoire du crypto store E2E (clés Olm/Megolm — nécessite matrix-nio[e2e])
        self._store_path = self._token_store.parent / "e2e_store"

    @property
    def identite(self) -> str:
        """Le MXID qui fait foi : celui du homeserver, sinon la configuration.

        Le repli permet au pipeline de tourner sans connexion — sans lui, tout test de
        reception exigerait un homeserver.
        """
        return self._identite or self._username

    def _retenir_identite(self, mxid: str) -> None:
        """Retient l'identite rendue par le serveur. Une valeur VIDE est ignoree.

        Un `whoami` degrade ne doit pas effacer ce qu'on savait : comparer `event.sender`
        a la chaine vide reviendrait a ne jamais reconnaitre ses propres messages.
        """
        if mxid:
            self._identite = mxid

    async def connect(self) -> None:
        """Connexion au homeserver Matrix (login ou réutilisation du token existant)."""
        # Charger le device_id sauvegardé pour passer au constructeur (évite de créer un nouveau device)
        saved_device_id = ""
        if self._token_store.exists():
            try:
                saved_device_id = json.loads(self._token_store.read_text()).get("device_id", "")
            except Exception:
                pass

        _exiger_e2e()

        # Créer le répertoire du store E2E (clés Olm/Megolm — requis par Tchap E2E)
        self._store_path.mkdir(parents=True, exist_ok=True)

        # E2E activé — Tchap chiffre tous les salons par défaut
        client_config = AsyncClientConfig(
            max_timeouts=10,
            max_limit_exceeded=0,
            store_sync_tokens=True,
            encryption_enabled=True,
        )
        self._client = AsyncClient(
            self._homeserver,
            self._username,
            device_id=saved_device_id,
            store_path=str(self._store_path),
            config=client_config,
        )
        self._start_time = time.time()
        # Un salon n'est prevenu qu'une fois d'un message illisible (L2.6).
        self._salons_prevenus_indechiffrable: set[str] = set()

        # Réutiliser le token existant via restore_login() qui initialise aussi le store Olm
        if self._token_store.exists():
            try:
                data = json.loads(self._token_store.read_text())
                self._client.restore_login(
                    user_id=data.get("user_id", self._username),
                    device_id=data["device_id"],
                    access_token=data["access_token"],
                )
                logger.info(
                    "matrix token chargé depuis %s (device_id=%s)",
                    self._token_store, data["device_id"],
                )
                # Valider le token immédiatement avec whoami() (pattern Colaig_26072025)
                resp = await self._client.whoami()
                if not hasattr(resp, "user_id"):
                    raise ValueError(f"token rejeté par le serveur: {resp}")
                logger.info("token validé (user_id=%s)", resp.user_id)
                self._retenir_identite(resp.user_id)
            except Exception as e:
                logger.warning("token Matrix invalide (%s), re-login...", e)
                self._token_store.unlink(missing_ok=True)
                await self._do_login()
        else:
            await self._do_login()

        logger.info(
            "matrix connecté à %s en tant que %s",
            self._homeserver, self._username,
        )

        # Upload des clés E2E — obligatoire pour que Synapse accepte ce device dans le sync
        # (sans clés, le sync worker peut bloquer sur la distribution des clés de session)
        try:
            if self._client.should_upload_keys:
                resp = await self._client.keys_upload()
                logger.info("clés E2E uploadées: %s", type(resp).__name__)
            else:
                logger.info("clés E2E déjà uploadées pour ce device")
        except Exception as exc:
            logger.warning("upload clés E2E échoué (%s)", exc)

        # Auto-trust : marque les devices inconnus comme user_ignored après chaque keys_query.
        # Pattern bot — le bot n'effectue pas de vérification manuelle des devices.
        self._client.add_response_callback(self._auto_trust_devices, KeysQueryResponse)
        # Applique immédiatement sur les devices déjà connus (re-login depuis token)
        self._do_auto_trust()

        # Enregistre les callbacks
        self._client.add_event_callback(self._on_invite, InviteMemberEvent)
        self._client.add_event_callback(self._on_room_message, RoomMessageText)
        # Audio : non-chiffré (rare) ET chiffré E2E (Tchap = RoomEncryptedAudio)
        self._client.add_event_callback(self._on_room_audio, RoomMessageAudio)
        self._client.add_event_callback(self._on_room_audio, RoomEncryptedAudio)
        # Fichiers et images, clairs ET chiffres (Tchap chiffre par defaut). Sans ces
        # quatre rappels, deposer un PDF dans un salon ne produisait RIEN — ni erreur,
        # ni trace. Colaig etait aveugle aux documents dans le canal meme ou on lui en
        # parle, alors que le classement documentaire est sa raison d'etre.
        self._client.add_event_callback(self._on_room_file, RoomMessageFile)
        self._client.add_event_callback(self._on_room_file, RoomMessageImage)
        self._client.add_event_callback(self._on_room_file, RoomEncryptedFile)
        self._client.add_event_callback(self._on_room_file, RoomEncryptedImage)
        # Messages que nio n'a pas pu dechiffrer. Sans ce rappel ils sont ignores
        # EN SILENCE : l'utilisateur voit un assistant qui ne repond pas (L2.6).
        # Reactions (L3.3) : le retour de l'utilisateur en un seul geste.
        self._client.add_event_callback(self._on_reaction, ReactionEvent)

        self._client.add_event_callback(self._on_undecryptable, MegolmEvent)

    def _do_auto_trust(self) -> None:
        """Marque tous les devices non-vérifiés comme user_ignored (pattern bot).

        device_store itère directement sur des OlmDevice (pas sur des user_ids).
        """
        if self._client is None or self._client.olm is None:
            return
        count = 0
        for device in self._client.olm.device_store:
            if device.trust_state == TrustState.unset:
                device.trust_state = TrustState.ignored
                count += 1
        if count:
            logger.debug("auto-trust: %d device(s) marqués user_ignored", count)

    async def _auto_trust_devices(self, response: KeysQueryResponse) -> None:
        """Callback KeysQueryResponse — auto-trust des nouveaux devices."""
        self._do_auto_trust()

    async def _do_login(self) -> None:
        """Login Matrix et persistance du token."""
        if self._client is None:
            return
        response = await self._client.login(self._password, device_name="Colaig")
        if not isinstance(response, LoginResponse):
            raise MessagingError(f"Login Matrix échoué: {response}")

        # Persister le token pour les prochains démarrages
        self._token_store.parent.mkdir(parents=True, exist_ok=True)
        self._token_store.write_text(json.dumps({
            "access_token": response.access_token,
            "device_id": response.device_id,
            "user_id": response.user_id,
        }))
        self._retenir_identite(response.user_id)
        logger.info("matrix token sauvegardé (device_id=%s)", response.device_id)

    async def _handle_sync_failure(self) -> None:
        """Après une exception dans sync_forever : whoami() pour valider le token.

        Si le token est invalide → re-login. Sinon → sleep 30s et réessayer.
        """
        import asyncio
        if self._client is None:
            return
        try:
            resp = await self._client.whoami()
            if not hasattr(resp, "user_id"):
                raise ValueError(f"token rejeté: {resp}")
            # Token encore valide — simple pause réseau
            logger.info("token valide (user_id=%s) — retry dans 30s", resp.user_id)
            await asyncio.sleep(30)
        except Exception:
            logger.warning("token invalide ou whoami échoué — re-login")
            try:
                self._token_store.unlink(missing_ok=True)
                await self._do_login()
                logger.info("re-login réussi")
            except Exception as login_exc:
                logger.error("re-login échoué (%s) — retry dans 60s", login_exc)
                await asyncio.sleep(60)

    async def run(self) -> None:
        """Boucle d'écoute infinie des événements.

        Pattern Colaig_26072025 :
        1. sync initial pour charger l'état des salons
        2. sync_forever(timeout=3000) — long-poll continu
        3. Si exception → vérifier le token via whoami(), re-login si nécessaire
        """
        if self._client is None:
            raise MessagingError("connect() doit être appelé avant run()")
        logger.info("matrix boucle d'écoute démarrée")

        # Filtre Synapse : lazy_load_members évite le calcul des clés Megolm pour tous
        # les membres à chaque sync (cause de timeouts sur Tchap avec salons chiffrés).
        # Ce filtre s'applique en inline (pas de server-side filter registration).
        _SYNC_FILTER = {
            "room": {
                "state": {"lazy_load_members": True},
                "timeline": {"limit": 50},
            },
            "presence": {"not_types": ["*"]},
        }

        # Sync initial : charge l'état des salons sans historique de messages.
        # Les messages anciens seront filtrés par timestamp dans _on_room_message.
        try:
            sync_resp = await self._client.sync(
                timeout=30000, full_state=True, sync_filter=_SYNC_FILTER,
            )
            if isinstance(sync_resp, SyncError):
                logger.warning("sync initial SyncError — %s", sync_resp)
            else:
                rooms_obj = getattr(sync_resp, "rooms", None)
                if rooms_obj is not None:
                    rooms_joined = len(getattr(rooms_obj, "join", {}))
                else:
                    rooms_joined = 0
                logger.info(
                    "sync initial OK — next_batch=%s rooms_joined=%d rooms_loaded=%d",
                    self._client.next_batch, rooms_joined, len(self._client.rooms),
                )
        except Exception as exc:
            logger.warning("sync initial échoué (%s) — on continue quand même", exc)

        # sync_forever : pattern de référence — simple et efficace quand le token est valide.
        # En cas d'exception (max_timeouts=10 dépassé ou erreur réseau), on vérifie le token
        # et on re-login si nécessaire avant de relancer.
        while True:
            try:
                await self._client.sync_forever(
                    timeout=3000, full_state=False, sync_filter=_SYNC_FILTER,
                )
            except Exception as exc:
                logger.warning("sync_forever interrompu (%s) — vérification token...", exc)
                await self._handle_sync_failure()

    async def send(
        self,
        conversation_id: str,
        text: str,
        formatted: str | None = None,
        is_status: bool = False,
    ) -> None:
        """Envoie un message dans une conversation."""
        if self._client is None:
            raise MessagingError("Bot non connecté")

        # Auto-trust des nouveaux devices avant chaque envoi (le store se peuple après le sync)
        self._do_auto_trust()

        content: dict = {
            "msgtype": "m.notice" if is_status else "m.text",
            "body": _strip_markdown(text),           # Texte brut (fallback clients sans HTML)
            "format": "org.matrix.custom.html",      # SPEC-48 : HTML sanitisé
            "formatted_body": formatted or _markdown_to_html(text),  # Rendu riche
        }

        reponse = await self._client.room_send(
            room_id=conversation_id,
            message_type="m.room.message",
            content=content,
            ignore_unverified_devices=True,
        )

        # RETENIR CE MESSAGE COMME RACINE DE FIL POSSIBLE (L3.2).
        #
        # C'est ce qui rend le critère du lot opérant : « un fil ouvert sur une réponse
        # du bot est suivi sans nouvelle mention ». Sans cet enregistrement,
        # `suivre_fil` existerait sans que rien ne l'appelle — le motif « écrit et non
        # branché » que ce dépôt a trouvé neuf fois.
        #
        # Les messages de STATUT en sont exclus : un indicateur de progression n'est
        # pas une réponse, et ouvrir un fil dessus n'aurait pas de sens.
        #
        # La signature reste `-> None` : la remonter changerait `MessagingProtocol`,
        # donc TOUS les canaux. L'identifiant est exposé par `dernier_message_envoye()`,
        # sur le Protocol optionnel `ReactionProtocol`, où il ne coûte rien à ceux qui
        # ne savent pas réagir (L3.3, D51).
        if not is_status:
            event_id = getattr(reponse, "event_id", "") or ""
            self.suivre_fil(event_id)
            self._retenir_envoi(conversation_id, event_id)

    async def send_typing(
        self,
        conversation_id: str,
        typing: bool = True,
        timeout: int = 10000,
    ) -> None:
        """Envoie/arrête l'indicateur de frappe."""
        if self._client is None:
            return
        await self._client.room_typing(conversation_id, typing, timeout)

    def on_message(self, callback: Callable) -> None:
        """Enregistre un callback appelé pour chaque message reçu."""
        self._message_callbacks.append(callback)

    # Alias de rétrocompatibilité
    async def send_message(self, room_id: str, text: str, formatted: str | None = None) -> None:
        """Alias pour send() — rétrocompatibilité."""
        await self.send(room_id, text, formatted)

    # ── Callbacks internes ───────────────────────────────────────────

    async def _on_invite(self, room, event: InviteMemberEvent) -> None:
        """Auto-join quand invité dans un salon."""
        if self._client is None:
            return
        if event.state_key != self.identite:
            return

        result = await self._client.join(room.room_id)
        if isinstance(result, JoinError):
            logger.error("impossible de rejoindre %s: %s", room.room_id, result)
        else:
            logger.info("salon rejoint: %s", room.room_id)

    async def _on_undecryptable(self, room, event) -> None:
        """Un message que nio n'a pas pu dechiffrer.

        Sans ce traitement, l'evenement etait ignore SANS UN MOT. Ce n'est pas
        theorique : D34 a releve des « undecryptable Megolm event from a unknown
        device » dans le journal du bot, et note qu'un appareil neuf ne lit pas
        l'historique chiffre. L'utilisateur, lui, voit un assistant qui ne repond pas.

        Le salon n'est prevenu QU'UNE FOIS par processus : un appareil mal apparie
        produit des dizaines d'evenements illisibles, et un message repete cesse d'etre
        lu — ce qui reviendrait au silence par un autre chemin.
        """
        salon = getattr(room, "room_id", "") or ""
        logger.warning(
            "message non dechiffre dans %s (expediteur %s, session %s) — Colaig ne "
            "peut pas le lire. Cause habituelle : l'appareil du bot est plus recent "
            "que le message, ou les cles n'ont pas ete partagees avec lui.",
            salon, getattr(event, "sender", "?"), getattr(event, "session_id", "?"),
        )

        if salon in self._salons_prevenus_indechiffrable:
            return
        self._salons_prevenus_indechiffrable.add(salon)

        try:
            await self.send(
                salon,
                "Je ne parviens pas a dechiffrer un message de ce salon — il a "
                "probablement ete envoye avant que je n'y sois, ou depuis un appareil "
                "dont je n'ai pas les cles. Reformulez-le et je pourrai le lire.",
            )
        except Exception as exc:  # noqa: BLE001
            # Prevenir est un mieux, pas une obligation : un envoi en echec ne doit pas
            # arreter la boucle de reception.
            logger.warning("impossible de prevenir %s (%s)", salon, exc)

    def suivre_fil(self, event_id: str) -> None:
        """Retient un fil ouvert sur une réponse du bot.

        Appelé après l'envoi d'une réponse : les tours suivants de ce fil n'auront pas
        à mentionner le bot de nouveau. C'est le critère du lot L3.2 — exiger une
        mention à chaque tour rendrait le fil inutile, autant écrire dans le salon.
        """
        if not event_id:
            return
        self._fils_suivis.pop(event_id, None)      # remettre en tête s'il existait
        self._fils_suivis[event_id] = None
        while len(self._fils_suivis) > _MAX_FILS_SUIVIS:
            self._fils_suivis.pop(next(iter(self._fils_suivis)))

    # ── ReactionProtocol (L3.3, D51) ─────────────────────────────────

    def _retenir_envoi(self, conversation_id: str, event_id: str) -> None:
        """Retient le dernier message non-statut émis dans ce salon."""
        if not conversation_id or not event_id:
            return
        self._derniers_envois.pop(conversation_id, None)   # remettre en tête
        self._derniers_envois[conversation_id] = event_id
        while len(self._derniers_envois) > _MAX_SALONS_SUIVIS:
            self._derniers_envois.pop(next(iter(self._derniers_envois)))

    def dernier_message_envoye(self, conversation_id: str) -> str:
        """Identifiant du dernier message NON-STATUT émis ici. `""` si aucun."""
        return self._derniers_envois.get(conversation_id, "")

    async def reagir(self, conversation_id: str, message_id: str, emoji: str) -> None:
        """Pose une réaction sur un message.

        NE LÈVE JAMAIS. Poser une réaction est un confort ; la réponse est le produit.
        Un homeserver qui refuse `m.reaction` ne doit pas faire échouer le tour de
        conversation qui vient d'aboutir.
        """
        if self._client is None or not message_id or not emoji:
            return
        try:
            await self._client.room_send(
                room_id=conversation_id,
                message_type="m.reaction",
                content={"m.relates_to": {"rel_type": "m.annotation",
                                          "event_id": message_id,
                                          "key": emoji}},
                ignore_unverified_devices=True,
            )
        except Exception:
            logger.debug("réaction %s non posée dans %s", emoji, conversation_id)

    def on_reaction(self, callback: Callable) -> None:
        """Enregistre le rappel appelé pour chaque réaction porteuse de signal."""
        self._reaction_callback = callback

    async def _on_reaction(self, room, event: ReactionEvent) -> None:
        """Réaction reçue — trois filtres avant de la faire remonter.

        **1. Pas les nôtres.** Colaig pose lui-même les gestes proposés sous chaque
        réponse. Si sa propre pose remontait, chaque réponse s'auto-attribuerait autant
        de retours qu'elle propose de gestes, et le premier chiffre lu sur la qualité
        serait entièrement fabriqué par nous.

        **2. Seulement sur NOS messages.** `_fils_suivis` est exactement l'ensemble des
        messages que nous avons émis : le réutiliser évite un second registre qui
        divergerait. Deux collègues qui se félicitent dans un salon ne parlent pas de
        Colaig.

        **3. Pas l'historique rejoué.** Au démarrage, le serveur redélivre le passé ;
        sans ce garde, chaque relance réenregistrerait tous les retours déjà comptés.
        """
        if event.sender == self.identite:
            return

        reagit_a = getattr(event, "reacts_to", "") or ""
        if reagit_a not in self._fils_suivis:
            return

        if event.server_timestamp / 1000 < self._start_time - _STALE_MESSAGE_SECONDS:
            return

        if self._reaction_callback is None:
            return

        reaction = Reaction(
            user_id=event.sender,
            conversation_id=room.room_id,
            message_id=reagit_a,
            emoji=getattr(event, "key", "") or "",
            reaction_id=event.event_id,
            horodatage=event.server_timestamp,
        )
        try:
            await self._reaction_callback(reaction)
        except Exception:
            logger.exception("traitement de réaction en échec: %s", event.event_id)

    def _nous_concerne(self, event, contenu: dict, thread_root: str) -> bool:
        """En salon, ce message appelle-t-il l'assistant ?

        TROIS RÈGLES, DANS CET ORDRE.

        **1. Un fil que le bot a ouvert se poursuit sans mention.** C'est le critère du
        lot : quelqu'un pose une question, Colaig répond, la conversation continue dans
        le fil. Le bot ne suit QUE les fils enracinés sur ses propres réponses — sans
        cela, « suivre les fils » reviendrait à répondre à tout, et l'on aurait
        remplacé un excès de zèle par un autre.

        **2. `m.mentions` fait foi quand il est présent.** C'est le champ natif de
        Matrix depuis la version 1.7, renseigné par le client quand l'utilisateur pose
        une vraie mention : une DÉCLARATION D'INTENTION, et non une coïncidence de
        vocabulaire. S'il est là et ne nomme pas le bot, la réponse est non — même si
        le corps contient son nom.

        **3. À défaut, le corps du message.** Repli pour les clients qui ne posent pas
        `m.mentions` : anciens clients, ponts, bots.

        CE QUE LA RÈGLE 2 CORRIGE. La décision se prenait par recherche de sous-chaîne
        dans le corps, sur le LOCALPART de l'identifiant — donc « il faudrait demander
        à colaig ce qu'il en pense » réveillait l'assistant. Dans un salon actif, il
        répondait à des messages qui parlaient de lui.
        """
        if thread_root and thread_root in self._fils_suivis:
            return True

        mentions = contenu.get("m.mentions")
        if isinstance(mentions, dict):
            return self.identite in (mentions.get("user_ids") or [])

        corps = getattr(event, "body", "") or ""
        localpart = self.identite.split(":")[0].lstrip("@")
        return localpart in corps or self.identite in corps

    async def _on_room_message(self, room, event: RoomMessageText) -> None:
        """Traite un message reçu dans un salon."""
        if self._client is None:
            return

        logger.info("message reçu: sender=%s room=%s body_chars=%d", event.sender, room.room_id, len(event.body))

        # Ignorer ses propres messages
        if event.sender == self.identite:
            logger.debug("ignoré: propre message")
            return

        # Ignorer les messages trop vieux (replay au démarrage)
        event_ts = event.server_timestamp / 1000  # ms → s
        if event_ts < self._start_time - _STALE_MESSAGE_SECONDS:
            logger.debug("ignoré: message trop vieux (ts=%s start=%s)", event_ts, self._start_time)
            return

        # Déterminer le type de conversation
        conversation_type = await self._resolve_conversation_type(room.room_id)

        contenu = (event.source or {}).get("content", {}) if isinstance(
            getattr(event, "source", None), dict) else {}

        # Le FIL, distinct de la citation.
        #
        # `rel_type: m.thread` ouvre une conversation séparée, qui a sa propre
        # continuité. `m.in_reply_to` seul est une CITATION dans le flux du salon.
        # Les confondre ferait suivre comme un fil toute réponse citée, et
        # l'assistant s'inviterait dans des échanges qui ne le concernent pas.
        relates = contenu.get("m.relates_to") or {}
        thread_root = (relates.get("event_id", "")
                       if relates.get("rel_type") == "m.thread" else "")
        in_reply = relates.get("m.in_reply_to") or {}
        is_reply = bool(in_reply.get("event_id"))
        reply_to = in_reply.get("event_id", "")

        # En salon (non-DM), décider s'il faut répondre.
        if conversation_type not in (ConversationType.DM, ConversationType.UNKNOWN):
            if not self._nous_concerne(event, contenu, thread_root):
                return

        # Construire l'IncomingMessage (noms provider-agnostic)
        message = IncomingMessage(
            user_id=event.sender,
            conversation_id=room.room_id,
            body=event.body,
            conversation_type=conversation_type,
            message_id=event.event_id,
            display_name=room.user_name(event.sender) or event.sender,
            is_reply=is_reply,
            reply_to=reply_to,
            thread_root=thread_root,
            platform="matrix",
        )

        # Appeler les handlers enregistrés
        for callback in self._message_callbacks:
            try:
                await callback(message)
            except Exception:
                logger.exception("erreur dans handler message pour %s", room.room_id)

    async def _on_room_audio(self, room, event: RoomMessageAudio) -> None:
        """Traite un message vocal reçu dans un salon."""
        if self._client is None:
            return

        # Ignorer ses propres messages
        if event.sender == self.identite:
            return

        # Ignorer les messages trop vieux (replay au démarrage)
        event_ts = event.server_timestamp / 1000
        if event_ts < self._start_time - _STALE_MESSAGE_SECONDS:
            logger.info("ignoré: vocal trop vieux (ts=%s start=%s)", event_ts, self._start_time)
            return

        logger.info("vocal reçu: sender=%s room=%s", event.sender, room.room_id)

        # Télécharger l'audio (gère chiffrement E2E si disponible)
        audio_bytes = await self._download_audio(event)
        if not audio_bytes:
            logger.warning("impossible de télécharger l'audio: %s", event.event_id)
            return

        filename = getattr(event, "body", None) or "voice.ogg"
        # Déterminer le MIME type depuis l'événement ou défaut ogg/opus (Tchap)
        content = event.source.get("content", {}) if hasattr(event, "source") else {}
        info = content.get("info", {})
        mime_type = info.get("mimetype", "audio/ogg")

        attachment = Attachment(
            filename=filename,
            content_type=mime_type,
            size=len(audio_bytes),
            content=audio_bytes,
        )

        conversation_type = await self._resolve_conversation_type(room.room_id)

        message = IncomingMessage(
            user_id=event.sender,
            conversation_id=room.room_id,
            body="",  # Transcription faite dans handlers.py
            conversation_type=conversation_type,
            message_id=event.event_id,
            display_name=room.user_name(event.sender) or event.sender,
            platform="matrix",
            attachments=[attachment],
        )

        for callback in self._message_callbacks:
            try:
                await callback(message)
            except Exception:
                logger.exception("erreur handler audio pour %s", room.room_id)

    async def _on_room_file(self, room, event) -> None:
        """Traite un fichier déposé dans un salon — document, image, clair ou chiffré.

        POURQUOI UNE PIÈCE JOINTE N'EXIGE PAS DE MENTION. Le texte en salon en demande
        une (L3.2) ; le fichier, non : **déposer un document EST l'intention**.
        Personne n'écrit « @colaig » en glissant un PDF, et le chemin audio suit déjà
        cette règle.

        La contrepartie est assumée : Colaig ne RÉPOND pas à un fichier, il le classe.
        Répondre à chaque dépôt inonderait un salon où des collègues s'échangent des
        documents.

        LE CORPS RESTE VIDE. Le nom du fichier n'est pas une question : le mettre dans
        `body` ferait chercher « marche-2026.pdf » dans le corpus par le pipeline de
        réponse, alors que le fichier ne demande rien.
        """
        if self._client is None:
            return
        if event.sender == self.identite:
            # Colaig produit des documents ; les réingérer ferait une boucle.
            return

        # Au démarrage, le serveur rejoue l'historique : sans ce garde, un redémarrage
        # réingérerait tous les documents du salon.
        if event.server_timestamp / 1000 < self._start_time - _STALE_MESSAGE_SECONDS:
            logger.info("ignoré: fichier trop vieux (%s)", event.event_id)
            return

        contenu = (event.source or {}).get("content", {}) if isinstance(
            getattr(event, "source", None), dict) else {}
        info = contenu.get("info") or {}
        nom = getattr(event, "body", None) or "piece-jointe"
        annonce = int(info.get("size") or 0)

        # Refus AVANT téléchargement : la borne ne servirait à rien si l'on chargeait
        # d'abord en mémoire ce qu'on refuse ensuite.
        if annonce > _MAX_PIECE_JOINTE_OCTETS:
            logger.warning(
                "pièce jointe refusée: %s annonce %.1f Mo (plafond %.0f Mo) — salon %s",
                nom, annonce / 1e6, _MAX_PIECE_JOINTE_OCTETS / 1e6, room.room_id)
            return

        # `_download_audio` gère `mxc://` ET le déchiffrement E2E. Rien dans son corps
        # n'est propre à l'audio — seul son nom le laisse croire.
        octets = await self._download_audio(event)
        if not octets:
            logger.warning("téléchargement impossible: %s (%s)", nom, event.event_id)
            return
        if len(octets) > _MAX_PIECE_JOINTE_OCTETS:
            # Un `info.size` absent ou menteur ne doit pas contourner la borne.
            logger.warning("pièce jointe refusée après téléchargement: %s (%.1f Mo)",
                           nom, len(octets) / 1e6)
            return

        piece = Attachment(
            filename=nom,
            content_type=info.get("mimetype", "application/octet-stream"),
            size=len(octets),
            content=octets,
        )
        message = IncomingMessage(
            user_id=event.sender,
            conversation_id=room.room_id,
            body="",
            conversation_type=await self._resolve_conversation_type(room.room_id),
            message_id=event.event_id,
            display_name=room.user_name(event.sender) or event.sender,
            platform="matrix",
            attachments=[piece],
        )
        for callback in self._message_callbacks:
            try:
                await callback(message)
            except Exception:
                logger.exception("erreur handler fichier pour %s", room.room_id)

    async def _download_audio(self, event: RoomMessageAudio) -> bytes | None:
        """Télécharge (et décrypte si E2E) l'audio d'un événement Matrix.

        Gère deux cas :
        - Message non chiffré : ``event.url`` est un mxc:// direct.
        - Message E2E chiffré : ``event.source["content"]["file"]`` contient
          l'url mxc + la clé AES-CTR (format Matrix v2 encrypted file).
        """
        if self._client is None:
            return None

        mxc_url: str | None = None
        file_info: dict | None = None

        # Cas 1 — RoomEncryptedAudio : attributs directs url/key/iv/hashes
        if isinstance(event, RoomEncryptedAudio):
            mxc_url = getattr(event, "url", None)
            if mxc_url and getattr(event, "key", None):
                file_info = {
                    "url": mxc_url,
                    "key": event.key,
                    "iv": event.iv,
                    "hashes": event.hashes,
                }
        # Cas 2 — RoomMessageAudio non chiffré : url directe
        elif getattr(event, "url", None):
            mxc_url = event.url
        else:
            # Cas 3 — fallback source dict (format legacy)
            content = event.source.get("content", {}) if hasattr(event, "source") else {}
            file_info = content.get("file")
            if file_info:
                mxc_url = file_info.get("url")

        if not mxc_url:
            return None

        try:
            resp = await self._client.download(mxc=mxc_url)
            if isinstance(resp, DownloadError):
                logger.warning("download audio Matrix échoué: %s", resp)
                return None

            audio_bytes: bytes = resp.body

            # Décryptage E2E si les informations de clé sont présentes
            if file_info and file_info.get("key"):
                try:
                    from nio.crypto.attachments import decrypt_attachment
                    key = file_info["key"]["k"]
                    iv = file_info["iv"]
                    sha256 = file_info["hashes"]["sha256"]
                    audio_bytes = decrypt_attachment(audio_bytes, key, sha256, iv)
                except Exception as exc:
                    logger.warning("décryptage audio E2E échoué (%s)", exc, exc_info=True)
                    return None  # Ne pas envoyer des bytes chiffrés à Albert

            return audio_bytes

        except Exception as exc:
            logger.warning("téléchargement audio Matrix échoué: %s", exc)
            return None

    async def _resolve_conversation_type(self, room_id: str) -> ConversationType:
        """Détermine le type de conversation (DM, public, privé)."""
        if self._client is None:
            return ConversationType.UNKNOWN

        try:
            response = await self._client.joined_members(room_id)
            if hasattr(response, "members"):
                if len(response.members) == 2:
                    return ConversationType.DM
        except Exception:
            pass

        # Vérifier si le salon est public
        room = self._client.rooms.get(room_id)
        if room:
            if hasattr(room, "join_rule") and room.join_rule == "public":
                return ConversationType.PUBLIC
            return ConversationType.PRIVATE

        return ConversationType.UNKNOWN


# Alias de rétrocompatibilité
MatrixBot = MatrixMessaging
