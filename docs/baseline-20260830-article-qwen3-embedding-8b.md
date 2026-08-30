# Rapport de référence — 30/08/2026

**Lot L1.5.** Référence contre laquelle toute modification du pipeline se juge.
Reproductible : corpus figé et vérifié par manifeste, jeu doré versionné,
`_chantier/scripts/reference_l15.py`.

## Montage

| | |
|---|---|
| Corpus | 108 documents, Code de la commande publique, articles en vigueur |
| Découpage | `Chunker(800, 100)` — paramètres de `config.py` — **1021 chunks** |
| Embeddings | `qwen3-embedding-8b`, 4096 dimensions (défaut D10) |
| Index | `FaissStore` / `IndexFlatIP`, recherche exacte |
| k | 10 passages, valeur de `max_results` de l'espace |
| Jeu doré | 135 cas, dont 22 négatifs |

## Palier 1 — récupération (déterministe)

Deux exécutions donnent le même résultat : ni juge, ni génération. **C'est le
socle.** Si le passage n'est pas remonté, aucune génération ne le rattrapera.

Sur les **113 cas ayant des articles attendus** :

| | cas | part |
|---|---|---|
| Tous les articles attendus remontés | **107** | 95 % |
| Partiellement remontés | 2 | 2 % |
| Aucun remonté | 4 | 4 % |

Rang du premier article attendu : médiane **1**, moyenne 1.5, max 8 (sur k=10).

Latence de recherche : médiane **1.6 ms** (min 1.5, max 2.4).
Embedding d'une question : **0 ms** en moyenne.

### Détail par cas

