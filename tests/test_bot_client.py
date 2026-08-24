"""Tests pour colaig/messaging/matrix.py — Client Matrix/Tchap."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from colaig.messaging.matrix import MatrixMessaging
from colaig.models import ConversationType, IncomingMessage


@pytest.fixture
def messaging(tmp_path) -> MatrixMessaging:
    """Fixture avec un token_store inexistant (force le login plutôt que restore)."""
    return MatrixMessaging(
        homeserver="https://matrix.test.local",
        username="@colaig:test.local",
        password="secret",
        token_store=tmp_path / "matrix_token.json",
    )


class TestMatrixMessagingInit:
    """Tests d'initialisation."""

    def test_init(self, messaging):
        assert messaging._homeserver == "https://matrix.test.local"
        assert messaging._username == "@colaig:test.local"
        assert messaging._message_callbacks == []

    def test_on_message_registers_callback(self, messaging):
        async def handler(msg):
            pass
        messaging.on_message(handler)
        assert len(messaging._message_callbacks) == 1

    def test_on_message_multiple(self, messaging):
        async def h1(msg): pass
        async def h2(msg): pass
        messaging.on_message(h1)
        messaging.on_message(h2)
        assert len(messaging._message_callbacks) == 2


class TestMatrixMessagingConnect:
    """Tests de connexion."""

    @pytest.fixture(autouse=True)
    def _sans_prerequis_crypto(self):
        """Neutralise la vérification de `python-olm` — ce n'est pas l'objet ici.

        Ces tests portent sur la **logique de session** : réutilisation du token,
        `whoami()`, re-login. Ils remplacent déjà `AsyncClient` et `AsyncClientConfig`
        par des doubles, donc n'ont jamais touché la couche crypto.

        `_exiger_e2e()` a été ajouté dans `connect()` le 23/08/2026 et les faisait
        échouer sur toute machine sans libolm — un échec qui ne disait rien sur ce
        qu'ils vérifient. Le prérequis lui-même est couvert par
        `tests/test_matrix_prerequis.py`, où il est l'objet du test et non son décor.
        """
        with patch("colaig.messaging.matrix._exiger_e2e"):
            yield

    async def test_connect_success(self, messaging, tmp_path):
        from nio import LoginResponse
        mock_client = AsyncMock()
        mock_login_resp = MagicMock(spec=LoginResponse)
        mock_login_resp.access_token = "tok"
        mock_login_resp.device_id = "DEV1"
        mock_login_resp.user_id = "@colaig:test.local"
        mock_client.login.return_value = mock_login_resp
        mock_client.add_event_callback = MagicMock()
        mock_client.should_upload_keys = False

        with patch("colaig.messaging.matrix.AsyncClientConfig"), \
             patch("colaig.messaging.matrix.AsyncClient", return_value=mock_client):
            await messaging.connect()

        mock_client.login.assert_called_once_with("secret", device_name="Colaig")
        assert mock_client.add_event_callback.call_count == 4  # invite + text + audio + encrypted audio

    async def test_connect_validates_token_with_whoami(self, messaging, tmp_path):
        """restore_login suivi de whoami() — si valide, pas de re-login."""
        token_file = tmp_path / "matrix_token.json"
        token_file.write_text('{"access_token":"tok","device_id":"DEV1","user_id":"@colaig:test.local"}')
        messaging._token_store = token_file

        mock_client = AsyncMock()
        mock_client.restore_login = MagicMock()
        mock_whoami = MagicMock()
        mock_whoami.user_id = "@colaig:test.local"
        mock_client.whoami.return_value = mock_whoami
        mock_client.add_event_callback = MagicMock()
        mock_client.should_upload_keys = False

        with patch("colaig.messaging.matrix.AsyncClientConfig"), \
             patch("colaig.messaging.matrix.AsyncClient", return_value=mock_client):
            await messaging.connect()

        mock_client.whoami.assert_called_once()
        mock_client.login.assert_not_called()  # pas de re-login

    async def test_connect_relogins_if_whoami_fails(self, messaging, tmp_path):
        """Si whoami() échoue, le token est supprimé et un re-login est effectué."""
        from nio import LoginResponse
        token_file = tmp_path / "matrix_token.json"
        token_file.write_text('{"access_token":"stale","device_id":"DEV1","user_id":"@colaig:test.local"}')
        messaging._token_store = token_file

        mock_client = AsyncMock()
        mock_client.restore_login = MagicMock()
        # whoami() retourne une réponse sans user_id (erreur)
        mock_client.whoami.return_value = MagicMock(spec=[])  # pas d'attribut user_id

        mock_login_resp = MagicMock(spec=LoginResponse)
        mock_login_resp.access_token = "new_tok"
        mock_login_resp.device_id = "DEV1"
        mock_login_resp.user_id = "@colaig:test.local"
        mock_client.login.return_value = mock_login_resp
        mock_client.add_event_callback = MagicMock()
        mock_client.should_upload_keys = False

        with patch("colaig.messaging.matrix.AsyncClientConfig"), \
             patch("colaig.messaging.matrix.AsyncClient", return_value=mock_client):
            await messaging.connect()

        mock_client.login.assert_called_once()  # re-login déclenché

    async def test_connect_failure(self, messaging):
        from colaig.exceptions import MessagingError
        mock_client = AsyncMock()
        mock_client.login.return_value = MagicMock()  # pas un LoginResponse
        mock_client.should_upload_keys = False

        with patch("colaig.messaging.matrix.AsyncClientConfig"), \
             patch("colaig.messaging.matrix.AsyncClient", return_value=mock_client):
            with pytest.raises(MessagingError, match="Login Matrix échoué"):
                await messaging.connect()


