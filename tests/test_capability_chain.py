"""
Tests — ProviderRegistry + CapabilityChain

Couvre :
- ProviderRegistry.from_env() : détection providers depuis os.environ
- ProviderRegistry.get_client() : création OpenAIClient par provider
- CapabilityChain.parse() : parsing spec "provider:model,provider:model"
- CapabilityChain.chat() : délégation + fallback sur rate limit / unavailable
- CapabilityChain.chat_stream() : fallback streaming
- CapabilityChain.chat_with_tools() : fallback tool calling
- CapabilityChain.embed() / embed_batch() : fallback embeddings
- CapabilityChain vide : is_empty, erreurs propres
- Intégration AlbertClient : chains construites depuis config
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from colaig.exceptions import LLMError, LLMRateLimitError, LLMUnavailableError
from colaig.integrations.llm.capability_chain import CapabilityChain
from colaig.integrations.llm.provider_registry import ProviderRegistry
from colaig.models import ChatCompletionResult, ToolCall


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_registry(**providers: tuple[str, str]) -> ProviderRegistry:
    """Crée un ProviderRegistry avec des providers fictifs."""
    return ProviderRegistry(dict(providers))


def _mock_client(name: str = "mock") -> MagicMock:
    """Crée un mock OpenAIClient."""
    client = MagicMock()
    client._backend = name
    return client


def _chain(*entries: tuple[MagicMock, str], cap: str = "test") -> CapabilityChain:
    """Crée une CapabilityChain avec des clients mockés."""
    return CapabilityChain(list(entries), capability=cap)


# ─── ProviderRegistry ────────────────────────────────────────────────────────


class TestProviderRegistry:
    def test_from_env_detects_albert(self, monkeypatch):
        monkeypatch.setenv("ALBERT_API_KEY", "sk-albert")
        monkeypatch.setenv("ALBERT_API_URL", "https://albert.example.com")
        # Masquer les autres providers
        for p in ["MISTRAL", "OPENAI", "GROQ", "TOGETHER"]:
            monkeypatch.delenv(f"{p}_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_API_URL", raising=False)

        registry = ProviderRegistry.from_env()
        assert registry.has("albert")
        assert not registry.has("mistral")

    def test_from_env_detects_mistral(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "sk-mistral")
        for p in ["ALBERT", "OPENAI", "GROQ", "TOGETHER"]:
            monkeypatch.delenv(f"{p}_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_API_URL", raising=False)

        registry = ProviderRegistry.from_env()
        assert registry.has("mistral")
        assert not registry.has("albert")

    def test_from_env_ollama_no_key_needed(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_URL", "http://localhost:11434")
        for p in ["ALBERT", "MISTRAL", "OPENAI", "GROQ", "TOGETHER"]:
            monkeypatch.delenv(f"{p}_API_KEY", raising=False)

        registry = ProviderRegistry.from_env()
        assert registry.has("ollama")

    def test_from_env_default_url_used_when_no_url_var(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "sk-mistral")
        monkeypatch.delenv("MISTRAL_API_URL", raising=False)
        for p in ["ALBERT", "OPENAI", "GROQ", "TOGETHER"]:
            monkeypatch.delenv(f"{p}_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_API_URL", raising=False)

        registry = ProviderRegistry.from_env()
        client = registry.get_client("mistral", model_chat="mistral-small")
        assert client is not None
        assert "api.mistral.ai" in client._base_url

    def test_from_env_custom_url_override(self, monkeypatch):
        monkeypatch.setenv("MISTRAL_API_KEY", "sk-mistral")
        monkeypatch.setenv("MISTRAL_API_URL", "https://my-proxy.example.com/mistral")
        for p in ["ALBERT", "OPENAI", "GROQ", "TOGETHER"]:
            monkeypatch.delenv(f"{p}_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_API_URL", raising=False)

        registry = ProviderRegistry.from_env()
        client = registry.get_client("mistral", model_chat="mistral-small")
        assert client is not None
        assert "my-proxy.example.com" in client._base_url

    def test_get_client_unknown_provider_returns_none(self):
        registry = _make_registry()
        assert registry.get_client("unknown") is None

    def test_get_client_creates_openai_client(self):
        registry = _make_registry(
            albert=("https://albert.example.com", "sk-albert"),
        )
        client = registry.get_client("albert", model_chat="gpt-oss-120b", model_embed="bge-m3")
        assert client is not None
        assert client._model_chat == "gpt-oss-120b"
        assert client._model_embed == "bge-m3"
        assert client._backend == "albert"

    def test_get_client_each_call_creates_new_instance(self):
        registry = _make_registry(
            mistral=("https://api.mistral.ai", "sk-mistral"),
        )
        c1 = registry.get_client("mistral", model_chat="small")
        c2 = registry.get_client("mistral", model_chat="large")
        assert c1 is not c2
        assert c1._model_chat == "small"
        assert c2._model_chat == "large"

    def test_available_lists_configured_providers(self):
        registry = _make_registry(
            albert=("https://albert.example.com", "sk-a"),
            mistral=("https://api.mistral.ai", "sk-m"),
        )
        available = registry.available()
        assert set(available) == {"albert", "mistral"}

    def test_multiple_providers_detected(self, monkeypatch):
        monkeypatch.setenv("ALBERT_API_KEY", "sk-albert")
        monkeypatch.setenv("MISTRAL_API_KEY", "sk-mistral")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setenv("GROQ_API_KEY", "sk-groq")
        monkeypatch.delenv("TOGETHER_API_KEY", raising=False)
        monkeypatch.delenv("OLLAMA_API_URL", raising=False)

        registry = ProviderRegistry.from_env()
        assert registry.has("albert")
        assert registry.has("mistral")
        assert registry.has("openai")
        assert registry.has("groq")
        assert not registry.has("together")


# ─── CapabilityChain.parse() ─────────────────────────────────────────────────


class TestCapabilityChainParse:
    def test_empty_spec_returns_empty_chain(self):
        registry = _make_registry()
        chain = CapabilityChain.parse("", registry, "chat")
        assert chain.is_empty

    def test_whitespace_spec_returns_empty_chain(self):
        registry = _make_registry()
        chain = CapabilityChain.parse("   ", registry, "chat")
        assert chain.is_empty

    def test_single_provider_model(self):
        registry = _make_registry(
            albert=("https://albert.example.com", "sk-a"),
        )
        chain = CapabilityChain.parse("albert:gpt-oss-120b", registry, "chat")
        assert not chain.is_empty
        assert len(chain._entries) == 1
        client, model = chain._entries[0]
        assert model == "gpt-oss-120b"
        assert client._backend == "albert"

    def test_two_providers_fallback_chain(self):
        registry = _make_registry(
            albert=("https://albert.example.com", "sk-a"),
            mistral=("https://api.mistral.ai", "sk-m"),
        )
        chain = CapabilityChain.parse(
            "albert:gpt-oss-120b,mistral:mistral-large-latest", registry, "chat"
        )
        assert len(chain._entries) == 2
        assert chain._entries[0][1] == "gpt-oss-120b"
        assert chain._entries[1][1] == "mistral-large-latest"

    def test_unknown_provider_skipped(self):
        registry = _make_registry(
            albert=("https://albert.example.com", "sk-a"),
        )
        chain = CapabilityChain.parse(
            "ghost:model,albert:gpt-oss-120b", registry, "chat"
        )
        assert len(chain._entries) == 1
        assert chain._entries[0][0]._backend == "albert"

    def test_all_unknown_providers_gives_empty_chain(self):
        registry = _make_registry()
        chain = CapabilityChain.parse("ghost:model,phantom:other", registry, "chat")
        assert chain.is_empty

    def test_provider_without_model_uses_default(self):
        registry = _make_registry(
            albert=("https://albert.example.com", "sk-a"),
        )
        chain = CapabilityChain.parse(
            "albert", registry, "chat", default_model="gpt-oss-120b"
        )
        assert chain._entries[0][1] == "gpt-oss-120b"

    def test_model_with_slash_parsed_correctly(self):
        registry = _make_registry(
            albert=("https://albert.example.com", "sk-a"),
        )
        chain = CapabilityChain.parse(
            "albert:mistralai/Mistral-Small-3.2-24B-Instruct-2506", registry, "ocr"
        )
        assert chain._entries[0][1] == "mistralai/Mistral-Small-3.2-24B-Instruct-2506"

    def test_extra_whitespace_stripped(self):
        registry = _make_registry(
            albert=("https://albert.example.com", "sk-a"),
            mistral=("https://api.mistral.ai", "sk-m"),
        )
        chain = CapabilityChain.parse(
            "  albert : gpt-oss-120b ,  mistral : mistral-large  ", registry, "chat"
        )
        assert len(chain._entries) == 2


# ─── CapabilityChain.chat() ──────────────────────────────────────────────────


class TestCapabilityChainChat:
    @pytest.mark.asyncio
    async def test_single_provider_success(self):
        client = _mock_client("albert")
        client.chat = AsyncMock(return_value="Bonjour")
        chain = _chain((client, "gpt-oss"), cap="chat")

        result = await chain.chat([{"role": "user", "content": "Hi"}])
        assert result == "Bonjour"
        client.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_on_rate_limit(self):
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(side_effect=LLMRateLimitError("429"))
        c2 = _mock_client("mistral")
        c2.chat = AsyncMock(return_value="Fallback OK")
        chain = _chain((c1, "gpt-oss"), (c2, "mistral-large"), cap="chat")

        result = await chain.chat([{"role": "user", "content": "Hi"}])
        assert result == "Fallback OK"
        c1.chat.assert_called_once()
        c2.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_on_unavailable(self):
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(side_effect=LLMUnavailableError("503"))
        c2 = _mock_client("mistral")
        c2.chat = AsyncMock(return_value="OK")
        chain = _chain((c1, "m1"), (c2, "m2"), cap="chat")

        result = await chain.chat([])
        assert result == "OK"

    @pytest.mark.asyncio
    async def test_no_fallback_on_functional_error(self):
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(side_effect=LLMError("Bad payload"))
        c2 = _mock_client("mistral")
        c2.chat = AsyncMock(return_value="Should not reach")
        chain = _chain((c1, "m1"), (c2, "m2"), cap="chat")

        with pytest.raises(LLMError, match="Bad payload"):
            await chain.chat([])
        c2.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_all_fail_raises_last_error(self):
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(side_effect=LLMRateLimitError("429 albert"))
        c2 = _mock_client("mistral")
        c2.chat = AsyncMock(side_effect=LLMRateLimitError("429 mistral"))
        chain = _chain((c1, "m1"), (c2, "m2"), cap="chat")

        with pytest.raises(LLMRateLimitError, match="429 mistral"):
            await chain.chat([])

    @pytest.mark.asyncio
    async def test_empty_chain_raises_llm_error(self):
        chain = CapabilityChain([], capability="chat")
        with pytest.raises(LLMError, match="aucun provider"):
            await chain.chat([])

    @pytest.mark.asyncio
    async def test_per_provider_model_wins_over_caller_model(self):
        """Le modèle configuré par provider (spec) a priorité sur le model appelant.

        Garantit que chaque provider reçoit son propre modèle même quand un agent
        passe model=config.albert_model_chat — évite qu'un fallback Mistral reçoive
        un modèle Albert invalide (ex: openai/gpt-oss-120b).
        """
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(return_value="OK")
        chain = _chain((c1, "per-provider-model"), cap="chat")

        await chain.chat([], model="caller-override")
        call_kwargs = c1.chat.call_args
        used_model = call_kwargs.kwargs.get("model") or (call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
        assert used_model == "per-provider-model"

    @pytest.mark.asyncio
    async def test_default_model_used_when_no_override(self):
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(return_value="OK")
        chain = _chain((c1, "chain-model"), cap="chat")

        await chain.chat([], model=None)
        call_args = c1.chat.call_args
        # model passé = "chain-model" (default_model de l'entrée)
        assert "chain-model" in str(call_args)

    @pytest.mark.asyncio
    async def test_priority_forwarded(self):
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(return_value="OK")
        chain = _chain((c1, "m"), cap="chat")

        await chain.chat([], priority="background")
        call_args = c1.chat.call_args
        assert "background" in str(call_args)


# ─── CapabilityChain.chat_stream() ───────────────────────────────────────────


class TestCapabilityChainStream:
    @pytest.mark.asyncio
    async def test_stream_success(self):
        async def _gen(*a, **kw):
            for chunk in ["Hello", " ", "world"]:
                yield chunk

        c1 = _mock_client("albert")
        c1.chat_stream = _gen
        chain = _chain((c1, "m"), cap="chat")

        chunks = []
        async for chunk in chain.chat_stream([]):
            chunks.append(chunk)
        assert chunks == ["Hello", " ", "world"]

    @pytest.mark.asyncio
    async def test_stream_fallback_on_rate_limit(self):
        async def _fail(*a, **kw):
            raise LLMRateLimitError("429")
            yield  # make it a generator

        async def _ok(*a, **kw):
            yield "fallback"

        c1 = _mock_client("albert")
        c1.chat_stream = _fail
        c2 = _mock_client("mistral")
        c2.chat_stream = _ok
        chain = _chain((c1, "m1"), (c2, "m2"), cap="stream")

        chunks = []
        async for chunk in chain.chat_stream([]):
            chunks.append(chunk)
        assert chunks == ["fallback"]

    @pytest.mark.asyncio
    async def test_empty_stream_chain_raises(self):
        chain = CapabilityChain([], capability="stream")
        with pytest.raises(LLMError):
            async for _ in chain.chat_stream([]):
                pass


# ─── CapabilityChain.chat_with_tools() ───────────────────────────────────────


class TestCapabilityChainTools:
    @pytest.mark.asyncio
    async def test_tools_success(self):
        result = ChatCompletionResult(content="OK", tool_calls=[], finish_reason="stop")
        c1 = _mock_client("albert")
        c1.chat_with_tools = AsyncMock(return_value=result)
        chain = _chain((c1, "m"), cap="tools")

        out = await chain.chat_with_tools([], tools=[])
        assert out.content == "OK"

    @pytest.mark.asyncio
    async def test_tools_fallback_on_unavailable(self):
        r = ChatCompletionResult(content="fallback", tool_calls=[], finish_reason="stop")
        c1 = _mock_client("albert")
        c1.chat_with_tools = AsyncMock(side_effect=LLMUnavailableError("503"))
        c2 = _mock_client("mistral")
        c2.chat_with_tools = AsyncMock(return_value=r)
        chain = _chain((c1, "m1"), (c2, "m2"), cap="tools")

        out = await chain.chat_with_tools([], tools=[])
        assert out.content == "fallback"

    @pytest.mark.asyncio
    async def test_tools_with_tool_calls(self):
        tc = ToolCall(tool_name="search", arguments={"q": "test"}, call_id="1")
        result = ChatCompletionResult(content="", tool_calls=[tc], finish_reason="tool_calls")
        c1 = _mock_client("albert")
        c1.chat_with_tools = AsyncMock(return_value=result)
        chain = _chain((c1, "m"), cap="tools")

        out = await chain.chat_with_tools([], tools=[{"name": "search"}])
        assert len(out.tool_calls) == 1
        assert out.tool_calls[0].tool_name == "search"


# ─── CapabilityChain.embed() / embed_batch() ─────────────────────────────────


class TestCapabilityChainEmbed:
    @pytest.mark.asyncio
    async def test_embed_success(self):
        c1 = _mock_client("albert")
        c1.embed = AsyncMock(return_value=[0.1, 0.2, 0.3])
        chain = _chain((c1, "bge-m3"), cap="embed")

        result = await chain.embed("test text")
        assert result == [0.1, 0.2, 0.3]

    @pytest.mark.asyncio
    async def test_embed_fallback_on_rate_limit(self):
        c1 = _mock_client("albert")
        c1.embed = AsyncMock(side_effect=LLMRateLimitError("429"))
        c2 = _mock_client("openai")
        c2.embed = AsyncMock(return_value=[0.4, 0.5])
        chain = _chain((c1, "m1"), (c2, "m2"), cap="embed")

        result = await chain.embed("text")
        assert result == [0.4, 0.5]

    @pytest.mark.asyncio
    async def test_embed_batch_success(self):
        c1 = _mock_client("albert")
        c1.embed_batch = AsyncMock(return_value=[[0.1], [0.2]])
        chain = _chain((c1, "bge-m3"), cap="embed")

        result = await chain.embed_batch(["a", "b"])
        assert result == [[0.1], [0.2]]

    @pytest.mark.asyncio
    async def test_embed_batch_fallback(self):
        c1 = _mock_client("albert")
        c1.embed_batch = AsyncMock(side_effect=LLMUnavailableError("503"))
        c2 = _mock_client("openai")
        c2.embed_batch = AsyncMock(return_value=[[0.9]])
        chain = _chain((c1, "m1"), (c2, "m2"), cap="embed")

        result = await chain.embed_batch(["x"])
        assert result == [[0.9]]

    @pytest.mark.asyncio
    async def test_embed_all_fail_raises(self):
        c1 = _mock_client("albert")
        c1.embed = AsyncMock(side_effect=LLMRateLimitError("429"))
        chain = _chain((c1, "m"), cap="embed")

        with pytest.raises(LLMRateLimitError):
            await chain.embed("text")

    @pytest.mark.asyncio
    async def test_embed_empty_chain_raises(self):
        chain = CapabilityChain([], capability="embed")
        with pytest.raises(LLMError, match="aucun provider"):
            await chain.embed("text")


# ─── Intégration AlbertClient ────────────────────────────────────────────────


class TestAlbertClientChainIntegration:
    """Vérifie que AlbertClient construit et utilise les chains correctement."""

    def _make_albert(self, env: dict | None = None) -> object:
        """Crée un AlbertClient avec un environnement LLM contrôlé.

        Les cap_* sont extraits de `env` et injectés dans ColaigConfig pour que
        AlbertClient.__init__ construise les chains correctement. ProviderRegistry
        est isolé via patch.dict pour ne voir que les providers déclarés dans `env`.
        """
        from colaig.models import ColaigConfig
        from colaig.integrations.albert import AlbertClient

        env = env or {}
        # Environnement minimal : masquer les vraies clés API, n'exposer que env
        base_env = {k: "" for k in [
            "ALBERT_API_KEY", "MISTRAL_API_KEY", "OPENAI_API_KEY",
            "GROQ_API_KEY", "TOGETHER_API_KEY",
        ]}
        base_env.update(env)

        config = ColaigConfig(
            albert_api_url="https://albert.example.com",
            albert_api_key="sk-albert",
            albert_model_chat="gpt-oss-120b",
            albert_model_embed="bge-m3",
            albert_model_ocr="mistral-ocr",
            cap_chat=env.get("COLAIG_CAP_CHAT", ""),
            cap_embed=env.get("COLAIG_CAP_EMBED", ""),
            cap_ocr=env.get("COLAIG_CAP_OCR", ""),
            cap_rerank=env.get("COLAIG_CAP_RERANK", ""),
            cap_audio=env.get("COLAIG_CAP_AUDIO", ""),
        )
        with patch.dict(os.environ, base_env):
            return AlbertClient(config)

    def test_no_cap_env_all_chains_empty(self):
        """Sans COLAIG_CAP_*, toutes les chains sont vides → Albert direct."""
        albert = self._make_albert()
        assert albert._chat_chain.is_empty
        assert albert._embed_chain.is_empty
        assert albert._ocr_chain.is_empty

    def test_cap_chat_builds_chain(self):
        """COLAIG_CAP_CHAT=albert:model → _chat_chain non vide."""
        albert = self._make_albert(env={
            "COLAIG_CAP_CHAT": "albert:openai/gpt-oss-120b",
            "ALBERT_API_KEY": "sk-albert",
        })
        assert not albert._chat_chain.is_empty
        assert len(albert._chat_chain._entries) == 1

    def test_cap_ocr_builds_chain_with_mistral(self):
        """COLAIG_CAP_OCR=mistral:model → _ocr_chain avec Mistral."""
        albert = self._make_albert(env={
            "COLAIG_CAP_OCR": "mistral:mistral-small-latest",
            "MISTRAL_API_KEY": "sk-mistral",
            "ALBERT_API_KEY": "sk-albert",
        })
        assert not albert._ocr_chain.is_empty
        client, model = albert._ocr_chain._entries[0]
        assert client._backend == "mistral"
        assert model == "mistral-small-latest"

    def test_cap_ocr_fallback_chain(self):
        """COLAIG_CAP_OCR=mistral:m1,albert:m2 → chain à 2 entrées."""
        albert = self._make_albert(env={
            "COLAIG_CAP_OCR": "mistral:mistral-small,albert:mistral-albert",
            "MISTRAL_API_KEY": "sk-mistral",
            "ALBERT_API_KEY": "sk-albert",
        })
        assert len(albert._ocr_chain._entries) == 2
        assert albert._ocr_chain._entries[0][0]._backend == "mistral"
        assert albert._ocr_chain._entries[1][0]._backend == "albert"

    @pytest.mark.asyncio
    async def test_albert_chat_delegates_to_chain(self):
        """AlbertClient.chat() délègue à _chat_chain si non vide."""
        client = self._make_albert(env={
            "COLAIG_CAP_CHAT": "albert:gpt-oss-120b",
            "ALBERT_API_KEY": "sk-albert",
        })
        # Remplace la méthode chat de la chain par un mock
        client._chat_chain.chat = AsyncMock(return_value="chain response")

        result = await client.chat([{"role": "user", "content": "Hi"}])
        assert result == "chain response"
        client._chat_chain.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_albert_embed_delegates_to_chain(self):
        """AlbertClient.embed() délègue à _embed_chain si non vide."""
        client = self._make_albert(env={
            "COLAIG_CAP_EMBED": "albert:BAAI/bge-m3",
            "ALBERT_API_KEY": "sk-albert",
        })
        client._embed_chain.embed = AsyncMock(return_value=[0.1, 0.2])

        result = await client.embed("test")
        assert result == [0.1, 0.2]
        client._embed_chain.embed.assert_called_once_with("test")


# ─── Scénarios réalistes ─────────────────────────────────────────────────────


class TestRealWorldScenarios:
    @pytest.mark.asyncio
    async def test_ocr_mistral_primary_albert_fallback(self):
        """Scénario : OCR → Mistral (429) → fallback Albert → succès."""
        c_mistral = _mock_client("mistral")
        c_mistral.chat = AsyncMock(side_effect=LLMRateLimitError("Mistral 429"))
        c_albert = _mock_client("albert")
        c_albert.chat = AsyncMock(return_value="OCR text from Albert")
        chain = _chain((c_mistral, "mistral-small"), (c_albert, "mistral-albert"), cap="ocr")

        result = await chain.chat([{"role": "user", "content": "OCR this"}], priority="background")
        assert result == "OCR text from Albert"
        c_mistral.chat.assert_called_once()
        c_albert.chat.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_all_providers_down_raises(self):
        """Tous les providers down → LLMRateLimitError propagée."""
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(side_effect=LLMRateLimitError("Albert 429"))
        c2 = _mock_client("mistral")
        c2.chat = AsyncMock(side_effect=LLMRateLimitError("Mistral 429"))
        c3 = _mock_client("openai")
        c3.chat = AsyncMock(side_effect=LLMUnavailableError("OpenAI 503"))
        chain = _chain((c1, "m1"), (c2, "m2"), (c3, "m3"), cap="chat")

        with pytest.raises((LLMRateLimitError, LLMUnavailableError)):
            await chain.chat([])

    @pytest.mark.asyncio
    async def test_three_provider_chain_success_on_third(self):
        """Scénario : 3 providers, les 2 premiers échouent, le 3e réussit."""
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(side_effect=LLMRateLimitError("429"))
        c2 = _mock_client("mistral")
        c2.chat = AsyncMock(side_effect=LLMUnavailableError("503"))
        c3 = _mock_client("groq")
        c3.chat = AsyncMock(return_value="Groq to the rescue")
        chain = _chain((c1, "m1"), (c2, "m2"), (c3, "m3"), cap="chat")

        result = await chain.chat([])
        assert result == "Groq to the rescue"
        assert c1.chat.call_count == 1
        assert c2.chat.call_count == 1
        assert c3.chat.call_count == 1


# ─── Circuit breaker ──────────────────────────────────────────────────────────


class TestCircuitBreaker:
    """Vérifie que le circuit breaker évite les appels infructueux en cooldown."""

    @pytest.mark.asyncio
    async def test_provider_on_cooldown_skipped(self):
        """Après un 429, le provider est en cooldown → sauté à l'appel suivant."""
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(side_effect=[LLMRateLimitError("429"), "never"])
        c2 = _mock_client("mistral")
        c2.chat = AsyncMock(return_value="mistral OK")
        chain = CapabilityChain([(c1, "m1"), (c2, "m2")], capability="chat", cooldown_s=60.0)

        # 1er appel : albert 429 → cooldown → mistral OK
        r1 = await chain.chat([])
        assert r1 == "mistral OK"
        assert c1.chat.call_count == 1

        # 2e appel : albert est en cooldown → sauté directement → mistral OK
        c2.chat = AsyncMock(return_value="mistral 2nd")
        r2 = await chain.chat([])
        assert r2 == "mistral 2nd"
        assert c1.chat.call_count == 1  # albert n'a PAS été rappelé

    @pytest.mark.asyncio
    async def test_cooldown_cleared_on_success(self):
        """Quand un provider récupère (succès), son cooldown est effacé."""
        c1 = _mock_client("albert")
        # 1er appel : 429, 2e appel : OK (provider récupéré)
        c1.chat = AsyncMock(side_effect=[LLMRateLimitError("429"), "albert back"])
        c2 = _mock_client("mistral")
        c2.chat = AsyncMock(return_value="mistral OK")
        chain = CapabilityChain([(c1, "m1"), (c2, "m2")], capability="chat", cooldown_s=0.0)

        # Cooldown = 0s → expiré immédiatement
        await chain.chat([])  # albert fail → mistral OK
        r2 = await chain.chat([])  # albert OK (cooldown expiré) → cooldown effacé
        assert r2 == "albert back"
        assert chain._cooldowns.get(id(c1)) is None  # cooldown effacé

    @pytest.mark.asyncio
    async def test_all_on_cooldown_raises_immediately(self):
        """Si tous les providers sont en cooldown → lève LLMError sans appel HTTP."""
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(side_effect=LLMRateLimitError("429"))
        c2 = _mock_client("mistral")
        c2.chat = AsyncMock(side_effect=LLMRateLimitError("429"))
        chain = CapabilityChain([(c1, "m1"), (c2, "m2")], capability="chat", cooldown_s=60.0)

        # 1er appel : les deux 429 → tous en cooldown
        with pytest.raises(LLMRateLimitError):
            await chain.chat([])

        # 2e appel : tous en cooldown → lève immédiatement sans appel HTTP
        c1.chat.reset_mock()
        c2.chat.reset_mock()
        with pytest.raises(LLMError):
            await chain.chat([])

        # Aucun appel HTTP n'a été fait pendant le cooldown
        c1.chat.assert_not_called()
        c2.chat.assert_not_called()

    @pytest.mark.asyncio
    async def test_cooldown_skips_also_for_embed(self):
        """Circuit breaker fonctionne aussi pour embed."""
        c1 = _mock_client("albert")
        c1.embed = AsyncMock(side_effect=LLMRateLimitError("429"))
        c2 = _mock_client("mistral")
        c2.embed = AsyncMock(return_value=[0.1, 0.2])
        chain = CapabilityChain([(c1, "m1"), (c2, "m2")], capability="embed", cooldown_s=60.0)

        await chain.embed("text")
        assert c1.embed.call_count == 1

        # 2e appel : c1 en cooldown → sauté
        c2.embed = AsyncMock(return_value=[0.3, 0.4])
        await chain.embed("text2")
        assert c1.embed.call_count == 1  # pas rappelé

    @pytest.mark.asyncio
    async def test_unavailable_also_triggers_cooldown(self):
        """LLMUnavailableError déclenche aussi le cooldown."""
        c1 = _mock_client("albert")
        c1.chat = AsyncMock(side_effect=LLMUnavailableError("503"))
        c2 = _mock_client("mistral")
        c2.chat = AsyncMock(return_value="OK")
        chain = CapabilityChain([(c1, "m1"), (c2, "m2")], capability="chat", cooldown_s=60.0)

        await chain.chat([])
        assert id(c1) in chain._cooldowns  # cooldown activé

    def test_circuit_breaker_state_visible(self):
        """Les cooldowns sont accessibles depuis _cooldowns."""
        c1 = _mock_client("albert")
        chain = CapabilityChain([(c1, "m1")], capability="chat", cooldown_s=30.0)
        assert not chain._is_on_cooldown(c1)
        chain._set_cooldown(c1)
        assert chain._is_on_cooldown(c1)
        chain._clear_cooldown(c1)
        assert not chain._is_on_cooldown(c1)
