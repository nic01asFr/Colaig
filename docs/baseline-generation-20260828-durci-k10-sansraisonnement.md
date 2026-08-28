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
| **Citation fantôme** — article cité inexistant dans le corpus | **7/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **16/135** |
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

**90/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **1.8 s** (min 0.5, max 7.3) sur 179 appels.

## Détail des anomalies

- **mp-005** — fantômes : L2161-1
- **mp-008** — hors contexte : R2162-1
- **mp-023** — hors contexte : R2122-1, R2191-1
- **mp-035** (exécution 1) — hors contexte : R2151-1
- **mp-047** — hors contexte : L2112-1, L2141-1
- **mp-048** — hors contexte : L2111-1, L2111-3, R2111-1, R2111-7
- **mp-077** — hors contexte : L2112-1
- **mp-078** (exécution 1) — hors contexte : L2194-1, R2194-7
- **mp-078** (exécution 2) — hors contexte : L2194-1, R2194-7
- **mp-078** (exécution 3) — hors contexte : L2194-1, R2194-7
- **mp-089** — fantômes : L2162-11
- **mp-094** — fantômes : L1414-1, L1414-2
- **mp-104** — hors contexte : L2122-1
- **mp-106** — hors contexte : L2122-1, R2122-1
- **mp-112** — fantômes : L2121-1 · hors contexte : L2122-1, R2121-1
- **mp-113** — fantômes : L3122-1 · hors contexte : L2122-1
- **mp-119** (exécution 2) — hors contexte : L2124-1
- **mp-120** (exécution 1) — fantômes : L2123-2 · hors contexte : R2123-1
- **mp-120** (exécution 3) — fantômes : L2123-2
- **mp-127** — fantômes : L218-10
- **mp-130** (exécution 2) — hors contexte : L2191-1
- **mp-130** (exécution 3) — hors contexte : R2191-1
- **mp-132** (exécution 1) — hors contexte : L2111-1
- **mp-132** (exécution 2) — hors contexte : L2111-1
- **mp-132** (exécution 3) — hors contexte : L2111-1
- **mp-133** (exécution 1) — hors contexte : R2171-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