class TestMatrixMessagingSend:
    """Tests d'envoi de messages."""

    async def test_send_plain(self, messaging):
        messaging._client = AsyncMock()
        await messaging.send("!room:test", "Hello")
        messaging._client.room_send.assert_called_once()
        call_kwargs = messaging._client.room_send.call_args[1]
        assert call_kwargs["room_id"] == "!room:test"
        assert call_kwargs["content"]["body"] == "Hello"

    async def test_send_formatted(self, messaging):
        messaging._client = AsyncMock()
        await messaging.send("!room:test", "Hello", formatted="<b>Hello</b>")
        content = messaging._client.room_send.call_args[1]["content"]
        assert content["format"] == "org.matrix.custom.html"
        assert content["formatted_body"] == "<b>Hello</b>"

    async def test_send_typing(self, messaging):
        messaging._client = AsyncMock()
        await messaging.send_typing("!room:test", typing=True, timeout=5000)
        messaging._client.room_typing.assert_called_once_with("!room:test", True, 5000)

    async def test_send_not_connected(self, messaging):
        from colaig.exceptions import MessagingError
        with pytest.raises(MessagingError, match="non connecté"):
            await messaging.send("!room:test", "Hello")


class TestMatrixMessagingMessageHandling:
    """Tests du traitement des messages reçus."""

    async def test_ignores_own_messages(self, messaging):
        """Le bot ignore ses propres messages."""
        messaging._client = AsyncMock()
        messaging._username = "@colaig:test.local"
        messaging._start_time = 0

        event = MagicMock()
        event.sender = "@colaig:test.local"  # c'est le bot lui-même
        event.body = "self message"
        event.event_id = "$1"
        event.server_timestamp = 99999999999

        handler = AsyncMock()
        messaging.on_message(handler)

        await messaging._on_room_message(MagicMock(), event)
        handler.assert_not_called()

    async def test_dispatches_to_callbacks(self, messaging):
        """Un message valide est dispatché aux callbacks."""
        import time
        messaging._client = AsyncMock()
        messaging._client.rooms = {}
        messaging._username = "@colaig:test.local"
        messaging._start_time = time.time() - 10

        # Message DM (pas besoin de mention)
        event = MagicMock()
        event.sender = "@user:test.local"
        event.body = "Bonjour"
        event.event_id = "$123"
        event.server_timestamp = time.time() * 1000
        event.source = {}

        room = MagicMock()
        room.room_id = "!dm:test"
        room.user_name.return_value = "User"

        # Mock _resolve_conversation_type pour retourner DM
        with patch.object(messaging, "_resolve_conversation_type", return_value=ConversationType.DM):
            handler = AsyncMock()
            messaging.on_message(handler)

            await messaging._on_room_message(room, event)

        handler.assert_called_once()
        msg = handler.call_args[0][0]
        assert isinstance(msg, IncomingMessage)
        assert msg.user_id == "@user:test.local"
        assert msg.body == "Bonjour"
        assert msg.conversation_type == ConversationType.DM


class _Stop(BaseException):
    """Sentinelle pour sortir de la boucle run() sans être attrapée par except Exception."""


