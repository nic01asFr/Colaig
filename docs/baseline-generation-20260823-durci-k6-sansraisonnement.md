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
| **Citation fantôme** — article cité inexistant dans le corpus | **10/124** |
| **Citation hors contexte** — article réel, absent des passages fournis | **22/124** |
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

**89/103** cas positifs citent au moins un article attendu.

Latence de génération : médiane **1.8 s** (min 0.8, max 8.8) sur 166 appels.

## Détail des anomalies

- **mp-003** — hors contexte : L2122-1
- **mp-005** — hors contexte : L2113-10
- **mp-008** — fantômes : L2161-1 · hors contexte : R2162-1 · montants : 144 000
- **mp-013** (exécution 2) — hors contexte : L2111-1
- **mp-020** — hors contexte : R2122-1
- **mp-023** — hors contexte : L2152-1
- **mp-025** — hors contexte : R2143-1
- **mp-030** — fantômes : L2162-1 · hors contexte : R2162-24
- **mp-031** — hors contexte : R2194-4, R2194-5
- **mp-033** — hors contexte : L2111-1
- **mp-041** — hors contexte : R2161-6, R3124-1
- **mp-047** — fantômes : L30, L30-1
- **mp-056** — hors contexte : R2111-7, R2111-8
- **mp-069** — hors contexte : R2161-1
- **mp-072** — hors contexte : L2112-1
- **mp-086** — hors contexte : L2111-1, R2111-1
- **mp-087** — fantômes : R216-1
- **mp-088** — fantômes : L5132-1
- **mp-091** — fantômes : R2113
- **mp-093** — fantômes : L2161-1 · hors contexte : R2161-1
- **mp-094** — hors contexte : L2113-1
- **mp-100** — montants : 1 000 000, 40 000
- **mp-104** — hors contexte : R2161-1, R2171-1
- **mp-105** — fantômes : R2173-1
- **mp-107** — hors contexte : R2122-6
- **mp-111** — fantômes : L2162-1
- **mp-113** — hors contexte : L2124-1, R2124-1
- **mp-116** — hors contexte : R2122-1
- **mp-118** — hors contexte : R2152-7
- **mp-120** (exécution 2) — fantômes : L2123, R2161

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
