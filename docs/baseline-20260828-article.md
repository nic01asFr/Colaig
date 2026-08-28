# Rapport de référence — 28/08/2026

**Lot L1.5.** Référence contre laquelle toute modification du pipeline se juge.
Reproductible : corpus figé et vérifié par manifeste, jeu doré versionné,
`_chantier/scripts/reference_l15.py`.

## Montage

| | |
|---|---|
| Corpus | 108 documents, Code de la commande publique, articles en vigueur |
| Découpage | `Chunker(800, 100)` — paramètres de `config.py` — **1021 chunks** |
| Embeddings | `BAAI/bge-m3`, 1024 dimensions (défaut D10) |
| Index | `FaissStore` / `IndexFlatIP`, recherche exacte |
| k | 10 passages, valeur de `max_results` de l'espace |
| Jeu doré | 135 cas, dont 22 négatifs |

## Palier 1 — récupération (déterministe)

Deux exécutions donnent le même résultat : ni juge, ni génération. **C'est le
socle.** Si le passage n'est pas remonté, aucune génération ne le rattrapera.

Sur les **113 cas ayant des articles attendus** :

| | cas | part |
|---|---|---|
| Tous les articles attendus remontés | **105** | 93 % |
| Partiellement remontés | 2 | 2 % |
| Aucun remonté | 6 | 5 % |

Rang du premier article attendu : médiane **1**, moyenne 1.9, max 10 (sur k=10).

Latence de recherche : médiane **0.0 ms** (min 0.0, max 47.0).
Embedding d'une question : **0 ms** en moyenne.

### Détail par cas

