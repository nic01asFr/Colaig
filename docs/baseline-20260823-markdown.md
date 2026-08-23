# Rapport de référence — 23/08/2026

**Lot L1.5.** Référence contre laquelle toute modification du pipeline se juge.
Reproductible : corpus figé et vérifié par manifeste, jeu doré versionné,
`_chantier/scripts/reference_l15.py`.

## Montage

| | |
|---|---|
| Corpus | 188 documents, Code de la commande publique, articles en vigueur |
| Découpage | `Chunker(800, 100)` — paramètres de `config.py` — **2417 chunks** |
| Embeddings | `BAAI/bge-m3`, 1024 dimensions (défaut D10) |
| Index | `FaissStore` / `IndexFlatIP`, recherche exacte |
| k | 6 passages, valeur de `max_results` de l'espace |
| Jeu doré | 122 cas, dont 21 négatifs |

## Palier 1 — récupération (déterministe)

Deux exécutions donnent le même résultat : ni juge, ni génération. **C'est le
socle.** Si le passage n'est pas remonté, aucune génération ne le rattrapera.

Sur les **103 cas ayant des articles attendus** :

| | cas | part |
|---|---|---|
| Tous les articles attendus remontés | **85** | 83 % |
| Partiellement remontés | 6 | 6 % |
| Aucun remonté | 12 | 12 % |

Rang du premier article attendu : médiane **1**, moyenne 1.7, max 6 (sur k=6).

Latence de recherche : médiane **0.0 ms** (min 0.0, max 47.0).
Embedding d'une question : **27 ms** en moyenne.

### Détail par cas

