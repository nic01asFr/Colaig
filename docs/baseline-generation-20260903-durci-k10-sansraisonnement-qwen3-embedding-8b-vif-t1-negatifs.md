# Palier génération — première mesure

**03/09/2026.** Complète `baseline-20260903.md`.

Variante de consigne : **durci**. Profondeur de recherche : **k=10**.

Montage : découpage par article (D12), `qwen3-embedding-8b` 4096 dim, k=10, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **5/22** |
| **Citation hors contexte** — article réel, absent des passages fournis | **9/22** |
| **Montant inventé** — somme absente des passages | **1/22** |

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

Latence de génération : médiane **12.5 s** (min 8.6, max 20.6) sur 66 appels.

## Détail des anomalies

- **mp-035** (exécution 2) — hors contexte : CCAG Travaux 1, R2112-2
- **mp-035** (exécution 3) — hors contexte : R2111-4, R2111-8, R2112-2
- **mp-062** (exécution 2) — hors contexte : L2111-1
- **mp-078** (exécution 1) — hors contexte : R2112-11
- **mp-078** (exécution 3) — hors contexte : R2112-6, R2112-9
- **mp-079** (exécution 1) — hors contexte : L2194-1
- **mp-079** (exécution 2) — hors contexte : L2194-1
- **mp-079** (exécution 3) — hors contexte : R2194-3
- **mp-080** (exécution 2) — montants : 140 000, 216 000, 432 000, 5 404 000
- **mp-097** (exécution 3) — fantômes : L212-1
- **mp-099** (exécution 1) — fantômes : L1414-3 · hors contexte : R2162-26
- **mp-099** (exécution 2) — hors contexte : L2113-8
- **mp-119** (exécution 2) — hors contexte : R2123-1, R2123-6
- **mp-120** (exécution 1) — hors contexte : R2124-4
- **mp-120** (exécution 2) — hors contexte : R2123-1
- **mp-120** (exécution 3) — hors contexte : L2122-1, R2122-1, R2123-5, R2124-4
- **mp-123** (exécution 1) — fantômes : R2192-1 · hors contexte : L2131-1, R2162-1
- **mp-123** (exécution 2) — hors contexte : L2131-1
- **mp-123** (exécution 3) — hors contexte : L2131-1, R2131-12, R2131-13, R2131-16, R2131-20, R2162-15
- **mp-130** (exécution 2) — fantômes : L2161-1
- **mp-131** (exécution 1) — hors contexte : L2111-1
- **mp-132** (exécution 3) — fantômes : R1111-2

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
