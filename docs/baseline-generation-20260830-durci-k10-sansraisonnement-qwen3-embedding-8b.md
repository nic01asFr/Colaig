# Palier génération — première mesure

**30/08/2026.** Complète `baseline-20260830.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=10**.

Montage : découpage par article (D12), `qwen3-embedding-8b` 4096 dim, k=10, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **9/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **17/135** |
| **Montant inventé** — somme absente des passages | **0/135** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 22 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **22/22** |
| Refuse *parfois* | 0/22 |
| Ne refuse **jamais** | **0/22** |
| *Observations écartées car tronquées* | *0* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**96/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **1.9 s** (min 0.6, max 8.2) sur 179 appels.

## Détail des anomalies

- **mp-006** — fantômes : L2162-1
- **mp-008** — fantômes : L2161-1 · hors contexte : L2122-1
- **mp-015** — hors contexte : L2151-1
- **mp-023** — hors contexte : R2112-5
- **mp-034** — hors contexte : L2122-1, L2123-1
- **mp-039** — fantômes : R2191-19
- **mp-056** — hors contexte : L2111-1, R2111-1
- **mp-066** — hors contexte : L2112-1
- **mp-069** — hors contexte : R2162-1
- **mp-070** — fantômes : L2161-1 · hors contexte : L2112-1
- **mp-079** (exécution 2) — hors contexte : L2194-1
- **mp-088** — hors contexte : R2113-1
- **mp-094** — fantômes : L1414-1
- **mp-098** (exécution 1) — fantômes : R2113-10
- **mp-098** (exécution 3) — fantômes : R2113-10
- **mp-110** — hors contexte : L2122-1
- **mp-112** — fantômes : L2121-1
- **mp-118** — hors contexte : R2162-1
- **mp-120** (exécution 1) — fantômes : L3111-1
- **mp-125** — hors contexte : R2162-7
- **mp-129** — hors contexte : R2162-7
- **mp-130** (exécution 3) — hors contexte : R2192-34
- **mp-132** (exécution 1) — hors contexte : L2111-1
- **mp-132** (exécution 2) — hors contexte : L2111-1
- **mp-132** (exécution 3) — hors contexte : L2111-1
- **mp-133** (exécution 1) — fantômes : L2166-1, R2166-1
- **mp-133** (exécution 2) — hors contexte : R2171-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
