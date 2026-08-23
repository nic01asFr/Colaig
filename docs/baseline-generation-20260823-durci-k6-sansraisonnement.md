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
| **Citation fantôme** — article cité inexistant dans le corpus | **4/124** |
| **Citation hors contexte** — article réel, absent des passages fournis | **17/124** |
| **Montant inventé** — somme absente des passages | **2/124** |

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

**87/103** cas positifs citent au moins un article attendu.

Latence de génération : médiane **1.8 s** (min 0.5, max 6.3) sur 166 appels.

## Détail des anomalies

- **mp-005** — fantômes : L2161-1
- **mp-008** — fantômes : L2161-1 · hors contexte : R2161-1 · montants : 140 000, 5 382 000
- **mp-030** — hors contexte : R2162-20, R2162-21
- **mp-031** — hors contexte : R2194-10
- **mp-034** — montants : 140 000
- **mp-037** — hors contexte : L2191-1, R2191-2
- **mp-038** — hors contexte : R2192-26
- **mp-047** — fantômes : L30, L30-1
- **mp-057** — hors contexte : L2111-1
- **mp-066** — hors contexte : L2112-1
- **mp-069** — hors contexte : R2112-1
- **mp-070** — hors contexte : L2141-1
- **mp-085** — hors contexte : R2111-1
- **mp-094** — hors contexte : L2113-1
- **mp-100** — hors contexte : R2122-1
- **mp-104** — hors contexte : R2122-1, R2161-1
- **mp-106** — hors contexte : L2122-1
- **mp-113** — hors contexte : L2124-1, R2124-1
- **mp-116** — hors contexte : L2124-1
- **mp-120** (exécution 1) — hors contexte : L2122-1
- **mp-120** (exécution 3) — hors contexte : L2122-1
- **mp-121** (exécution 1) — fantômes : L212-1
- **mp-121** (exécution 2) — fantômes : L212-1
- **mp-121** (exécution 3) — fantômes : L212-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
