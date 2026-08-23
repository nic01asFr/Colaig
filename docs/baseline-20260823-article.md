# Rapport de référence — 23/08/2026

**Lot L1.5.** Référence contre laquelle toute modification du pipeline se juge.
Reproductible : corpus figé et vérifié par manifeste, jeu doré versionné,
`_chantier/scripts/reference_l15.py`.

## Montage

| | |
|---|---|
| Corpus | 185 documents, Code de la commande publique, articles en vigueur |
| Découpage | `Chunker(800, 100)` — paramètres de `config.py` — **1762 chunks** |
| Embeddings | `BAAI/bge-m3`, 1024 dimensions (défaut D10) |
| Index | `FaissStore` / `IndexFlatIP`, recherche exacte |
| k | 6 passages, valeur de `max_results` de l'espace |
| Jeu doré | 20 cas, dont 4 négatifs |

## Palier 1 — récupération (déterministe)

Deux exécutions donnent le même résultat : ni juge, ni génération. **C'est le
socle.** Si le passage n'est pas remonté, aucune génération ne le rattrapera.

Sur les **17 cas ayant des articles attendus** :

| | cas | part |
|---|---|---|
| Tous les articles attendus remontés | **13** | 76 % |
| Partiellement remontés | 2 | 12 % |
| Aucun remonté | 2 | 12 % |

Rang du premier article attendu : médiane **1**, moyenne 1.7, max 5 (sur k=6).

Latence de recherche : médiane **0.0 ms** (min 0.0, max 0.0).
Embedding d'une question : **12 ms** en moyenne.

### Détail par cas

| cas | type | attendus | remontés | rang | score max |
|---|---|---|---|---|---|
| mp-001 | fait | R2112-1 | ✅ R2112-1 | 1 | 0.7125 |
| mp-002 | fait | L2112-1, R2112-1 | ✅ L2112-1, R2112-1 | 1 | 0.5917 |
| mp-003 | fait | R2122-8 | ✅ R2122-8 | 1 | 0.7364 |
| mp-004 | fait | R2161-2 | ✅ R2161-2 | 3 | 0.7468 |
| mp-005 | procedure | L2113-10, L2113-11 | ⚠️ L2113-11 | 1 | 0.5438 |
| mp-006 | procedure | L2124-2 | ✅ L2124-2 | 1 | 0.6403 |
| mp-007 | procedure | L2193-3 | ✅ L2193-3 | 1 | 0.712 |
| mp-008 | redaction | L2113-10, R2112-1, R2122-8 | ⚠️ R2112-1, R2122-8 | 1 | 0.5986 |
| mp-009 | procedure | L2152-1 | ✅ L2152-1 | 5 | 0.6932 |
| mp-010 | redaction | L2111-1 | ✅ L2111-1 | 3 | 0.6699 |
| mp-011 | fait | R2196-1 | ✅ R2196-1 | 1 | 0.6996 |
| mp-012 | piege | L2124-1 | ❌ — | — | 0.6242 |
| mp-013 | piege (négatif) | — | — | — | 0.5205 |
| mp-014 | piege (négatif) | — | — | — | 0.6287 |
| mp-015 | procedure | R2151-1 | ❌ — | — | 0.6979 |
| mp-016 | procedure | L2123-1 | ✅ L2123-1 | 1 | 0.6926 |
| mp-017 | fait | L2113-10 | ✅ L2113-10 | 4 | 0.653 |
| mp-018 | redaction | L2193-3 | ✅ L2193-3 | 1 | 0.5989 |
| mp-019 | piege (négatif) | — | — | — | 0.4871 |
| mp-020 | fait | R2122-8 | ✅ R2122-8 | 1 | 0.645 |

### Échecs de récupération — à examiner en priorité

- **mp-012** : attendus L2124-1 — remontés depuis 144-deuxieme-partie-marches-public, 134-deuxieme-partie-marches-public, 128-deuxieme-partie-marches-public
- **mp-015** : attendus R2151-1 — remontés depuis 154-deuxieme-partie-marches-public, 151-deuxieme-partie-marches-public, 154-deuxieme-partie-marches-public

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

- Embedding du corpus : **16 s** pour 1762 chunks (113 chunks/s).
- Construction de l'index : 31 ms.
- Empreinte de l'index : **7.2 Mo** en float32.

## Comment rejouer

```bash
python _chantier/scripts/reference_l15.py
```

Le corpus est vérifié par `tests/test_jeu_dore.py` contre son manifeste avant
toute comparaison : une référence établie sur un corpus dérivé ne vaut rien.
