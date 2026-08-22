# Catalogue LLM — sonde du 2026-08-22 21:16

Endpoint : `https://llm.lab.sspcloud.fr/api/v1`

## 1. Modèles — `GET /models` → 200 (0.20s)

| modèle |
|---|
| `gemma4-26b-moe` |
| `qwen3-6-35b-moe` |
| `qwen3-vl` |
| `qwen3-embedding-8b` |
| `chandra-ocr-2` |
| `qwen3-8-27b` |
| `qwen3-cursor` |

## 2. Chat — modèle `qwen3-6-35b-moe`

`POST /chat/completions` → 200 (0.39s) — **OK**

## 3. Tool calling ⚠️

`tools=[...]` → 200 (1.19s) — tool_calls : **PRÉSENT**

```json
{
  "role": "assistant",
  "tool_calls": [
    {
      "function": {
        "arguments": "{\"query\": \"marchés publics\"}",
        "name": "search_documents"
      },
      "id": "chatcmpl-tool-ad058e1f219fcee4",
      "type": "function"
    }
  ],
  "reasoning_content": "The user is asking to search for documents related to \"marchés publics\" (public procurement).\nI need to use the `search_documents` tool with the query \"marchés publics\".\n\n1.  **Identify the tool**: `search_documents`.\n2.  **Identify the parameter**: `query` = \"marchés publics\".\n3.  **Construct the call**: `search_documents(query=\"marchés publics\")`.\n4.  **Execute**: Call the tool.\n5.  **Response**: Return the results or a summary based on the tool's output.\n\nThe user's request is in French, so the query should be in French.\n\nAction: Call `search_documents` with `query=\"marchés publics\"`.\n",
  "provider_specific_fields": {
    "reasoning": "The user is asking to search for documents related to \"marchés publics\" (public procurement).\nI need to use the `search_documents` tool with the query \"marchés publics\".\n\n1.  **Identify the tool**: `search_documents`.\n2.  **Identify the parameter
```

> **Si tool_calls est ABSENT :** la boucle agent ne peut pas fonctionner en
> mode natif. Replis possibles, par ordre de préférence : (a) un autre modèle
> du catalogue, (b) Albert API en provider dédié pour l'orchestration,
> (c) parsing d'un JSON structuré imposé par prompt — dégradé, à éviter.
> **Arrêter et arbitrer avant d'écrire du code.**

## 4. Embeddings — modèle `qwen3-embedding-8b`

→ 200 (0.28s) — **dimension = 4096**

## 5. Reranker (H2)

Aucun modèle de reranking dans le catalogue.

> **H2 non levée par le catalogue.** Vérifier côté Albert API. Si aucun
> reranker n'est disponible : le pipeline perd le gain le plus documenté
> des benchmarks 2026. Repli = MMR seul. **À inscrire comme dette dans
> AVANCEMENT.md, pas à découvrir au lot L4.1.**

## Verdict

- **H1** (chat + tool calling) : ✅ levée
- **H2** (reranker) : ❌ non levée
