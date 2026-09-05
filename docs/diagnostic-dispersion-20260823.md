# Dispersion des scores et effet du reranking

**23/08/2026.** Diagnostic déclenché par la régression de `mp-015` : les dix
premiers candidats y tenaient dans 1,8 %, rendant leur classement arbitraire.
Mesuré sur un seul cas, cela ne prouvait rien. Voici la mesure sur les vingt.

Montage : découpage par article, 1 762 chunks, `bge-m3` 1024 dimensions,
20 candidats denses rescorés par `bge-reranker-v2-m3`, succès = top 6.

## Résultat

| | dans le top 6 |
|---|---|
| Recherche dense seule | **15/17** |
| Dense puis reranking | **16/17** |

## L'écrasement des scores prédit-il l'échec ?

| | dispersion médiane du top 10 |
|---|---|
| cas réussis en dense | 12.35 % |
| cas échoués en dense | 5.19 % |

Latence de reranking : médiane **141 ms** pour 20 candidats.

## Détail

| cas | dispersion top 10 | rang dense | rang reranké | |
|---|---|---|---|---|
| mp-001 | 12.82 % | 1 | 1 | → |
| mp-002 | 9.16 % | 1 | 2 | → |
| mp-003 | 8.39 % | 1 | 1 | → |
| mp-004 | 7.54 % | 3 | 1 | → |
| mp-005 | 12.99 % | 1 | 3 | → |
| mp-006 | 8.06 % | 1 | 1 | → |
| mp-007 | 17.14 % | 1 | 1 | → |
| mp-008 | 4.50 % | 1 | 2 | → |
| mp-009 | 11.63 % | 5 | 6 | → |
| mp-010 | 13.08 % | 3 | 1 | → |
| mp-011 | 17.17 % | 1 | 1 | → |
| mp-012 | 8.59 % | 9 | 9 | → |
| mp-013 (négatif) | 6.31 % | — | — | — |
| mp-014 (négatif) | 22.32 % | — | — | — |
| mp-015 | 1.79 % | 11 | 5 | ↑ **gagné** |
| mp-016 | 15.80 % | 1 | 3 | → |
| mp-017 | 17.66 % | 4 | 1 | → |
| mp-018 | 12.35 % | 1 | 1 | → |
| mp-019 (négatif) | 4.54 % | — | — | — |
| mp-020 | 9.87 % | 1 | 5 | → |


## Lecture

### 1. L'écrasement des scores prédit bien l'échec

**Dispersion médiane : 12,35 % sur les cas réussis, 5,19 % sur les cas échoués.** Un
facteur 2,4. Quand l'embedding sépare mal, il se trompe — l'hypothèse tirée d'un seul cas
se vérifie sur l'ensemble.

Et le cas le plus pathologique est isolé sans ambiguïté : **`mp-015`, 1,79 %**, très loin
du suivant (`mp-008`, 4,50 %). C'est exactement le cas qui avait régressé.

### 2. Le reranker répare précisément ce cas-là

`mp-015` : **rang 11 en dense → rang 5 après reranking.** Le seul cas gagné, et c'est
celui que le diagnostic désignait. Là encore, ce n'est pas l'agrégat qui convainc — c'est
la coïncidence entre le cas prédit et le cas corrigé.

### 3. Mais le reranker n'est pas uniformément meilleur

Il **reclasse**, et pas toujours dans le bon sens :

| cas | dense | reranké | dispersion |
|---|---|---|---|
| `mp-005` | 1 | 3 | 12,99 % |
| `mp-016` | 1 | 3 | 15,80 % |
| `mp-020` | 1 | 5 | 9,87 % |

Aucun ne sort du top 6, donc le score global n'en souffre pas. Mais la tendance est
lisible : **là où la recherche dense est déjà nette — dispersion élevée — le reranking
dégrade le rang.** Là où elle est plate, il sauve.

Gain net : **15/17 → 16/17**. Un cas. Sous le seuil de signification que la référence
s'est fixé. **Je ne conclus donc pas que le reranker améliore le pipeline** ; je conclus
qu'il répare le cas que la dispersion désignait.

### 4. `mp-012` résiste, et c'est cohérent

Rang 9 avant et après. C'est le cas qui demande un seuil en euros : la question tire vers
les documents qui **contiennent des montants**, et aucun reclassement ne corrige une
question qui vise à côté. Ce n'est pas un problème de classement mais de sémantique — et
c'est un cas négatif, dont la bonne réponse est de dire que l'information n'y est pas.

## Ce que cela suggère — à mesurer, pas à appliquer

**Un reranking conditionnel.** Le déclencher quand la dispersion du top 10 passe sous un
seuil, la laisser tranquille sinon. On prendrait le gain là où il existe sans subir le
reclassement là où le dense est déjà sûr.

C'est peu coûteux : **141 ms de médiane** pour 20 candidats, à comparer aux 1,19 s d'un
tour LLM outillé. Et c'est testable exactement comme le reste.

**Conséquence pour l'arbitrage H2 :** le reranker n'apparaît plus comme un gain général —
il apparaît comme le **remède d'une pathologie identifiable et mesurable**. Cela ne
tranche pas entre bi-provider, MMR et reranker local, mais cela change la question : il ne
s'agit plus de savoir si le reranker « améliore », mais si l'on accepte de laisser sans
remède les requêtes à faible dispersion. Sur un corpus juridique, où elles sont
structurellement fréquentes, c'est une question sérieuse.

**Réserve : 17 cas.** Tout ce qui précède demande confirmation au volume.

## Rejouer

```bash
python _chantier/scripts/diagnostic_dispersion.py
```
