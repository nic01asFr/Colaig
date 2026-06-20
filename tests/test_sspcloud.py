"""
Tests de l'intégration SSPCloud : auto-récupération de la clé LLM depuis le
Secret K8s `*-secretassistant` et bootstrap de la config.
"""
import base64
import json
import os

import app.platform.sspcloud as sspcloud
from app.platform.sspcloud import extract_llm_key_from_secrets, bootstrap_llm_config
from app.config import Config


def _secret(name, cfg_dict):
    raw = base64.b64encode(json.dumps(cfg_dict).encode()).decode()
    return {"metadata": {"name": name}, "data": {"config.json": raw}}


def test_extract_key_found():
    items = [
        {"metadata": {"name": "autre"}, "data": {"x": "y"}},
        _secret("colaig-secretassistant", {"api_keys": {"OPENAI_API_KEY": "sk-abc"}}),
    ]
    assert extract_llm_key_from_secrets(items) == "sk-abc"


def test_extract_key_absent_secret():
    items = [{"metadata": {"name": "colaig-config"}, "data": {"config.json": "e30="}}]
    assert extract_llm_key_from_secrets(items) is None


def test_extract_key_empty_value():
    # Secret présent mais clé non renseignée → None (pas de chaîne vide).
    items = [_secret("x-secretassistant", {"api_keys": {"OPENAI_API_KEY": ""}})]
    assert extract_llm_key_from_secrets(items) is None


def test_extract_key_malformed_json():
    items = [{"metadata": {"name": "x-secretassistant"}, "data": {"config.json": "!!notbase64!!"}}]
    assert extract_llm_key_from_secrets(items) is None


def test_extract_key_empty_list():
    assert extract_llm_key_from_secrets([]) is None


def _cfg(**kw):
    base = dict(albert_api_token="", llm_api_key="", llm_base_url="", albert_model="m")
    base.update(kw)
    return Config(**base)


async def test_bootstrap_applies_key(monkeypatch):
    async def fake_read():
        return "sk-from-onyxia"

    monkeypatch.setattr(sspcloud, "read_sspcloud_llm_key", fake_read)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_BASE_URL", raising=False)

    cfg = _cfg()
    applied = await bootstrap_llm_config(cfg)

    assert applied is True
    assert cfg.llm_api_key == "sk-from-onyxia"
    assert cfg.llm_base_url == sspcloud.DEFAULT_SSPCLOUD_LLM_BASE_URL
    # Exporté dans l'env pour les sous-process (BotRunner).
    assert os.environ["LLM_API_KEY"] == "sk-from-onyxia"
    assert os.environ["LLM_BASE_URL"] == sspcloud.DEFAULT_SSPCLOUD_LLM_BASE_URL


async def test_bootstrap_skips_when_key_already_set(monkeypatch):
    called = {"n": 0}

    async def fake_read():
        called["n"] += 1
        return "sk-should-not-be-used"

    monkeypatch.setattr(sspcloud, "read_sspcloud_llm_key", fake_read)

    cfg = _cfg(llm_api_key="sk-explicit")
    applied = await bootstrap_llm_config(cfg)

    assert applied is False
    assert cfg.llm_api_key == "sk-explicit"
    assert called["n"] == 0  # auto-découverte court-circuitée


async def test_bootstrap_skips_when_albert_token_set(monkeypatch):
    async def fake_read():
        return "sk-x"

    monkeypatch.setattr(sspcloud, "read_sspcloud_llm_key", fake_read)
    cfg = _cfg(albert_api_token="ALB")
    assert await bootstrap_llm_config(cfg) is False


async def test_bootstrap_no_key_found(monkeypatch):
    async def fake_read():
        return None

    monkeypatch.setattr(sspcloud, "read_sspcloud_llm_key", fake_read)
    cfg = _cfg()
    assert await bootstrap_llm_config(cfg) is False