| cas | type | attendus | remontés | rang | score max |
|---|---|---|---|---|---|
| mp-001 | fait | R2112-1 | ✅ R2112-1 | 1 | 0.7124 |
| mp-002 | fait | L2112-1, R2112-1 | ✅ L2112-1, R2112-1 | 1 | 0.5916 |
| mp-003 | fait | R2122-8 | ✅ R2122-8 | 1 | 0.7364 |
| mp-004 | fait | R2161-2 | ✅ R2161-2 | 3 | 0.7468 |
| mp-005 | procedure | L2113-10, L2113-11 | ⚠️ L2113-11 | 1 | 0.5438 |
| mp-006 | procedure | L2124-2 | ✅ L2124-2 | 1 | 0.6403 |
| mp-007 | procedure | L2193-3 | ✅ L2193-3 | 1 | 0.712 |
| mp-008 | redaction | L2113-10, R2112-1, R2122-8 | ⚠️ R2122-8 | 1 | 0.5986 |
| mp-009 | procedure | L2152-1 | ✅ L2152-1 | 4 | 0.6932 |
| mp-010 | redaction | L2111-1 | ✅ L2111-1 | 3 | 0.6699 |
| mp-011 | fait | R2196-1 | ✅ R2196-1 | 1 | 0.6996 |
| mp-012 | fait | Annexe 2 — Seuils de procédure — texte 1 | ✅ Annexe 2 — Seuils de procédure — texte 1 | 2 | 0.6499 |
| mp-013 | fait | CCAG Travaux 4 | ✅ CCAG Travaux 4 | 1 | 0.7586 |
| mp-014 | piege (négatif) | — | — | — | 0.6287 |
| mp-015 | procedure | R2151-1 | ✅ R2151-1 | 10 | 0.6979 |
| mp-016 | procedure | L2123-1 | ✅ L2123-1 | 1 | 0.6927 |
| mp-017 | fait | L2113-10 | ✅ L2113-10 | 3 | 0.653 |
| mp-018 | redaction | L2193-3 | ✅ L2193-3 | 1 | 0.5989 |
| mp-019 | piege (négatif) | — | — | — | 0.4909 |
| mp-020 | fait | R2122-8 | ✅ R2122-8 | 1 | 0.676 |
| mp-021 | procedure | R2191-3 | ✅ R2191-3 | 1 | 0.637 |
| mp-022 | fait | R2182-1 | ✅ R2182-1 | 2 | 0.7274 |
| mp-023 | redaction | R2112-4 | ✅ R2112-4 | 1 | 0.5664 |
| mp-024 | fait | R2131-6 | ✅ R2131-6 | 1 | 0.673 |
| mp-025 | procedure | R2144-4 | ❌ — | — | 0.6057 |
| mp-026 | procedure | R2152-2 | ✅ R2152-2 | 1 | 0.7257 |
| mp-027 | fait | L2193-7 | ✅ L2193-7 | 1 | 0.7444 |
| mp-028 | procedure | L2193-12 | ✅ L2193-12 | 1 | 0.6634 |
| mp-029 | redaction | L2113-15, L2113-16 | ✅ L2113-15, L2113-16 | 1 | 0.712 |
| mp-030 | fait | R2162-22 | ✅ R2162-22 | 1 | 0.662 |
| mp-031 | procedure | L2194-1 | ✅ L2194-1 | 1 | 0.7111 |
| mp-032 | fait | R2191-7 | ✅ R2191-7 | 1 | 0.6845 |
| mp-033 | fait | L2113-2 | ✅ L2113-2 | 1 | 0.6906 |
| mp-034 | redaction | R2112-1, R2122-8, R2191-3 | ✅ R2112-1, R2122-8, R2191-3 | 2 | 0.5996 |
| mp-035 | piege (négatif) | — | — | — | 0.549 |
| mp-036 | procedure | R2194-1 | ✅ R2194-1 | 1 | 0.7249 |
| mp-037 | fait | R2191-3 | ✅ R2191-3 | 2 | 0.6634 |
| mp-038 | procedure | L2192-12 | ✅ L2192-12 | 1 | 0.739 |
| mp-039 | fait | L2191-4 | ✅ L2191-4 | 4 | 0.6376 |
| mp-040 | piege (négatif) | — | — | — | 0.6813 |
| mp-041 | procedure | R2151-3 | ✅ R2151-3 | 1 | 0.6185 |
| mp-042 | fait | R2191-4 | ✅ R2191-4 | 1 | 0.7275 |
| mp-043 | procedure | L2113-16 | ✅ L2113-16 | 2 | 0.617 |
| mp-044 | piege | L2141-7 | ✅ L2141-7 | 1 | 0.681 |
| mp-045 | fait | R2112-4 | ✅ R2112-4 | 1 | 0.6811 |
| mp-046 | fait | R2111-1 | ✅ R2111-1 | 1 | 0.6144 |
| mp-047 | procedure | R2111-2 | ❌ — | — | 0.6347 |
| mp-048 | redaction | R2111-8 | ✅ R2111-8 | 10 | 0.5804 |
| mp-049 | redaction | R2111-7 | ✅ R2111-7 | 1 | 0.623 |
| mp-050 | redaction | R2111-9 | ✅ R2111-9 | 2 | 0.6148 |
| mp-051 | procedure | R2111-11 | ✅ R2111-11 | 9 | 0.6297 |
| mp-052 | fait | R2111-12 | ✅ R2111-12 | 1 | 0.64 |
| mp-053 | procedure | R2111-14, R2111-15 | ✅ R2111-14, R2111-15 | 1 | 0.7254 |
| mp-054 | procedure | R2111-17 | ✅ R2111-17 | 1 | 0.6303 |
| mp-055 | redaction | R2111-6 | ✅ R2111-6 | 1 | 0.6715 |
| mp-056 | redaction | R2111-5 | ✅ R2111-5 | 9 | 0.6277 |
| mp-057 | fait | L2111-1 | ❌ — | — | 0.5867 |
| mp-058 | fait | L2111-3 | ✅ L2111-3 | 1 | 0.7193 |
| mp-059 | piege (négatif) | — | — | — | 0.7079 |
| mp-060 | procedure | R2143-11 | ✅ R2143-11 | 5 | 0.6432 |
| mp-061 | redaction | Annexe 13 — Modèles de garantie — texte 1 | ✅ Annexe 13 — Modèles de garantie — texte 1 | 2 | 0.6783 |
| mp-062 | piege (négatif) | — | — | — | 0.6603 |
| mp-063 | fait | R2112-13, R2112-9 | ✅ R2112-13, R2112-9 | 1 | 0.6554 |
| mp-064 | redaction | R2112-13 | ✅ R2112-13 | 2 | 0.6065 |
| mp-065 | redaction | R2112-10 | ✅ R2112-10 | 1 | 0.7222 |
| mp-066 | fait | R2112-11 | ✅ R2112-11 | 1 | 0.699 |
| mp-067 | procedure | R2112-4 | ✅ R2112-4 | 1 | 0.6699 |
| mp-068 | piege | R2112-4 | ✅ R2112-4 | 5 | 0.6192 |
| mp-069 | redaction | CCAG Travaux — texte 1 | ✅ CCAG Travaux — texte 1 | 2 | 0.6472 |
| mp-070 | redaction | R2112-3 | ❌ — | — | 0.6469 |
| mp-071 | fait | R2112-6 | ✅ R2112-6 | 1 | 0.6501 |
| mp-072 | procedure | R2112-17 | ✅ R2112-17 | 1 | 0.7173 |
| mp-073 | redaction | R2112-16 | ✅ R2112-16 | 1 | 0.7429 |
| mp-074 | fait | L2112-5 | ✅ L2112-5 | 1 | 0.6791 |
| mp-075 | redaction | L2112-4 | ✅ L2112-4 | 1 | 0.6609 |
| mp-076 | fait | L2112-3 | ✅ L2112-3 | 1 | 0.7331 |
| mp-077 | procedure | R2112-14 | ✅ R2112-14 | 1 | 0.614 |
| mp-078 | piege (négatif) | — | — | — | 0.6147 |
| mp-079 | piege (négatif) | — | — | — | 0.6267 |
| mp-080 | piege (négatif) | — | — | — | 0.6403 |
| mp-081 | fait | L2113-10 | ✅ L2113-10 | 4 | 0.6129 |
| mp-082 | procedure | L2113-11 | ✅ L2113-11 | 1 | 0.7435 |
| mp-083 | procedure | R2113-2 | ✅ R2113-2 | 4 | 0.6789 |
| mp-084 | redaction | R2113-1 | ✅ R2113-1 | 1 | 0.7225 |
| mp-085 | redaction | R2113-4, R2113-5 | ✅ R2113-4, R2113-5 | 1 | 0.6843 |
| mp-086 | procedure | R2113-6 | ✅ R2113-6 | 1 | 0.6481 |
| mp-087 | procedure | L2113-12, R2113-7 | ✅ L2113-12, R2113-7 | 1 | 0.5456 |
| mp-088 | fait | L2113-13 | ✅ L2113-13 | 1 | 0.6556 |
| mp-089 | fait | L2113-16 | ✅ L2113-16 | 1 | 0.6982 |
| mp-090 | redaction | R2113-8 | ✅ R2113-8 | 2 | 0.6743 |
| mp-091 | procedure | L2113-14 | ✅ L2113-14 | 3 | 0.6126 |
| mp-092 | fait | L2113-2 | ✅ L2113-2 | 1 | 0.6906 |
| mp-093 | procedure | L2113-4 | ✅ L2113-4 | 8 | 0.5702 |
| mp-094 | fait | L2113-6 | ✅ L2113-6 | 1 | 0.6125 |
| mp-095 | fait | L2113-7 | ✅ L2113-7 | 1 | 0.5583 |
| mp-096 | procedure | L2113-17 | ✅ L2113-17 | 1 | 0.6104 |
| mp-097 | piege (négatif) | — | — | — | 0.6533 |
| mp-098 | piege (négatif) | — | — | — | 0.6559 |
| mp-099 | piege (négatif) | — | — | — | 0.6426 |
| mp-100 | fait | R2122-8 | ✅ R2122-8 | 1 | 0.7023 |
| mp-101 | procedure | R2123-1 | ✅ R2123-1 | 1 | 0.6864 |
| mp-102 | fait | L2123-1 | ✅ L2123-1 | 1 | 0.7007 |
| mp-103 | procedure | R2123-1 | ✅ R2123-1 | 3 | 0.6927 |
| mp-104 | procedure | R2122-2 | ❌ — | — | 0.568 |
| mp-105 | procedure | R2122-1 | ✅ R2122-1 | 1 | 0.6302 |
| mp-106 | procedure | R2122-3 | ✅ R2122-3 | 1 | 0.585 |
| mp-107 | procedure | R2122-7 | ✅ R2122-7 | 1 | 0.6495 |
| mp-108 | procedure | R2122-6 | ✅ R2122-6 | 1 | 0.6923 |
| mp-109 | fait | R2122-9-1 | ✅ R2122-9-1 | 1 | 0.6233 |
| mp-110 | fait | R2122-9 | ✅ R2122-9 | 1 | 0.6567 |
| mp-111 | fait | L2124-2, L2124-3 | ✅ L2124-2, L2124-3 | 1 | 0.6487 |
| mp-112 | procedure | R2124-3 | ✅ R2124-3 | 1 | 0.7331 |
| mp-113 | piege | R2124-4 | ✅ R2124-4 | 2 | 0.6744 |
| mp-114 | fait | L2124-4, R2124-5 | ✅ L2124-4, R2124-5 | 1 | 0.6893 |
| mp-115 | redaction | R2123-5 | ✅ R2123-5 | 1 | 0.6789 |
| mp-116 | piege | R2123-6 | ❌ — | — | 0.642 |
| mp-117 | procedure | R2123-2 | ✅ R2123-2 | 1 | 0.6437 |
| mp-118 | redaction | R2123-7 | ✅ R2123-7 | 4 | 0.6402 |
| mp-119 | piege (négatif) | — | — | — | 0.5951 |
| mp-120 | piege (négatif) | — | — | — | 0.6381 |
| mp-121 | fait | Annexe 2 — Seuils de procédure — texte 1 | ✅ Annexe 2 — Seuils de procédure — texte 1 | 1 | 0.6651 |
| mp-122 | piege (négatif) | — | — | — | 0.6775 |
| mp-123 | piege (négatif) | — | — | — | 0.6445 |
| mp-124 | procedure | Annexe 7 — Profils d'acheteurs 1 | ✅ Annexe 7 — Profils d'acheteurs 1 | 3 | 0.691 |
| mp-125 | redaction | CCAG Travaux 4 | ✅ CCAG Travaux 4 | 2 | 0.6286 |
| mp-126 | fait | CCAG Travaux 19 | ✅ CCAG Travaux 19 | 1 | 0.6683 |
| mp-127 | procedure | CCAG Travaux 41 | ✅ CCAG Travaux 41 | 1 | 0.7163 |
| mp-128 | redaction | CCAG Travaux 12 | ✅ CCAG Travaux 12 | 1 | 0.6678 |
| mp-129 | procedure | CCAG Travaux 3 | ✅ CCAG Travaux 3 | 5 | 0.6871 |
| mp-130 | piege (négatif) | — | — | — | 0.6662 |
| mp-131 | piege (négatif) | — | — | — | 0.6986 |
| mp-132 | piege (négatif) | — | — | — | 0.5729 |
| mp-133 | piege (négatif) | — | — | — | 0.6354 |
| mp-134 | piege (négatif) | — | — | — | 0.682 |
| mp-135 | piege (négatif) | — | — | — | 0.7256 |