| cas | type | attendus | remontés | rang | score max |
|---|---|---|---|---|---|
| mp-001 | fait | R2112-1 | ✅ R2112-1 | 1 | 0.8912 |
| mp-002 | fait | L2112-1, R2112-1 | ✅ L2112-1, R2112-1 | 1 | 0.7634 |
| mp-003 | fait | R2122-8 | ✅ R2122-8 | 1 | 0.8037 |
| mp-004 | fait | R2161-2 | ✅ R2161-2 | 1 | 0.8724 |
| mp-005 | procedure | L2113-10, L2113-11 | ✅ L2113-10, L2113-11 | 1 | 0.5164 |
| mp-006 | procedure | L2124-2 | ❌ — | — | 0.7279 |
| mp-007 | procedure | L2193-3 | ✅ L2193-3 | 1 | 0.7992 |
| mp-008 | redaction | L2113-10, R2112-1, R2122-8 | ⚠️ R2112-1, R2122-8 | 1 | 0.703 |
| mp-009 | procedure | L2152-1 | ✅ L2152-1 | 4 | 0.7595 |
| mp-010 | redaction | L2111-1 | ✅ L2111-1 | 3 | 0.6254 |
| mp-011 | fait | R2196-1 | ✅ R2196-1 | 1 | 0.7157 |
| mp-012 | fait | Annexe 2 — Seuils de procédure — texte 1 | ✅ Annexe 2 — Seuils de procédure — texte 1 | 7 | 0.697 |
| mp-013 | fait | CCAG Travaux 4 | ✅ CCAG Travaux 4 | 1 | 0.7959 |
| mp-014 | piege (négatif) | — | — | — | 0.8285 |
| mp-015 | procedure | R2151-1 | ✅ R2151-1 | 1 | 0.8339 |
| mp-016 | procedure | L2123-1 | ✅ L2123-1 | 1 | 0.676 |
| mp-017 | fait | L2113-10 | ✅ L2113-10 | 2 | 0.6429 |
| mp-018 | redaction | L2193-3 | ✅ L2193-3 | 1 | 0.6717 |
| mp-019 | piege (négatif) | — | — | — | 0.455 |
| mp-020 | fait | R2122-8 | ✅ R2122-8 | 1 | 0.8122 |
| mp-021 | procedure | R2191-3 | ✅ R2191-3 | 2 | 0.6174 |
| mp-022 | fait | R2182-1 | ✅ R2182-1 | 1 | 0.7267 |
| mp-023 | redaction | R2112-4 | ✅ R2112-4 | 1 | 0.6666 |
| mp-024 | fait | R2131-6 | ✅ R2131-6 | 1 | 0.791 |
| mp-025 | procedure | R2144-4 | ✅ R2144-4 | 2 | 0.6293 |
| mp-026 | procedure | R2152-2 | ✅ R2152-2 | 1 | 0.8433 |
| mp-027 | fait | L2193-7 | ✅ L2193-7 | 1 | 0.8196 |
| mp-028 | procedure | L2193-12 | ✅ L2193-12 | 1 | 0.8106 |
| mp-029 | redaction | L2113-15, L2113-16 | ✅ L2113-15, L2113-16 | 1 | 0.757 |
| mp-030 | fait | R2162-22 | ✅ R2162-22 | 1 | 0.795 |
| mp-031 | procedure | L2194-1 | ✅ L2194-1 | 1 | 0.8903 |
| mp-032 | fait | R2191-7 | ✅ R2191-7 | 3 | 0.5367 |
| mp-033 | fait | L2113-2 | ✅ L2113-2 | 1 | 0.8315 |
| mp-034 | redaction | R2112-1, R2122-8, R2191-3 | ⚠️ R2191-3 | 1 | 0.7284 |
| mp-035 | piege (négatif) | — | — | — | 0.6722 |
| mp-036 | procedure | R2194-1 | ✅ R2194-1 | 1 | 0.8407 |
| mp-037 | fait | R2191-3 | ✅ R2191-3 | 1 | 0.7623 |
| mp-038 | procedure | L2192-12 | ✅ L2192-12 | 1 | 0.766 |
| mp-039 | fait | L2191-4 | ❌ — | — | 0.6867 |
| mp-040 | piege (négatif) | — | — | — | 0.7303 |
| mp-041 | procedure | R2151-3 | ✅ R2151-3 | 1 | 0.7549 |
| mp-042 | fait | R2191-4 | ✅ R2191-4 | 1 | 0.7621 |
| mp-043 | procedure | L2113-16 | ✅ L2113-16 | 1 | 0.7077 |
| mp-044 | piege | L2141-7 | ✅ L2141-7 | 2 | 0.7683 |
| mp-045 | fait | R2112-4 | ✅ R2112-4 | 1 | 0.8098 |
| mp-046 | fait | R2111-1 | ✅ R2111-1 | 1 | 0.6765 |
| mp-047 | procedure | R2111-2 | ✅ R2111-2 | 1 | 0.6077 |
| mp-048 | redaction | R2111-8 | ✅ R2111-8 | 2 | 0.7205 |
| mp-049 | redaction | R2111-7 | ✅ R2111-7 | 1 | 0.7252 |
| mp-050 | redaction | R2111-9 | ✅ R2111-9 | 4 | 0.6822 |
| mp-051 | procedure | R2111-11 | ✅ R2111-11 | 1 | 0.6926 |
| mp-052 | fait | R2111-12 | ✅ R2111-12 | 1 | 0.7592 |
| mp-053 | procedure | R2111-14, R2111-15 | ✅ R2111-14, R2111-15 | 1 | 0.8289 |
| mp-054 | procedure | R2111-17 | ✅ R2111-17 | 1 | 0.6846 |
| mp-055 | redaction | R2111-6 | ✅ R2111-6 | 1 | 0.7933 |
| mp-056 | redaction | R2111-5 | ✅ R2111-5 | 1 | 0.6868 |
| mp-057 | fait | L2111-1 | ✅ L2111-1 | 1 | 0.5987 |
| mp-058 | fait | L2111-3 | ✅ L2111-3 | 1 | 0.7093 |
| mp-059 | piege (négatif) | — | — | — | 0.6992 |
| mp-060 | procedure | R2143-11 | ✅ R2143-11 | 1 | 0.7282 |
| mp-061 | redaction | Annexe 13 — Modèles de garantie — texte 1 | ✅ Annexe 13 — Modèles de garantie — texte 1 | 2 | 0.7178 |
| mp-062 | piege (négatif) | — | — | — | 0.745 |
| mp-063 | fait | R2112-13, R2112-9 | ✅ R2112-13, R2112-9 | 2 | 0.7961 |
| mp-064 | redaction | R2112-13 | ✅ R2112-13 | 3 | 0.6249 |
| mp-065 | redaction | R2112-10 | ✅ R2112-10 | 2 | 0.7927 |
| mp-066 | fait | R2112-11 | ✅ R2112-11 | 1 | 0.743 |
| mp-067 | procedure | R2112-4 | ✅ R2112-4 | 1 | 0.7581 |
| mp-068 | piege | R2112-4 | ✅ R2112-4 | 1 | 0.703 |
| mp-069 | redaction | CCAG Travaux — texte 1 | ❌ — | — | 0.7097 |
| mp-070 | redaction | R2112-3 | ❌ — | — | 0.7 |
| mp-071 | fait | R2112-6 | ✅ R2112-6 | 1 | 0.7836 |
| mp-072 | procedure | R2112-17 | ✅ R2112-17 | 1 | 0.8308 |
| mp-073 | redaction | R2112-16 | ✅ R2112-16 | 1 | 0.872 |
| mp-074 | fait | L2112-5 | ✅ L2112-5 | 1 | 0.7759 |
| mp-075 | redaction | L2112-4 | ✅ L2112-4 | 1 | 0.7664 |
| mp-076 | fait | L2112-3 | ✅ L2112-3 | 1 | 0.7718 |
| mp-077 | procedure | R2112-14 | ✅ R2112-14 | 1 | 0.7402 |
| mp-078 | piege (négatif) | — | — | — | 0.7002 |
| mp-079 | piege (négatif) | — | — | — | 0.726 |
| mp-080 | piege (négatif) | — | — | — | 0.7528 |
| mp-081 | fait | L2113-10 | ✅ L2113-10 | 2 | 0.6599 |
| mp-082 | procedure | L2113-11 | ✅ L2113-11 | 1 | 0.7671 |
| mp-083 | procedure | R2113-2 | ✅ R2113-2 | 2 | 0.7999 |
| mp-084 | redaction | R2113-1 | ✅ R2113-1 | 1 | 0.7669 |
| mp-085 | redaction | R2113-4, R2113-5 | ✅ R2113-4, R2113-5 | 1 | 0.7081 |
| mp-086 | procedure | R2113-6 | ✅ R2113-6 | 1 | 0.7116 |
| mp-087 | procedure | L2113-12, R2113-7 | ✅ L2113-12, R2113-7 | 1 | 0.6841 |
| mp-088 | fait | L2113-13 | ✅ L2113-13 | 1 | 0.7466 |
| mp-089 | fait | L2113-16 | ✅ L2113-16 | 1 | 0.7933 |
| mp-090 | redaction | R2113-8 | ✅ R2113-8 | 1 | 0.6941 |
| mp-091 | procedure | L2113-14 | ✅ L2113-14 | 1 | 0.6463 |
| mp-092 | fait | L2113-2 | ✅ L2113-2 | 1 | 0.8315 |
| mp-093 | procedure | L2113-4 | ✅ L2113-4 | 1 | 0.7658 |
| mp-094 | fait | L2113-6 | ✅ L2113-6 | 1 | 0.7799 |
| mp-095 | fait | L2113-7 | ✅ L2113-7 | 1 | 0.7489 |
| mp-096 | procedure | L2113-17 | ✅ L2113-17 | 1 | 0.7023 |
| mp-097 | piege (négatif) | — | — | — | 0.7365 |
| mp-098 | piege (négatif) | — | — | — | 0.7047 |
| mp-099 | piege (négatif) | — | — | — | 0.72 |
| mp-100 | fait | R2122-8 | ✅ R2122-8 | 1 | 0.8415 |
| mp-101 | procedure | R2123-1 | ✅ R2123-1 | 2 | 0.8206 |
| mp-102 | fait | L2123-1 | ✅ L2123-1 | 1 | 0.671 |
| mp-103 | procedure | R2123-1 | ✅ R2123-1 | 5 | 0.676 |
| mp-104 | procedure | R2122-2 | ✅ R2122-2 | 6 | 0.6924 |
| mp-105 | procedure | R2122-1 | ✅ R2122-1 | 1 | 0.6991 |
| mp-106 | procedure | R2122-3 | ✅ R2122-3 | 1 | 0.6714 |
| mp-107 | procedure | R2122-7 | ✅ R2122-7 | 1 | 0.7485 |
| mp-108 | procedure | R2122-6 | ✅ R2122-6 | 1 | 0.7874 |
| mp-109 | fait | R2122-9-1 | ✅ R2122-9-1 | 1 | 0.7577 |
| mp-110 | fait | R2122-9 | ✅ R2122-9 | 1 | 0.6561 |
| mp-111 | fait | L2124-2, L2124-3 | ✅ L2124-2, L2124-3 | 1 | 0.7293 |
| mp-112 | procedure | R2124-3 | ✅ R2124-3 | 2 | 0.7798 |
| mp-113 | piege | R2124-4 | ✅ R2124-4 | 2 | 0.6967 |
| mp-114 | fait | L2124-4, R2124-5 | ✅ L2124-4, R2124-5 | 1 | 0.6783 |
| mp-115 | redaction | R2123-5 | ✅ R2123-5 | 1 | 0.8385 |
| mp-116 | piege | R2123-6 | ✅ R2123-6 | 3 | 0.6916 |
| mp-117 | procedure | R2123-2 | ✅ R2123-2 | 1 | 0.6965 |
| mp-118 | redaction | R2123-7 | ✅ R2123-7 | 2 | 0.7174 |
| mp-119 | piege (négatif) | — | — | — | 0.5696 |
| mp-120 | piege (négatif) | — | — | — | 0.6141 |
| mp-121 | fait | Annexe 2 — Seuils de procédure — texte 1 | ✅ Annexe 2 — Seuils de procédure — texte 1 | 1 | 0.7579 |
| mp-122 | piege (négatif) | — | — | — | 0.7987 |
| mp-123 | piege (négatif) | — | — | — | 0.6213 |
| mp-124 | procedure | Annexe 7 — Profils d'acheteurs 1 | ✅ Annexe 7 — Profils d'acheteurs 1 | 1 | 0.7095 |
| mp-125 | redaction | CCAG Travaux 4 | ✅ CCAG Travaux 4 | 1 | 0.7687 |
| mp-126 | fait | CCAG Travaux 19 | ✅ CCAG Travaux 19 | 2 | 0.7621 |
| mp-127 | procedure | CCAG Travaux 41 | ✅ CCAG Travaux 41 | 1 | 0.7099 |
| mp-128 | redaction | CCAG Travaux 12 | ✅ CCAG Travaux 12 | 8 | 0.6836 |
| mp-129 | procedure | CCAG Travaux 3 | ✅ CCAG Travaux 3 | 5 | 0.6198 |
| mp-130 | piege (négatif) | — | — | — | 0.6644 |
| mp-131 | piege (négatif) | — | — | — | 0.7755 |
| mp-132 | piege (négatif) | — | — | — | 0.6541 |
| mp-133 | piege (négatif) | — | — | — | 0.702 |
| mp-134 | piege (négatif) | — | — | — | 0.7318 |
| mp-135 | piege (négatif) | — | — | — | 0.726 |