class TestMatrixMessagingRun:
    """Tests de la boucle run() — pattern sync_forever."""

    def _make_whoami_ok(self):
        resp = MagicMock()
        resp.user_id = "@colaig:test.local"
        return resp

    async def test_run_calls_initial_sync_then_sync_forever(self, messaging):
        """run() fait un sync initial puis entre dans sync_forever."""
        messaging._client = AsyncMock()
        sync_forever_calls = []

        async def fake_sync_forever(**kwargs):
            sync_forever_calls.append(kwargs)
            raise _Stop

        messaging._client.sync_forever = fake_sync_forever

        with pytest.raises(_Stop):
            await messaging.run()

        messaging._client.sync.assert_called_once()  # sync initial
        assert len(sync_forever_calls) == 1
        assert sync_forever_calls[0]["timeout"] == 3000

    async def test_run_retries_after_sync_forever_exception(self, messaging):
        """Exception dans sync_forever → _handle_sync_failure() puis retry."""
        messaging._client = AsyncMock()
        messaging._handle_sync_failure = AsyncMock()
        call_count = 0

        async def fake_sync_forever(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("réseau coupé")
            raise _Stop

        messaging._client.sync_forever = fake_sync_forever

        with pytest.raises(_Stop):
            await messaging.run()

        messaging._handle_sync_failure.assert_called_once()

    async def test_run_initial_sync_failure_does_not_crash(self, messaging):
        """Exception dans le sync initial n'empêche pas la boucle de démarrer."""
        messaging._client = AsyncMock()
        messaging._client.sync = AsyncMock(side_effect=ConnectionError("timeout"))

        async def fake_sync_forever(**kwargs):
            raise _Stop

        messaging._client.sync_forever = fake_sync_forever

        with pytest.raises(_Stop):
            await messaging.run()  # ne doit pas lever avant _Stop


class TestMatrixMessagingHandleSyncFailure:
    """Tests de _handle_sync_failure() — vérification token + re-login."""

    async def test_sleeps_if_token_still_valid(self, messaging):
        """whoami() réussit → token valide → sleep 30s, pas de re-login."""
        messaging._client = AsyncMock()
        whoami_resp = MagicMock()
        whoami_resp.user_id = "@colaig:test.local"
        messaging._client.whoami = AsyncMock(return_value=whoami_resp)

        sleep_calls = []
        with patch("asyncio.sleep", side_effect=lambda t: sleep_calls.append(t)):
            await messaging._handle_sync_failure()

        assert sleep_calls == [30]
        messaging._client.login.assert_not_called()

    async def test_relogins_if_whoami_rejects_token(self, messaging):
        """whoami() retourne une réponse sans user_id → re-login."""
        from nio import LoginResponse
        messaging._client = AsyncMock()
        messaging._client.whoami = AsyncMock(return_value=MagicMock(spec=[]))  # pas de user_id

        mock_login_resp = MagicMock(spec=LoginResponse)
        mock_login_resp.access_token = "new_tok"
        mock_login_resp.device_id = "DEV1"
        mock_login_resp.user_id = "@colaig:test.local"
        messaging._client.login = AsyncMock(return_value=mock_login_resp)

        with patch("asyncio.sleep", return_value=None):
            await messaging._handle_sync_failure()

        messaging._client.login.assert_called_once()

    async def test_relogins_if_whoami_raises(self, messaging):
        """whoami() lève une exception → re-login."""
        from nio import LoginResponse
        messaging._client = AsyncMock()
        messaging._client.whoami = AsyncMock(side_effect=ConnectionError("réseau"))

        mock_login_resp = MagicMock(spec=LoginResponse)
        mock_login_resp.access_token = "new_tok"
        mock_login_resp.device_id = "DEV1"
        mock_login_resp.user_id = "@colaig:test.local"
        messaging._client.login = AsyncMock(return_value=mock_login_resp)

        with patch("asyncio.sleep", return_value=None):
            await messaging._handle_sync_failure()

        messaging._client.login.assert_called_once()

    async def test_sleeps_60s_if_relogin_fails(self, messaging):
        """whoami() échoue + re-login échoue → sleep 60s."""
        from colaig.exceptions import MessagingError
        messaging._client = AsyncMock()
        messaging._client.whoami = AsyncMock(side_effect=ConnectionError)
        messaging._client.login = AsyncMock(side_effect=MessagingError("login impossible"))

        sleep_calls = []
        with patch("asyncio.sleep", side_effect=lambda t: sleep_calls.append(t)):
            await messaging._handle_sync_failure()

        assert sleep_calls == [60]
