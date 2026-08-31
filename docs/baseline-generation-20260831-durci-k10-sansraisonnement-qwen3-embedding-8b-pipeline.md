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
| **Citation fantôme** — article cité inexistant dans le corpus | **10/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **23/135** |
| **Montant inventé** — somme absente des passages | **1/135** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 22 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **19/22** |
| Refuse *parfois* | 3/22 |
| Ne refuse **jamais** | **0/22** |
| *Observations écartées car tronquées* | *1* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**95/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **8.4 s** (min 5.0, max 46.1) sur 179 appels.

## Détail des anomalies

- **mp-008** — fantômes : L2161-1
- **mp-011** — montants : 39 999
- **mp-021** — hors contexte : L2191-1
- **mp-023** — hors contexte : L2123-1
- **mp-025** — hors contexte : R2143-1
- **mp-034** — hors contexte : L2122-1
- **mp-035** (exécution 1) — hors contexte : R2151-1, R2152-1
- **mp-035** (exécution 2) — hors contexte : R2161-11, R2161-12
- **mp-035** (exécution 3) — hors contexte : L2151-1, R2152-1
- **mp-040** (exécution 1) — hors contexte : R2192-30
- **mp-053** — hors contexte : L2111-1
- **mp-055** — fantômes : R111-17
- **mp-067** — fantômes : L2112-8 · hors contexte : L2112-1, R2112-1
- **mp-069** — hors contexte : R2151-1, R2161-1
- **mp-077** — hors contexte : L2112-1
- **mp-078** (exécution 1) — hors contexte : L2112-1
- **mp-078** (exécution 2) — hors contexte : L2194-1
- **mp-078** (exécution 3) — hors contexte : L2112-1, R2112-11
- **mp-084** — hors contexte : L2112-1
- **mp-085** — hors contexte : R2113-6
- **mp-088** — fantômes : L5132-1
- **mp-094** — fantômes : L1414-1
- **mp-097** (exécution 2) — fantômes : L212-1
- **mp-097** (exécution 3) — fantômes : L212-1
- **mp-098** (exécution 1) — fantômes : R2113-10
- **mp-099** (exécution 3) — fantômes : L2161-1 · hors contexte : R2161-1, R2161-12
- **mp-104** — hors contexte : L2122-1
- **mp-106** — hors contexte : L2141-1
- **mp-107** — hors contexte : R2194-10
- **mp-114** — hors contexte : R2161-6
- **mp-123** (exécution 1) — fantômes : L2111-4 · hors contexte : L2111-1
- **mp-126** — fantômes : R2192-1
- **mp-127** — hors contexte : R2191-1
- **mp-132** (exécution 1) — hors contexte : L2111-1
- **mp-133** (exécution 1) — hors contexte : R2162-1
- **mp-134** (exécution 1) — hors contexte : L2172-1
- **mp-134** (exécution 2) — hors contexte : R2171-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
