"""
Tests de la résolution des endpoints LLM/embeddings (découplage backend).

Vérifie que Config produit les bonnes URLs/clés/modèles :
- par défaut → comportement Albert historique (rétro-compatible)
- override llm_* → backend tiers (ex: Open WebUI SSPCloud), sans /v1 forcé
- embeddings dissociables du chat
"""
from app.config import Config


def _cfg(**kw):
    base = dict(
        albert_api_url="https://albert.api.etalab.gouv.fr/v1",
        albert_api_token="ALB",
        albert_model="m-alb",
        albert_model_embedding="bge",
    )
    base.update(kw)
    return Config(**base)


def test_default_albert_backward_compatible():
    c = _cfg()
    assert c.chat_completions_url == "https://albert.api.etalab.gouv.fr/v1/chat/completions"
    assert c.embeddings_url == "https://albert.api.etalab.gouv.fr/v1/embeddings"
    assert c.effective_llm_api_key == "ALB"
    assert c.effective_llm_model == "m-alb"
    assert c.effective_embeddings_model == "bge"


def test_albert_url_without_v1_suffix():
    # Si albert_api_url ne contient pas /v1, on l'ajoute proprement.
    c = _cfg(albert_api_url="https://albert.api.etalab.gouv.fr")
    assert c.chat_completions_url == "https://albert.api.etalab.gouv.fr/v1/chat/completions"


def test_sspcloud_openwebui_override():
    c = _cfg(
        llm_base_url="https://llm.lab.sspcloud.fr/api",
        llm_api_key="sk-x",
        llm_model="qwen3-6-35b-moe",
    )
    # Pas de /v1 forcé sur un backend tiers.
    assert c.chat_completions_url == "https://llm.lab.sspcloud.fr/api/chat/completions"
    assert c.effective_llm_api_key == "sk-x"
    assert c.effective_llm_model == "qwen3-6-35b-moe"
    # Embeddings retombent sur la base LLM si non configurés.
    assert c.embeddings_url == "https://llm.lab.sspcloud.fr/api/embeddings"
    assert c.effective_embeddings_api_key == "sk-x"


def test_trailing_slash_normalized():
    c = _cfg(llm_base_url="https://llm.lab.sspcloud.fr/ollama/v1/")
    assert c.chat_completions_url == "https://llm.lab.sspcloud.fr/ollama/v1/chat/completions"


def test_embeddings_separate_provider():
    # Chat sur SSPCloud, embeddings sur Albert.
    c = _cfg(
        llm_base_url="https://llm.lab.sspcloud.fr/ollama/v1",
        llm_api_key="sk-y",
        embeddings_base_url="https://albert.api.etalab.gouv.fr/v1",
        embeddings_model="bge-m3",
    )
    assert c.chat_completions_url == "https://llm.lab.sspcloud.fr/ollama/v1/chat/completions"
    assert c.embeddings_url == "https://albert.api.etalab.gouv.fr/v1/embeddings"
    assert c.effective_embeddings_model == "bge-m3"
    # Clé embeddings retombe sur la clé chat effective si non fournie.
    assert c.effective_embeddings_api_key == "sk-y"


def test_embeddings_explicit_key():
    c = _cfg(
        llm_base_url="https://llm.lab.sspcloud.fr/api",
        llm_api_key="sk-chat",
        embeddings_api_key="sk-emb",
    )
    assert c.effective_embeddings_api_key == "sk-emb"
