# Palier génération — première mesure

**02/09/2026.** Complète `baseline-20260902.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=10**.

Montage : découpage par article (D12), `qwen3-embedding-8b` 4096 dim, k=10, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **10/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **16/135** |
| **Montant inventé** — somme absente des passages | **1/135** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 22 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **19/22** |
| Refuse *parfois* | 2/22 |
| Ne refuse **jamais** | **1/22** |
| *Observations écartées car tronquées* | *0* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**96/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **6.4 s** (min 4.6, max 13.5) sur 179 appels.

## Détail des anomalies

- **mp-006** — fantômes : L2162-1, L2163-1
- **mp-007** — hors contexte : L2193-1
- **mp-008** — hors contexte : L2122-1
- **mp-022** — fantômes : L551-1
- **mp-025** — hors contexte : R2143-1
- **mp-034** — fantômes : L2121-1 · hors contexte : L2122-1, R2122-1
- **mp-035** (exécution 2) — fantômes : R215-1
- **mp-035** (exécution 3) — hors contexte : R2151-1
- **mp-047** — hors contexte : L2141-1
- **mp-049** — fantômes : L2111-4
- **mp-054** — hors contexte : L2112-1
- **mp-062** (exécution 2) — fantômes : L211-1
- **mp-065** — hors contexte : L2112-1, R2112-1, R2112-12
- **mp-072** — hors contexte : L2112-1
- **mp-094** — fantômes : L1414-1
- **mp-098** (exécution 2) — fantômes : R2113-10
- **mp-100** — hors contexte : R2122-1
- **mp-104** — fantômes : L2162-1
- **mp-110** — hors contexte : L2122-1, R2122-1
- **mp-113** — hors contexte : L2112-1
- **mp-121** — montants : 143 000
- **mp-124** — hors contexte : R2161-1
- **mp-126** — fantômes : L2161-1 · hors contexte : R2161-1
- **mp-132** (exécution 3) — hors contexte : L2111-1
- **mp-133** (exécution 2) — hors contexte : L2112-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
