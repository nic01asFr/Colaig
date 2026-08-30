# Palier génération — première mesure

**30/08/2026.** Complète `baseline-20260830.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=10**.

Montage : découpage par article (D12), `qwen3-embedding-8b` 4096 dim, k=10, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **14/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **23/135** |
| **Montant inventé** — somme absente des passages | **1/135** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 22 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **18/22** |
| Refuse *parfois* | 4/22 |
| Ne refuse **jamais** | **0/22** |
| *Observations écartées car tronquées* | *2* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**96/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **6.5 s** (min 3.7, max 15.5) sur 179 appels.

## Détail des anomalies

- **mp-006** — hors contexte : L2122-1, R2122-1
- **mp-008** — fantômes : L2155-1 · montants : 5 538 000, 90 000
- **mp-019** (exécution 1) — hors contexte : L2142-1
- **mp-023** — hors contexte : L2124-1
- **mp-024** — fantômes : L2131-4 · hors contexte : R2131-5
- **mp-032** — fantômes : L441-6
- **mp-034** — fantômes : L2121-1 · hors contexte : R2121-1
- **mp-035** (exécution 1) — hors contexte : R2152-7, R2152-8
- **mp-035** (exécution 2) — hors contexte : L2151-1
- **mp-035** (exécution 3) — fantômes : L2121-1 · hors contexte : L2111-1
- **mp-046** — fantômes : L2122-4, L2161-1 · hors contexte : L2122-1, L2171-1
- **mp-047** — hors contexte : L2141-1
- **mp-053** — hors contexte : L2152-1
- **mp-054** — fantômes : R2141-1 · hors contexte : L2141-1
- **mp-055** — fantômes : R111-19
- **mp-059** (exécution 2) — fantômes : D2111-1
- **mp-061** — hors contexte : R2191-38
- **mp-062** (exécution 1) — hors contexte : R2112-10
- **mp-065** — hors contexte : L2194-1
- **mp-070** — hors contexte : L2112-1, R2112-7
- **mp-077** — hors contexte : L2194-1
- **mp-078** (exécution 1) — hors contexte : L2112-1, L2112-2
- **mp-078** (exécution 2) — hors contexte : L2112-1
- **mp-078** (exécution 3) — hors contexte : L2194-1, L2194-3, R2194-4
- **mp-083** — fantômes : L2161-1 · hors contexte : R2161-1
- **mp-084** — hors contexte : L2113-1
- **mp-086** — hors contexte : L2151-1, R2151-1
- **mp-098** (exécution 1) — fantômes : R2113-10
- **mp-123** (exécution 1) — hors contexte : L2112-1
- **mp-123** (exécution 2) — fantômes : L30
- **mp-126** — hors contexte : L2192-1
- **mp-128** — fantômes : L441-1
- **mp-132** (exécution 3) — hors contexte : L2111-1
- **mp-133** (exécution 3) — fantômes : R2164-1 · hors contexte : R2162-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
