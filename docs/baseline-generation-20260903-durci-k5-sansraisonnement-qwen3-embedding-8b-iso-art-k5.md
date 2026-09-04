# Palier génération — première mesure

**03/09/2026.** Complète `baseline-20260903.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=5**.

Montage : découpage par article (D12), `qwen3-embedding-8b` 4096 dim, k=5, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **3/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **22/135** |
| **Montant inventé** — somme absente des passages | **1/135** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 22 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **22/22** |
| Refuse *parfois* | 0/22 |
| Ne refuse **jamais** | **0/22** |
| *Observations écartées car tronquées* | *0* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**97/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **1.8 s** (min 0.4, max 11.9) sur 179 appels.

## Détail des anomalies

- **mp-003** — hors contexte : L2122-1
- **mp-004** — hors contexte : R2162-1
- **mp-005** — fantômes : L2161-1
- **mp-006** — hors contexte : R2161-1, R2161-10
- **mp-008** — montants : 90 000
- **mp-013** — hors contexte : R2162-7, R2162-8
- **mp-018** — hors contexte : L2193-1
- **mp-025** — hors contexte : L2142-1, R2142-1
- **mp-030** — hors contexte : R2162-18, R2162-21
- **mp-034** — hors contexte : L2123-1, R2123-1
- **mp-037** — hors contexte : L2191-1, L2191-2
- **mp-040** (exécution 2) — hors contexte : L2192-1
- **mp-057** — fantômes : L2111-9 · hors contexte : L2111-2, R2111-1, R2111-9
- **mp-066** — hors contexte : L2112-1
- **mp-071** — hors contexte : R2191-1
- **mp-075** — hors contexte : R2112-1
- **mp-082** — hors contexte : L2113-10
- **mp-093** — hors contexte : L2112-1
- **mp-096** — fantômes : L2172-4
- **mp-104** — hors contexte : R2161-6
- **mp-112** — hors contexte : L2122-1, R2122-1
- **mp-120** (exécution 2) — hors contexte : L2122-1, R2122-1
- **mp-120** (exécution 3) — hors contexte : L2122-1
- **mp-125** — hors contexte : R2152-7
- **mp-129** — hors contexte : R2162-7
- **mp-132** (exécution 1) — hors contexte : L2111-1
- **mp-132** (exécution 2) — hors contexte : L2111-1
- **mp-132** (exécution 3) — hors contexte : L2111-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
