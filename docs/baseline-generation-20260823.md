# Palier génération — première mesure

**23/08/2026.** Complète `baseline-20260823.md`.

Montage : découpage par article (D12), `bge-m3` 1024 dim, k=6, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **0/45** |
| **Citation hors contexte** — article réel, absent des passages fournis | **0/45** |
| **Montant inventé** — somme absente des passages | **0/45** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 8 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **0/8** |
| Refuse *parfois* | 0/8 |
| Ne refuse **jamais** | **8/8** |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**0/37** cas positifs citent au moins un article attendu.

Latence de génération : médiane **4.9 s** (min 4.8, max 5.0) sur 61 appels.

## Détail des anomalies

*Aucune.*

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
