# Palier génération — première mesure

**28/08/2026.** Complète `baseline-20260828.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=10**.

Montage : découpage par article (D12), `bge-m3` 1024 dim, k=10, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **8/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **18/135** |
| **Montant inventé** — somme absente des passages | **0/135** |

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

**95/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **2.0 s** (min 0.5, max 7.1) sur 179 appels.

## Détail des anomalies

- **mp-005** — fantômes : L2162-1 · hors contexte : R2162-1
- **mp-023** — hors contexte : L2123-1
- **mp-030** — fantômes : L2162-1
- **mp-031** — hors contexte : R2194-4, R2194-5
- **mp-038** — fantômes : L441-6 · hors contexte : R2192-21
- **mp-040** (exécution 1) — hors contexte : R2191-1
- **mp-040** (exécution 2) — fantômes : R2192-1, R2192-2
- **mp-041** — hors contexte : R2151-1
- **mp-047** — hors contexte : L2141-1
- **mp-048** — fantômes : L215, L215-1, L215-4, R215, R215-1, R215-10
- **mp-056** — fantômes : L111-4
- **mp-065** — hors contexte : L2112-1
- **mp-077** — hors contexte : L2194-1
- **mp-078** (exécution 1) — hors contexte : L2194-1
- **mp-078** (exécution 3) — hors contexte : L2194-1
- **mp-094** — hors contexte : L2113-1
- **mp-104** — hors contexte : L2125-1
- **mp-112** — hors contexte : R2124-1
- **mp-113** — fantômes : L3122-1 · hors contexte : L2122-1
- **mp-119** (exécution 1) — hors contexte : R2161-1
- **mp-119** (exécution 2) — hors contexte : L2152-1
- **mp-130** (exécution 1) — fantômes : R2191-10-1
- **mp-131** (exécution 1) — hors contexte : L2111-1
- **mp-132** (exécution 1) — hors contexte : L2111-1
- **mp-132** (exécution 2) — hors contexte : L2111-1
- **mp-132** (exécution 3) — hors contexte : L2111-1
- **mp-133** (exécution 1) — hors contexte : L2112-1, R2112-1
- **mp-133** (exécution 2) — hors contexte : L2111-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
