# Palier génération — première mesure

**01/09/2026.** Complète `baseline-20260901.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=10**.

Montage : découpage par article (D12), `qwen3-embedding-8b` 4096 dim, k=10, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **11/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **13/135** |
| **Montant inventé** — somme absente des passages | **3/135** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 22 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **17/22** |
| Refuse *parfois* | 5/22 |
| Ne refuse **jamais** | **0/22** |
| *Observations écartées car tronquées* | *1* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**97/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **8.1 s** (min 4.2, max 27.7) sur 179 appels.

## Détail des anomalies

- **mp-005** — hors contexte : L2112-1
- **mp-006** — fantômes : L2162-1, L2164-1
- **mp-008** — fantômes : L2161-1 · montants : 5 382 000
- **mp-018** — hors contexte : L2193-6
- **mp-026** — hors contexte : L2152-3
- **mp-034** — fantômes : L2121-1 · hors contexte : L2122-1
- **mp-035** (exécution 1) — hors contexte : L2151-1, R2151-7
- **mp-035** (exécution 2) — fantômes : L215 · hors contexte : R2152-7
- **mp-035** (exécution 3) — hors contexte : R2112-1, R2151-3
- **mp-047** — hors contexte : L2122-1
- **mp-059** (exécution 1) — montants : 50 000 000
- **mp-065** — fantômes : L2151-4
- **mp-066** — hors contexte : L2112-1
- **mp-072** — hors contexte : L2113-1
- **mp-080** (exécution 3) — montants : 140 000, 214 000
- **mp-094** — fantômes : L1414-1
- **mp-097** (exécution 3) — fantômes : L212-1
- **mp-098** (exécution 2) — fantômes : R2113-10
- **mp-098** (exécution 3) — fantômes : R2113-10
- **mp-107** — hors contexte : R2122-5
- **mp-113** — fantômes : L3122-1 · hors contexte : L2122-1
- **mp-126** — fantômes : R2141-27
- **mp-127** — fantômes : L2161-1
- **mp-132** (exécution 1) — hors contexte : L2111-1
- **mp-132** (exécution 3) — hors contexte : L2111-1
- **mp-133** (exécution 2) — hors contexte : L2112-1
- **mp-134** (exécution 2) — hors contexte : L2112-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
