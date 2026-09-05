# Palier génération — première mesure

**30/08/2026.** Complète `baseline-20260830.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=10**.

Montage : découpage par article (D12), `bge-m3` 1024 dim, k=10, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **2/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **6/135** |
| **Montant inventé** — somme absente des passages | **2/135** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 22 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **0/22** |
| Refuse *parfois* | 4/22 |
| Ne refuse **jamais** | **18/22** |
| *Observations écartées car tronquées* | *6* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**98/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **8.1 s** (min 4.3, max 14.7) sur 179 appels.

## Détail des anomalies

- **mp-008** — fantômes : L2161-1 · montants : 5 382 000
- **mp-015** — hors contexte : L2172-1
- **mp-018** — hors contexte : L2193-12
- **mp-034** — hors contexte : R2122-1 · montants : 140 000
- **mp-104** — fantômes : L2122-1-1 · hors contexte : L2122-1
- **mp-105** — hors contexte : L2111-1
- **mp-109** — hors contexte : L2172-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