| cas | type | attendus | remontés | rang | score max |
|---|---|---|---|---|---|
| mp-001 | fait | R2112-1 | ✅ R2112-1 | 1 | 0.748 |
| mp-002 | fait | L2112-1, R2112-1 | ⚠️ R2112-1 | 1 | 0.6247 |
| mp-003 | fait | R2122-8 | ✅ R2122-8 | 1 | 0.7454 |
| mp-004 | fait | R2161-2 | ❌ — | — | 0.7645 |
| mp-005 | procedure | L2113-10, L2113-11 | ⚠️ L2113-11 | 2 | 0.5797 |
| mp-006 | procedure | L2124-2 | ✅ L2124-2 | 1 | 0.66 |
| mp-007 | procedure | L2193-3 | ✅ L2193-3 | 1 | 0.7185 |
| mp-008 | redaction | L2113-10, R2112-1, R2122-8 | ⚠️ R2112-1, R2122-8 | 3 | 0.6261 |
| mp-009 | procedure | L2152-1 | ❌ — | — | 0.7055 |
| mp-010 | redaction | L2111-1 | ✅ L2111-1 | 4 | 0.6774 |
| mp-011 | fait | R2196-1 | ✅ R2196-1 | 1 | 0.7509 |
| mp-012 | piege | L2124-1 | ❌ — | — | 0.6765 |
| mp-013 | piege (négatif) | — | — | — | 0.49 |
| mp-014 | piege (négatif) | — | — | — | 0.6568 |
| mp-015 | procedure | R2151-1 | ✅ R2151-1 | 6 | 0.747 |
| mp-016 | procedure | L2123-1 | ✅ L2123-1 | 1 | 0.6984 |
| mp-017 | fait | L2113-10 | ✅ L2113-10 | 2 | 0.669 |
| mp-018 | redaction | L2193-3 | ✅ L2193-3 | 1 | 0.5926 |
| mp-019 | piege (négatif) | — | — | — | 0.5006 |
| mp-020 | fait | R2122-8 | ✅ R2122-8 | 1 | 0.6561 |
| mp-021 | procedure | R2191-3 | ✅ R2191-3 | 3 | 0.6828 |
| mp-022 | fait | R2182-1 | ✅ R2182-1 | 1 | 0.7142 |
| mp-023 | redaction | R2112-4 | ✅ R2112-4 | 1 | 0.6067 |
| mp-024 | fait | R2131-6 | ✅ R2131-6 | 1 | 0.7774 |
| mp-025 | procedure | R2144-4 | ❌ — | — | 0.6749 |
| mp-026 | procedure | R2152-2 | ✅ R2152-2 | 1 | 0.7309 |
| mp-027 | fait | L2193-7 | ✅ L2193-7 | 1 | 0.7208 |
| mp-028 | procedure | L2193-12 | ❌ — | — | 0.629 |
| mp-029 | redaction | L2113-16 | ✅ L2113-16 | 1 | 0.7268 |
| mp-030 | fait | R2162-22 | ✅ R2162-22 | 1 | 0.7041 |
| mp-031 | procedure | L2194-1 | ✅ L2194-1 | 1 | 0.7346 |
| mp-032 | piege | R2191-3 | ✅ R2191-3 | 6 | 0.695 |
| mp-033 | fait | L2113-2 | ✅ L2113-2 | 1 | 0.7495 |
| mp-034 | redaction | R2112-1, R2122-8, R2191-3 | ⚠️ R2122-8 | 2 | 0.6122 |
| mp-035 | piege (négatif) | — | — | — | 0.4804 |
| mp-036 | procedure | R2194-1 | ✅ R2194-1 | 2 | 0.7313 |
| mp-037 | fait | R2191-3 | ✅ R2191-3 | 4 | 0.6735 |
| mp-038 | procedure | L2192-12 | ✅ L2192-12 | 2 | 0.7944 |
| mp-039 | fait | L2191-4 | ✅ L2191-4 | 4 | 0.6283 |
| mp-040 | piege (négatif) | — | — | — | 0.6395 |
| mp-041 | procedure | R3124-3 | ✅ R3124-3 | 1 | 0.6973 |
| mp-042 | fait | R2191-4 | ✅ R2191-4 | 1 | 0.7157 |
| mp-043 | procedure | L2113-16 | ✅ L2113-16 | 2 | 0.6475 |
| mp-044 | piege | L2141-7 | ✅ L2141-7 | 3 | 0.67 |
| mp-045 | fait | R2112-4 | ✅ R2112-4 | 1 | 0.6876 |
| mp-046 | fait | R2111-1 | ✅ R2111-1 | 1 | 0.6742 |
| mp-047 | procedure | R2111-2 | ❌ — | — | 0.6163 |
| mp-048 | redaction | R2111-8 | ✅ R2111-8 | 1 | 0.6006 |
| mp-049 | redaction | R2111-7 | ✅ R2111-7 | 1 | 0.6448 |
| mp-050 | redaction | R2111-9 | ✅ R2111-9 | 3 | 0.6423 |
| mp-051 | procedure | R2111-11 | ✅ R2111-11 | 3 | 0.6427 |
| mp-052 | fait | R2111-12 | ✅ R2111-12 | 1 | 0.6807 |
| mp-053 | procedure | R2111-14, R2111-15 | ✅ R2111-14, R2111-15 | 1 | 0.7578 |
| mp-054 | procedure | R2111-17 | ✅ R2111-17 | 1 | 0.6573 |
| mp-055 | redaction | R2111-6 | ✅ R2111-6 | 1 | 0.7044 |
| mp-056 | redaction | R2111-5 | ❌ — | — | 0.574 |
| mp-057 | fait | L2111-1 | ❌ — | — | 0.6023 |
| mp-058 | fait | L2111-3 | ✅ L2111-3 | 1 | 0.7272 |
| mp-059 | piege (négatif) | — | — | — | 0.7706 |
| mp-060 | piege (négatif) | — | — | — | 0.6498 |
| mp-061 | piege (négatif) | — | — | — | 0.6321 |
| mp-062 | piege (négatif) | — | — | — | 0.6781 |
| mp-063 | fait | R2112-13, R2112-9 | ✅ R2112-13, R2112-9 | 1 | 0.7045 |
| mp-064 | redaction | R2112-13 | ✅ R2112-13 | 6 | 0.6055 |
| mp-065 | redaction | R2112-10 | ✅ R2112-10 | 1 | 0.755 |
| mp-066 | fait | R2112-11 | ✅ R2112-11 | 1 | 0.7427 |
| mp-067 | procedure | R2112-4 | ✅ R2112-4 | 1 | 0.699 |
| mp-068 | piege | R2112-4 | ✅ R2112-4 | 4 | 0.6459 |
| mp-069 | redaction | R2112-2 | ❌ — | — | 0.5294 |
| mp-070 | redaction | R2112-3 | ❌ — | — | 0.4972 |
| mp-071 | fait | R2112-6 | ✅ R2112-6 | 1 | 0.6935 |
| mp-072 | procedure | R2112-17 | ✅ R2112-17 | 1 | 0.7637 |
| mp-073 | redaction | R2112-16 | ✅ R2112-16 | 1 | 0.7582 |
| mp-074 | fait | L2112-5 | ✅ L2112-5 | 1 | 0.6915 |
| mp-075 | redaction | L2112-4 | ✅ L2112-4 | 1 | 0.679 |
| mp-076 | fait | L2112-3 | ✅ L2112-3 | 1 | 0.78 |
| mp-077 | procedure | R2112-14 | ✅ R2112-14 | 2 | 0.6377 |
| mp-078 | piege (négatif) | — | — | — | 0.6243 |
| mp-079 | piege (négatif) | — | — | — | 0.6375 |
| mp-080 | piege (négatif) | — | — | — | 0.6729 |
| mp-081 | fait | L2113-10 | ✅ L2113-10 | 5 | 0.6342 |
| mp-082 | procedure | L2113-11 | ✅ L2113-11 | 1 | 0.7748 |
| mp-083 | procedure | R2113-2 | ✅ R2113-2 | 1 | 0.7106 |
| mp-084 | redaction | R2113-1 | ✅ R2113-1 | 1 | 0.7124 |
| mp-085 | redaction | R2113-4, R2113-5 | ✅ R2113-4, R2113-5 | 1 | 0.6717 |
| mp-086 | procedure | R2113-6 | ✅ R2113-6 | 1 | 0.721 |
| mp-087 | procedure | L2113-12, R2113-7 | ⚠️ L2113-12 | 1 | 0.5589 |
| mp-088 | fait | L2113-13 | ✅ L2113-13 | 1 | 0.6791 |
| mp-089 | fait | L2113-16 | ✅ L2113-16 | 1 | 0.7372 |
| mp-090 | redaction | R2113-8 | ✅ R2113-8 | 2 | 0.6883 |
| mp-091 | procedure | L2113-14 | ✅ L2113-14 | 3 | 0.6261 |
| mp-092 | fait | L2113-2 | ✅ L2113-2 | 1 | 0.7495 |
| mp-093 | procedure | L2113-4 | ✅ L2113-4 | 2 | 0.6045 |
| mp-094 | fait | L2113-6 | ✅ L2113-6 | 1 | 0.6502 |
| mp-095 | fait | L2113-7 | ✅ L2113-7 | 1 | 0.6121 |
| mp-096 | procedure | L2113-17 | ✅ L2113-17 | 1 | 0.6293 |
| mp-097 | piege (négatif) | — | — | — | 0.6651 |
| mp-098 | piege (négatif) | — | — | — | 0.6901 |
| mp-099 | piege (négatif) | — | — | — | 0.6584 |
| mp-100 | fait | R2122-8 | ✅ R2122-8 | 1 | 0.7741 |
| mp-101 | procedure | R2123-1 | ✅ R2123-1 | 1 | 0.6941 |
| mp-102 | fait | L2123-1 | ✅ L2123-1 | 1 | 0.7079 |
| mp-103 | procedure | R2123-1 | ❌ — | — | 0.6984 |
| mp-104 | procedure | R2122-2 | ✅ R2122-2 | 3 | 0.5837 |
| mp-105 | procedure | R2122-1 | ✅ R2122-1 | 1 | 0.6574 |
| mp-106 | procedure | R2122-3 | ✅ R2122-3 | 1 | 0.6203 |
| mp-107 | procedure | R2122-7 | ✅ R2122-7 | 2 | 0.7059 |
| mp-108 | procedure | R2122-6 | ✅ R2122-6 | 1 | 0.7065 |
| mp-109 | fait | R2122-9-1 | ✅ R2122-9-1 | 1 | 0.6068 |
| mp-110 | fait | R2122-9 | ✅ R2122-9 | 1 | 0.681 |
| mp-111 | fait | L2124-2, L2124-3 | ✅ L2124-2, L2124-3 | 1 | 0.6697 |
| mp-112 | procedure | R2124-3 | ✅ R2124-3 | 1 | 0.7592 |
| mp-113 | piege | R2124-4 | ✅ R2124-4 | 6 | 0.6975 |
| mp-114 | fait | L2124-4, R2124-5 | ⚠️ L2124-4 | 1 | 0.7621 |
| mp-115 | redaction | R2123-5 | ✅ R2123-5 | 3 | 0.6868 |
| mp-116 | piege | R2123-6 | ❌ — | — | 0.6499 |
| mp-117 | procedure | R2123-2 | ✅ R2123-2 | 1 | 0.6982 |
| mp-118 | redaction | R2123-7 | ✅ R2123-7 | 2 | 0.6756 |
| mp-119 | piege (négatif) | — | — | — | 0.6335 |
| mp-120 | piege (négatif) | — | — | — | 0.6383 |
| mp-121 | piege (négatif) | — | — | — | 0.6374 |
| mp-122 | piege (négatif) | — | — | — | 0.6776 |

