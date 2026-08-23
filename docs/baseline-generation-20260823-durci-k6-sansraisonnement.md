# Palier génération — première mesure

**23/08/2026.** Complète `baseline-20260823.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=6**.

Montage : découpage par article (D12), `bge-m3` 1024 dim, k=6, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **6/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **30/135** |
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

**90/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **2.0 s** (min 0.5, max 11.7) sur 179 appels.

## Détail des anomalies

- **mp-005** — hors contexte : L2152-1, L2152-2
- **mp-007** — hors contexte : L2193-4
- **mp-008** — hors contexte : R2162-1
- **mp-023** — fantômes : L2161-1
- **mp-025** — hors contexte : R2143-1
- **mp-030** — hors contexte : R2162-15, R2162-16
- **mp-031** — hors contexte : R2194-10
- **mp-037** — hors contexte : R2191-11
- **mp-040** (exécution 1) — fantômes : R2192-1
- **mp-040** (exécution 2) — fantômes : L2161-1 · hors contexte : R2161-1
- **mp-040** (exécution 3) — hors contexte : R2162-7
- **mp-047** — hors contexte : L3
- **mp-048** — hors contexte : L2111-1
- **mp-053** — hors contexte : L2122-1
- **mp-056** — fantômes : L312-2 · hors contexte : R2152-7
- **mp-057** — hors contexte : L2111-1
- **mp-064** — hors contexte : L2112-1
- **mp-069** — hors contexte : L2112-1
- **mp-071** — hors contexte : L2112-1
- **mp-086** — hors contexte : L2111-1, R2111-1
- **mp-093** — fantômes : L2161-1
- **mp-100** — hors contexte : L2123-1
- **mp-104** — hors contexte : R2122-1
- **mp-112** — hors contexte : L2124-1, R2162-3
- **mp-113** — hors contexte : L2124-1, R2124-1
- **mp-115** — hors contexte : R2161-1, R2161-22
- **mp-116** — hors contexte : L2122-1, R2122-1
- **mp-119** (exécution 3) — hors contexte : R2123-1
- **mp-120** (exécution 3) — fantômes : L2123-2 · hors contexte : R2123-1
- **mp-125** — hors contexte : R2152-7
- **mp-126** — fantômes : R2174-1
- **mp-129** — hors contexte : R2162-7
- **mp-132** (exécution 1) — hors contexte : L2111-1
- **mp-132** (exécution 2) — hors contexte : L2111-1
- **mp-132** (exécution 3) — hors contexte : L2111-1
- **mp-133** (exécution 3) — hors contexte : L2151-1
- **mp-134** (exécution 2) — hors contexte : R2171-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
