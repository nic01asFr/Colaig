# Palier génération — première mesure

**02/09/2026.** Complète `baseline-20260902.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=10**.

Montage : découpage par article (D12), `qwen3-embedding-8b` 4096 dim, k=10, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **4/22** |
| **Citation hors contexte** — article réel, absent des passages fournis | **4/22** |
| **Montant inventé** — somme absente des passages | **0/22** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 22 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **20/22** |
| Refuse *parfois* | 2/22 |
| Ne refuse **jamais** | **0/22** |
| *Observations écartées car tronquées* | *0* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**0/0** cas positifs citent au moins un article attendu.

Latence de génération : médiane **6.9 s** (min 4.7, max 17.5) sur 66 appels.

## Détail des anomalies

- **mp-035** (exécution 1) — hors contexte : R2152-7
- **mp-035** (exécution 2) — hors contexte : L2151-1
- **mp-035** (exécution 3) — fantômes : R215-1
- **mp-080** (exécution 2) — fantômes : L2162-1
- **mp-098** (exécution 1) — fantômes : R2113-10
- **mp-098** (exécution 3) — fantômes : R2113-10
- **mp-132** (exécution 1) — fantômes : R1111-2
- **mp-132** (exécution 2) — hors contexte : L2111-1
- **mp-132** (exécution 3) — hors contexte : L2111-1, R2111-1
- **mp-133** (exécution 1) — hors contexte : L2112-1
- **mp-133** (exécution 2) — hors contexte : L2112-1
- **mp-133** (exécution 3) — hors contexte : L2112-1
- **mp-134** (exécution 2) — hors contexte : R2171-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