### Échecs de récupération — à examiner en priorité

- **mp-025** : attendus R2144-4 — remontés depuis 064-deuxieme-partie-marches-public, 064-deuxieme-partie-marches-public, 081-deuxieme-partie-marches-public
- **mp-047** : attendus R2111-2 — remontés depuis 072-deuxieme-partie-marches-public, 072-deuxieme-partie-marches-public, 072-deuxieme-partie-marches-public
- **mp-057** : attendus L2111-1 — remontés depuis 078-deuxieme-partie-marches-public, 078-deuxieme-partie-marches-public, 078-deuxieme-partie-marches-public
- **mp-070** : attendus R2112-3 — remontés depuis 053-ccag-travaux-chapitre-1er-gene, 043-ccag-techniques-de-l-informati, 026-ccag-maitrise-d-uvre-chapitre-
- **mp-104** : attendus R2122-2 — remontés depuis 062-deuxieme-partie-marches-public, 107-deuxieme-partie-marches-public, 104-deuxieme-partie-marches-public
- **mp-116** : attendus R2123-6 — remontés depuis 062-deuxieme-partie-marches-public, 079-deuxieme-partie-marches-public, 097-deuxieme-partie-marches-public

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

- Embedding du corpus : **0 s** pour 1021 chunks (3633 chunks/s).
- Construction de l'index : 47 ms.
- Empreinte de l'index : **4.2 Mo** en float32.

## Comment rejouer

```bash
python _chantier/scripts/reference_l15.py
```

Le corpus est vérifié par `tests/test_jeu_dore.py` contre son manifeste avant
toute comparaison : une référence établie sur un corpus dérivé ne vaut rien.
