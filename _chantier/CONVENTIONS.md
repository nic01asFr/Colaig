# Conventions de code et de travail

## En-tête de fichier — obligatoire

Convention reprise de `mcp_server_colaig_v2` (mars 2025), la seule génération du projet
qui ait formalisé le suivi d'implémentation. Elle permet à un agent de reprendre un
fichier sans relire le module entier.

```python
"""
<description courte du module>

STATUT: NON_IMPLEMENTE | PARTIEL | COMPLET | TESTE
VERSION: AAAA-MM-JJ - vX.Y
LOT: Lx.y
"""
```

`STATUT` :
- `NON_IMPLEMENTE` — fichier créé, fonctions vides ou `...`
- `PARTIEL` — implémenté avec des `TODO` restants
- `COMPLET` — implémenté, non couvert par des tests
- `TESTE` — implémenté **et** couvert, critère de fin du lot atteint

## TODO priorisés

```python
# TODO-CRITIQUE: bloque la fonctionnalité
# TODO-HAUTE:    nécessaire avant production
# TODO-NORMALE:  cycle normal
# TODO-BASSE:    amélioration future
# FIXME:         problème connu
# NOTE:          information importante pour la suite
```

## Les 8 règles

1. **Protocols only.** Un module consomme les autres *uniquement* par leur Protocol.
   Aucun import d'implémentation concrète hors de `main.py`.
2. **Test avant code.** Le test est écrit et commité avant le portage. Si le test ne peut
   pas s'écrire, la brique est mal délimitée : remonter, ne pas coder.
3. **Marqueurs** à jour à chaque modification.
4. **`CLAUDE.md` par module** décrivant le contrat, pas l'implémentation.
5. **Un lot = une branche = une PR.** Nommage : `lot/L3.4-client-mcp`.
6. **Tout portage derrière un flag** `COLAIG_<BRIQUE>_ENABLED`, avec date de péremption
   dans la PR. Le vieux code part au même sprint que le nouveau.
7. **Rien de nominatif** dans le dépôt.
8. **Ne jamais inventer une donnée.** Catalogue de modèles, latence, volumétrie : si la
   valeur manque, arrêter le lot et demander.

## Anti-exemples, tirés du projet

Ces cas sont réels et servent de repères.

| Anti-patron | Où | Pourquoi c'est grave |
|---|---|---|
| Repli hallucinatoire | `browser_extraction.extract_with_llm_summary` | demande au LLM d'**imaginer** le contenu d'une page inaccessible, et le présente comme extrait |
| Import d'un module inexistant | `reactions._save_to_workspace_notes` → `app.services.webdav_client` | la fonction ➕ échoue silencieusement depuis toujours |
| Deux sources de vérité | `.albert` (index, skills, MCP) vs `.colaig` (descripteur) | un espace n'est fonctionnel que s'il porte les deux arborescences |
| Sous-système fantôme | `behavior_manager` chargé à chaque message pour alimenter un résumé de prompt | ~2 000 lignes maintenues pour trois phrases |
| Cache écrit jamais lu | `web_search_cache.set()` sans `.get()` | deux fois la même question = deux fois tout le pipeline |
| Bug d'unité | chunking web : `chunk_size` en caractères, `overlap` en **mots** | ~60 % de recouvrement, embeddings payés 2,5× |
| Flag mort | `has_doc_index = workspace_root is not None` avec `workspace_root = ""` | toujours vrai, et jamais utilisé par la fonction qui le reçoit |

## Tests — cinq niveaux

| Niveau | Emplacement | Quand | Bloquant |
|---|---|---|---|
| Unitaire pur (sans I/O) | `tests/unit/` | chaque push | oui |
| Contrat (une suite par Protocol, toutes implémentations) | `tests/contract/` | chaque push | oui |
| Composant (retriever, resolver, pre-execution) + RAGAS | `tests/component/` | PR | sur régression relative |
| Trajectoire (boucle agent) + DeepEval | `tests/trajectory/` | PR | sur régression relative |
| Adversarial (injection, tool poisoning) | `tests/adversarial/` | hebdo + release | zéro échec |

Aucun test hors `component/` et `trajectory/` n'appelle un service réseau.
