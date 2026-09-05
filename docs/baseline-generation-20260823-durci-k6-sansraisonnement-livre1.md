# Palier génération — première mesure

**23/08/2026.** Complète `baseline-20260823.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=6**.

Montage : découpage par article (D12), `bge-m3` 1024 dim, k=6, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **6/124** |
| **Citation hors contexte** — article réel, absent des passages fournis | **20/124** |
| **Montant inventé** — somme absente des passages | **0/124** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 21 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **21/21** |
| Refuse *parfois* | 0/21 |
| Ne refuse **jamais** | **0/21** |
| *Observations écartées car tronquées* | *0* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**90/103** cas positifs citent au moins un article attendu.

Latence de génération : médiane **1.8 s** (min 0.5, max 10.1) sur 166 appels.

## Détail des anomalies

- **mp-005** — hors contexte : L2113-10
- **mp-008** — fantômes : L2161-1 · hors contexte : R2161-1
- **mp-010** — fantômes : L2121-1 · hors contexte : R2111-2
- **mp-013** (exécution 1) — hors contexte : L2113-1
- **mp-013** (exécution 2) — hors contexte : R2162-1
- **mp-018** — hors contexte : R2193-4
- **mp-025** — hors contexte : R2143-1
- **mp-026** — hors contexte : L2152-1
- **mp-030** — hors contexte : R2162-20, R2162-21
- **mp-031** — hors contexte : R2194-2, R2194-3, R2194-4, R2194-5
- **mp-047** — fantômes : L3001-1
- **mp-048** — hors contexte : R2111-1, R2111-15, R2111-9
- **mp-053** — hors contexte : L2112-4
- **mp-057** — hors contexte : L2111-1
- **mp-069** — hors contexte : R2191-1
- **mp-071** — hors contexte : R2112-5
- **mp-085** — hors contexte : R2112-1
- **mp-086** — hors contexte : R2113-1, R2113-4
- **mp-104** — fantômes : L2122-1-1 · hors contexte : R2122-1, R2172-1
- **mp-106** — hors contexte : L2122-1, R2122-2
- **mp-111** — fantômes : L2162-1
- **mp-113** — hors contexte : L2122-1
- **mp-120** (exécution 2) — hors contexte : R2122-1
- **mp-121** (exécution 1) — fantômes : L2121-1
- **mp-121** (exécution 2) — fantômes : L212-1
- **mp-121** (exécution 3) — fantômes : L212-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
