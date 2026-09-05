# Capacités LLM — mesurées

**Livrable du lot L1.3.** Tout ce qui suit a été **mesuré** contre les endpoints réels
les 22 et 23 août 2026. Rien n'y est déduit d'une documentation ou d'un catalogue
annoncé : ce qui n'a pas été appelé est marqué **INCONNU**.

Régénération : `_chantier/scripts/probe_llm.py`, avec `COLAIG_LLM_BASE_URL` et
`COLAIG_LLM_API_KEY`.

---

## Les deux endpoints

| | SSPCloud — **cible de production (D3)** | Albert |
|---|---|---|
| Base | `https://llm.lab.sspcloud.fr/api` | `https://albert.api.etalab.gouv.fr` |
| Modèles servis | 7 | 10 |
| Chat | ✅ `qwen3-6-35b-moe` — 0,39 s | ✅ `openai/gpt-oss-120b` — 0,20 s |
| **Tool calling** | ✅ `tool_calls` bien formé — 1,19 s | ✅ — 0,41 s |
| Embeddings | `qwen3-embedding-8b` — **4096** | `bge-m3` — **1024** · `qwen3-vl-embedding-8b` — 4096 |
| Reranker | ❌ **aucun au catalogue** | ✅ `bge-reranker-v2-m3` — 0,12 s |
| OCR | ✅ `chandra-ocr-2` | ✅ `lightonocr-2-1b` |

> **La base ne porte jamais `/v1`.** Les clients construisent eux-mêmes
> `{base}/v1/chat/completions`. Un `/v1` dans la configuration produirait `/v1/v1/`.
>
> **Ne pas utiliser `https://llm.lab.sspcloud.fr/openai`** : l'appel devient
> `/openai/v1/chat/completions` et le serveur répond **403 — « Direct API passthrough is
> disabled »**. Le déploiement démarre puis échoue au premier appel. Mesuré.

## Tool calling — la boucle agent est réalisable

Vérifié sur les deux endpoints avec un outil `search_documents` : `tool_calls` présent,
bien formé, arguments JSON valides. **Le repli « JSON imposé par prompt » est écarté.**

Deux détails de parsing, constatés sur `qwen3-6-35b-moe` :

- `content` vaut **`null`** quand un outil est appelé. Un client qui suppose une chaîne
  plante.
- Le raisonnement arrive dans `reasoning_content` et
  `provider_specific_fields.reasoning`, en plus de `tool_calls`.

**Latence à surveiller :** 1,19 s par tour outillé sur SSPCloud contre 0,41 s sur Albert.
Sur une boucle à plusieurs itérations, c'est le poste dominant du budget de réponse —
très loin devant le stockage, mesuré à 31 ms.

## Embeddings — la dimension est un choix, pas une donnée

| dimensions | Spearman vs 4096 | index pour 1 059 chunks |
|---|---|---|
| 4096 | 1,0000 | 17 Mo |
| 2048 | 0,9819 | 9 Mo |
| **1024** | 0,9546 | **4 Mo** |
| 512 | 0,9376 | 2 Mo |

Le top-1 est correct **à toutes les dimensions**, y compris 512 — signe que
l'échantillon de contrôle (6 questions, 10 documents) est trop facile pour discriminer.
Le signal exploitable est le Spearman, qui se dégrade régulièrement : c'est le classement
**fin** qui souffre, et c'est lui qui compte quand des dizaines de chunks se ressemblent.

Le paramètre `dimensions` est **refusé** par la passerelle SSPCloud
(`litellm.UnsupportedParamsError`) : une réduction serait à faire côté client.

Décision **D10** : dimension paramétrable, défaut 1024, arbitrage suspendu à la référence
L1.5. **Sur la qualité, pas sur la mémoire** — l'écart 17 Mo / 4 Mo ne justifie rien.

## Reranker — un arbitrage ouvert

SSPCloud n'en sert **aucun**. Albert sert `bge-reranker-v2-m3`. Trois options — bi-provider,
MMR seul, reranker local — détaillées dans `_chantier/HYPOTHESES.md`. Aucune retenue par
défaut : c'est une décision, tranchée à L1.5.

## OCR — mesuré sur un PDF scanné réel

| modèle | s/page | mots | 5-grammes uniques |
|---|---|---|---|
| **`lightonocr-2-1b`** (Albert) | **1,6** | 654 | **99,4 %** |
| `chandra-ocr-2` (SSPCloud) | 6,9 | 1 047 | 93,4 % |
| `qwen3-vl` (SSPCloud) | 10,1 | 685 | — |

Les trois rendent du français plausible. Comparaison des deux principaux, sans lecture du
contenu : Jaccard **80,9 %**. `chandra` retrouve **98,4 %** du vocabulaire de
`lightonocr`, qui n'en retrouve que **82 %** du sien.

`lightonocr` est donc un **sous-ensemble quasi propre** — il omet plutôt qu'il n'invente.
`chandra` rend 80 mots de plus, avec 6,6 % de 5-grammes répétés contre 0,6 %.
**Impossible de dire sans vérité terrain si ces 80 mots sont du texte capté ou du bruit.**
Décision suspendue à L1.5.

## Capacités par backend — ce que le Protocol ne dit pas

`LLMClientProtocol` déclare cinq méthodes. Le code métier en appelle huit.

| capacité | au Protocol | albert | openai | azure | ollama |
|---|---|---|---|---|---|
| `chat`, `chat_stream`, `chat_with_tools`, `embed`, `embed_batch` | ✅ | ✅ | ✅ | ✅ | ✅ |
| `ocr` — `rag/indexer.py` | ❌ | ✅ | — | — | — |
| `rerank` — `rag/retriever.py` | ❌ | ✅ | ✅ | — | — |
| `transcribe` — `messaging/handlers.py` | ❌ | ✅ | ✅ | — | — |

Ces trois-là ne peuvent pas devenir obligatoires — exiger un OCR d'Ollama n'aurait pas de
sens. Leur absence se **demande** désormais, via `integrations/llm/capabilities.py`, au
lieu de se découvrir par un `AttributeError` au milieu d'une indexation.

## `priority` — une qualité de service à ne pas négliger

`chat`, `chat_stream` et `chat_with_tools` acceptent `priority` : `"user"` par défaut,
`"background"` pour l'OCR et l'indexation. Les appels de fond prennent un sémaphore
réduit, laissant toujours un créneau aux requêtes des usagers.

Un travail de fond qui s'annoncerait `"user"` affamerait les conversations **sans erreur
ni trace**. `FakeLLM` enregistre les priorités pour rendre ce défaut assertionnable.