### Échecs de récupération — à examiner en priorité

- **mp-004** : attendus R2161-2 — remontés depuis 147-deuxieme-partie-marches-public, 154-deuxieme-partie-marches-public, 151-deuxieme-partie-marches-public
- **mp-009** : attendus L2152-1 — remontés depuis 036-deuxieme-partie-marches-public, 086-troisieme-partie-concessions-l, 147-deuxieme-partie-marches-public
- **mp-012** : attendus L2124-1 — remontés depuis 144-deuxieme-partie-marches-public, 134-deuxieme-partie-marches-public, 155-deuxieme-partie-marches-public
- **mp-025** : attendus R2144-4 — remontés depuis 108-deuxieme-partie-marches-public, 131-deuxieme-partie-marches-public, 131-deuxieme-partie-marches-public
- **mp-028** : attendus L2193-12 — remontés depuis 029-deuxieme-partie-marches-public, 134-deuxieme-partie-marches-public, 113-deuxieme-partie-marches-public
- **mp-047** : attendus R2111-2 — remontés depuis 105-deuxieme-partie-marches-public, 147-deuxieme-partie-marches-public, 005-deuxieme-partie-marches-public
- **mp-056** : attendus R2111-5 — remontés depuis 036-deuxieme-partie-marches-public, 128-deuxieme-partie-marches-public, 155-deuxieme-partie-marches-public
- **mp-057** : attendus L2111-1 — remontés depuis 155-deuxieme-partie-marches-public, 128-deuxieme-partie-marches-public, 128-deuxieme-partie-marches-public
- **mp-069** : attendus R2112-2 — remontés depuis 017-deuxieme-partie-marches-public, 128-deuxieme-partie-marches-public, 156-deuxieme-partie-marches-public
- **mp-070** : attendus R2112-3 — remontés depuis 143-deuxieme-partie-marches-public, 085-troisieme-partie-concessions-l, 178-troisieme-partie-concessions-l
- **mp-103** : attendus R2123-1 — remontés depuis 025-deuxieme-partie-marches-public, 128-deuxieme-partie-marches-public, 129-deuxieme-partie-marches-public
- **mp-116** : attendus R2123-6 — remontés depuis 025-deuxieme-partie-marches-public, 147-deuxieme-partie-marches-public, 106-deuxieme-partie-marches-public

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

- Embedding du corpus : **64 s** pour 2417 chunks (38 chunks/s).
- Construction de l'index : 735 ms.
- Empreinte de l'index : **9.9 Mo** en float32.

## Comment rejouer

```bash
python _chantier/scripts/reference_l15.py
```

Le corpus est vérifié par `tests/test_jeu_dore.py` contre son manifeste avant
toute comparaison : une référence établie sur un corpus dérivé ne vaut rien.
