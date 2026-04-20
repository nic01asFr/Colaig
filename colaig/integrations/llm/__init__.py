"""
Colaig — Clients LLM (provider-agnostic)

Implémentations de LLMClientProtocol (alias AlbertClientProtocol) pour
différents backends LLM compatibles OpenAI.

Backends disponibles :
    albert      → AlbertClient (Albert API Etalab — défaut secteur public FR)
    openai      → OpenAIClient (OpenAI, Mistral, Groq, Together AI...)
    azure       → AzureClient  (Azure OpenAI Service)
    ollama      → OllamaClient (Ollama local — autohébergé)

Tous implémentent les mêmes méthodes : chat, chat_stream, embed, embed_batch,
chat_with_tools. Albert ajoute : rerank, ocr, transcribe (spécifiques à Albert).

Usage :
    from colaig.integrations.llm import create_llm_client
    client = create_llm_client(config)
"""

from colaig.integrations.albert import AlbertClient
from colaig.integrations.llm.openai_client import OpenAIClient
from colaig.integrations.llm.azure_client import AzureClient
from colaig.integrations.llm.ollama_client import OllamaClient
from colaig.integrations.llm.utils import normalize_tool_call_id

__all__ = ["AlbertClient", "OpenAIClient", "AzureClient", "OllamaClient", "normalize_tool_call_id"]
