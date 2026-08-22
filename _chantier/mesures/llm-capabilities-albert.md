# Catalogue LLM — sonde du 2026-08-22 21:08

Endpoint : `https://albert.api.etalab.gouv.fr/v1`

## 1. Modèles — `GET /models` → 200 (0.11s)

| modèle |
|---|
| `openai/gpt-oss-120b` |
| `qwen3-coder-30b-A3b-instruct` |
| `ministral-3-8b-instruct-2512` |
| `bge-m3` |
| `bge-reranker-v2-m3` |
| `mistral-small-3-2-24b-instruct-2506` |
| `whisper-large-v3` |
| `lightonocr-2-1b` |
| `deepseek-v4-flash` |
| `qwen3-vl-embedding-8b` |

## 2. Chat — modèle `openai/gpt-oss-120b`

`POST /chat/completions` → 200 (0.17s) — **OK**

## 3. Tool calling ⚠️

`tools=[...]` → 200 (0.41s) — tool_calls : **PRÉSENT**

```json
{
  "role": "assistant",
  "content": null,
  "refusal": null,
  "annotations": null,
  "audio": null,
  "function_call": null,
  "tool_calls": [
    {
      "id": "chatcmpl-tool-a9a25c9044caae68",
      "type": "function",
      "function": {
        "name": "search_documents",
        "arguments": "{\"query\": \"march\\u00e9s publics\"}"
      }
    }
  ],
  "reasoning": "The user wants to search documents about \"marchés publics\". We have a function search_documents. We'll call it with query \"marchés publics\"."
}
```

> **Si tool_calls est ABSENT :** la boucle agent ne peut pas fonctionner en
> mode natif. Replis possibles, par ordre de préférence : (a) un autre modèle
> du catalogue, (b) Albert API en provider dédié pour l'orchestration,
> (c) parsing d'un JSON structuré imposé par prompt — dégradé, à éviter.
> **Arrêter et arbitrer avant d'écrire du code.**

## 4. Embeddings — modèle `qwen3-vl-embedding-8b`

→ 200 (0.19s) — **dimension = 4096**

## 5. Reranker (H2)

`POST /rerank` modèle `bge-reranker-v2-m3` → 200 (0.11s)

```
{"object": "list", "id": "request-0f270e09953649ef96ceccaec7bee35c", "results": [{"relevance_score": 0.009859855, "index": 0}, {"relevance_score": 1.6187581e-05, "index": 1}], "model": "bge-reranker-v2-m3", "usage": {"prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10, "cost": 0.0, "impacts": {"kWh": 0.0, "kgCO2eq": 0.0}}}
```

## Verdict

- **H1** (chat + tool calling) : ✅ levée
- **H2** (reranker) : ✅ levée
