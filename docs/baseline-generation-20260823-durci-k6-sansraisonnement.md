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
| **Citation fantôme** — article cité inexistant dans le corpus | **4/122** |
| **Citation hors contexte** — article réel, absent des passages fournis | **26/122** |
| **Montant inventé** — somme absente des passages | **1/122** |

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

**88/101** cas positifs citent au moins un article attendu.

Latence de génération : médiane **2.0 s** (min 1.0, max 5.9) sur 164 appels.

## Détail des anomalies

- **mp-003** — hors contexte : L2122-1
- **mp-005** — fantômes : L2161-1
- **mp-007** — hors contexte : L2193-1
- **mp-008** — fantômes : L2161-1 · montants : 150 000
- **mp-011** — hors contexte : R2396-1
- **mp-015** — hors contexte : R2161-8
- **mp-023** — hors contexte : L2191-1, R2191-1
- **mp-024** — hors contexte : R2162-1
- **mp-025** — hors contexte : R2143-1
- **mp-030** — hors contexte : R2162-24, R2162-25
- **mp-031** — hors contexte : R2194-5
- **mp-033** — hors contexte : L2111-1
- **mp-034** — hors contexte : R2122-1, R2122-2
- **mp-035** (exécution 2) — fantômes : L2121-1 · hors contexte : R2121-1
- **mp-035** (exécution 3) — hors contexte : L2141-1, R2161-1
- **mp-047** — hors contexte : L2112-1, L2141-1
- **mp-053** — hors contexte : L2122-1
- **mp-054** — hors contexte : L2141-1, L2311-1
- **mp-057** — hors contexte : L2111-1
- **mp-081** — hors contexte : R2113-1
- **mp-094** — hors contexte : L2113-1, L2113-2, L2113-5
- **mp-100** — hors contexte : R2122-1
- **mp-101** — hors contexte : L2323-1
- **mp-104** — hors contexte : R2161-1
- **mp-105** — fantômes : R2173-1
- **mp-106** — hors contexte : R2122-1, R2122-2
- **mp-107** — hors contexte : R2122-6
- **mp-112** — hors contexte : R2162-3
- **mp-114** — hors contexte : L2124-1
- **mp-116** — hors contexte : R2122-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
