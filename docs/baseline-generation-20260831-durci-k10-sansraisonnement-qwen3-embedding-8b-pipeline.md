# Palier génération — première mesure

**31/08/2026.** Complète `baseline-20260831.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=10**.

Montage : découpage par article (D12), `qwen3-embedding-8b` 4096 dim, k=10, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **5/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **8/135** |
| **Montant inventé** — somme absente des passages | **2/135** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 21 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **18/21** |
| Refuse *parfois* | 3/21 |
| Ne refuse **jamais** | **0/21** |
| *Observations écartées car tronquées* | *1* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**97/112** cas positifs citent au moins un article attendu.

Latence de génération : médiane **7.7 s** (min 4.6, max 17.9) sur 174 appels.

## Détail des anomalies

- **mp-008** — hors contexte : L2122-1 · montants : 5 538 000
- **mp-022** — fantômes : L311-1
- **mp-035** (exécution 1) — fantômes : R215-1
- **mp-039** — hors contexte : L2191-1
- **mp-059** (exécution 2) — montants : 50 000 000
- **mp-072** — hors contexte : L2112-1
- **mp-085** — hors contexte : L2113-11
- **mp-086** — hors contexte : L2113-1
- **mp-098** (exécution 1) — fantômes : R2113-10
- **mp-098** (exécution 2) — fantômes : R2113-10
- **mp-098** (exécution 3) — fantômes : R2113-10
- **mp-123** (exécution 2) — hors contexte : CCAG Fournitures et services 5, CCAG Prestations intellectuelles 5
- **mp-125** — hors contexte : L2111-1
- **mp-127** — fantômes : L219-1
- **mp-132** (exécution 1) — fantômes : R1111-2
- **mp-132** (exécution 2) — hors contexte : L2111-1, R2111-1
- **mp-132** (exécution 3) — hors contexte : L2111-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
