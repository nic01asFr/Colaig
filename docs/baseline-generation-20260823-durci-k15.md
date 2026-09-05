# Palier génération — première mesure

**23/08/2026.** Complète `baseline-20260823.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=15**.

Montage : découpage par article (D12), `bge-m3` 1024 dim, k=15, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **6/122** |
| **Citation hors contexte** — article réel, absent des passages fournis | **1/122** |
| **Montant inventé** — somme absente des passages | **0/122** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 21 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **16/20** |
| Refuse *parfois* | 3/20 |
| Ne refuse **jamais** | **1/20** |
| *Observations écartées car tronquées* | *9* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**84/101** cas positifs citent au moins un article attendu.

Latence de génération : médiane **15.9 s** (min 6.8, max 22.1) sur 164 appels.

## Détail des anomalies

- **mp-058** — fantômes : L3332-17-1
- **mp-063** — fantômes : R2112
- **mp-087** — fantômes : L344-2, L5213-13
- **mp-088** — fantômes : L5132-4
- **mp-099** (exécution 1) — fantômes : L1414-3
- **mp-102** — hors contexte : L2
- **mp-107** — fantômes : R2

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
