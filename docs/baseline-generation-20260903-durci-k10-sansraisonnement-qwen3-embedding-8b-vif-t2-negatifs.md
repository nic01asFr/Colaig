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
| **Citation fantôme** — article cité inexistant dans le corpus | **1/22** |
| **Citation hors contexte** — article réel, absent des passages fournis | **10/22** |
| **Montant inventé** — somme absente des passages | **2/22** |

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

Latence de génération : médiane **11.6 s** (min 6.9, max 18.5) sur 66 appels.

## Détail des anomalies

- **mp-019** (exécution 2) — hors contexte : L2122-1
- **mp-035** (exécution 1) — hors contexte : R2111-4, R2111-8
- **mp-035** (exécution 2) — hors contexte : L2151-1, R2111-4, R2151-1
- **mp-040** (exécution 2) — hors contexte : L2192-1
- **mp-059** (exécution 1) — montants : 50 000 000
- **mp-078** (exécution 1) — fantômes : R2195-1, R2195-5
- **mp-078** (exécution 2) — fantômes : L3512-1, L3512-2
- **mp-078** (exécution 3) — hors contexte : R2112-11, R2112-14, R2112-9
- **mp-079** (exécution 1) — hors contexte : L2194-1
- **mp-079** (exécution 3) — hors contexte : L2194-1
- **mp-080** (exécution 2) — montants : 140 000, 5 404 000
- **mp-099** (exécution 2) — hors contexte : L2113-8
- **mp-120** (exécution 1) — hors contexte : L3
- **mp-120** (exécution 2) — hors contexte : R2124-4
- **mp-123** (exécution 1) — hors contexte : L2120-1, L2131-1, R2131-12, R2131-13, R2131-16, R2131-20, R2162-15
- **mp-123** (exécution 3) — hors contexte : L2131-1, R2131-12, R2131-20, R2162-15
- **mp-131** (exécution 3) — hors contexte : R2162-1
- **mp-134** (exécution 2) — hors contexte : R2171-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