### Échecs de récupération — à examiner en priorité

- **mp-006** : attendus L2124-2 — remontés depuis 104-deuxieme-partie-marches-public, 104-deuxieme-partie-marches-public, 104-deuxieme-partie-marches-public
- **mp-039** : attendus L2191-4 — remontés depuis 088-deuxieme-partie-marches-public, 035-ccag-prestations-intellectuell, 044-ccag-techniques-de-l-informati
- **mp-069** : attendus CCAG Travaux — texte 1 — remontés depuis 016-ccag-marches-industriels-chapi, 007-ccag-fournitures-et-services-c, 034-ccag-prestations-intellectuell
- **mp-070** : attendus R2112-3 — remontés depuis 053-ccag-travaux-chapitre-1er-gene, 016-ccag-marches-industriels-chapi, 007-ccag-fournitures-et-services-c

## Palier 2 — génération (jugée, variable)

**Non mesuré dans cette référence v1.** La génération dépend d'un modèle et
varie d'une exécution à l'autre ; l'inclure au socle reproduirait le
« ça a l'air mieux » que ce chantier combat. À ajouter comme palier distinct,
avec sa variance mesurée sur plusieurs exécutions — pas un chiffre unique.

Ce qu'il devra mesurer, par ordre d'importance :

1. **Le refus sur cas négatif.** Un seuil inventé produit une procédure
   irrégulière. C'est l'échec le plus coûteux, et il ne se voit pas.
2. **L'exactitude des citations** — l'article cité existe et dit ce qu'on lui fait dire.
3. La fidélité de la réponse aux passages remontés.

## Coûts d'établissement

- Embedding du corpus : **1 s** pour 1021 chunks (1485 chunks/s).
- Construction de l'index : 135 ms.
- Empreinte de l'index : **16.7 Mo** en float32.

## Comment rejouer

```bash
python _chantier/scripts/reference_l15.py
```

Le corpus est vérifié par `tests/test_jeu_dore.py` contre son manifeste avant
toute comparaison : une référence établie sur un corpus dérivé ne vaut rien.
