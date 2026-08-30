# Palier génération — première mesure

**30/08/2026.** Complète `baseline-20260830.md`.

Variante de consigne : **temoin**. Profondeur de recherche : **k=10**.

Montage : découpage par article (D12), `bge-m3` 1024 dim, k=10, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **6/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **19/135** |
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

Latence de génération : médiane **1.9 s** (min 0.4, max 9.4) sur 179 appels.

## Détail des anomalies

- **mp-002** — hors contexte : L2111-1, R2121-1
- **mp-008** — fantômes : L2161-1
- **mp-023** — hors contexte : L2123-1
- **mp-030** — fantômes : L2162-1
- **mp-038** — hors contexte : L2192-11
- **mp-047** — hors contexte : L2141-1
- **mp-048** — hors contexte : L2111-1, L2111-3, R2111-1, R2111-7
- **mp-052** — hors contexte : R2111-11
- **mp-078** (exécution 1) — hors contexte : L2194-1, R2194-7
- **mp-078** (exécution 2) — hors contexte : L2194-1, R2194-7
- **mp-078** (exécution 3) — hors contexte : L2194-1, R2194-7
- **mp-088** — fantômes : L5132-1
- **mp-100** — hors contexte : R2122-7
- **mp-106** — hors contexte : L2122-1
- **mp-107** — hors contexte : R2122-1, R2122-10
- **mp-109** — hors contexte : R2122-1
- **mp-112** — hors contexte : L2124-1, L2124-2
- **mp-113** — fantômes : L3122-1 · hors contexte : L2122-1
- **mp-114** — hors contexte : R2162-1
- **mp-120** (exécution 1) — fantômes : L2123-2 · hors contexte : R2123-1
- **mp-120** (exécution 2) — fantômes : L2123-2 · hors contexte : R2123-1
- **mp-127** — hors contexte : L2123-1
- **mp-128** — hors contexte : L2192-1
- **mp-129** — fantômes : L300-2
- **mp-132** (exécution 1) — hors contexte : L2111-1
- **mp-132** (exécution 2) — hors contexte : L2111-1
- **mp-132** (exécution 3) — hors contexte : L2111-1
- **mp-133** (exécution 2) — hors contexte : R2171-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
