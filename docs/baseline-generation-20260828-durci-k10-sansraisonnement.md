# Palier génération — première mesure

**28/08/2026.** Complète `baseline-20260828.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=10**.

Montage : découpage par article (D12), `bge-m3` 1024 dim, k=10, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **10/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **19/135** |
| **Montant inventé** — somme absente des passages | **2/135** |

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

**90/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **2.0 s** (min 0.5, max 8.5) sur 179 appels.

## Détail des anomalies

- **mp-005** — fantômes : L2161-1
- **mp-008** — hors contexte : R2162-7
- **mp-020** — hors contexte : L2122-1
- **mp-023** — hors contexte : R2122-1, R2191-1
- **mp-030** — fantômes : L2162-1
- **mp-034** — montants : 140 000, 215 000, 750 000
- **mp-047** — hors contexte : L2141-1
- **mp-048** — hors contexte : L2111-1, R2111-1
- **mp-071** — hors contexte : L2112-1
- **mp-077** — hors contexte : L2112-1
- **mp-078** (exécution 1) — hors contexte : L2194-1, R2194-7
- **mp-078** (exécution 2) — hors contexte : L2194-1, R2194-7
- **mp-078** (exécution 3) — hors contexte : L2194-1, R2194-7
- **mp-080** (exécution 1) — fantômes : R212-1
- **mp-080** (exécution 3) — fantômes : L212-1, R212-1
- **mp-085** — hors contexte : R2113-3
- **mp-094** — fantômes : L1414-1
- **mp-095** — fantômes : L2167-1
- **mp-104** — fantômes : L2122-2-1, L2122-3 · hors contexte : L2122-1
- **mp-106** — hors contexte : L2122-1, R2122-1
- **mp-110** — hors contexte : L2122-1
- **mp-112** — hors contexte : L2124-1, R2124-1
- **mp-113** — fantômes : L3122-1 · hors contexte : L2122-1
- **mp-116** — fantômes : L2123-9
- **mp-120** (exécution 1) — hors contexte : L2122-1
- **mp-121** — fantômes : L212-1 · montants : 215 000
- **mp-127** — hors contexte : L2123-1
- **mp-128** — hors contexte : L2123-1
- **mp-129** — fantômes : L300-2
- **mp-130** (exécution 3) — hors contexte : R2191-1
- **mp-132** (exécution 1) — hors contexte : L2111-1
- **mp-132** (exécution 2) — hors contexte : L2111-1
- **mp-132** (exécution 3) — hors contexte : L2111-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
