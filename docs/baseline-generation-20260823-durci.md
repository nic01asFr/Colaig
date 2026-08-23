# Palier génération — première mesure

**23/08/2026.** Complète `baseline-20260823.md`.

Variante de consigne : **durci**.

Montage : découpage par article (D12), `bge-m3` 1024 dim, k=6, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **3/45** |
| **Citation hors contexte** — article réel, absent des passages fournis | **14/45** |
| **Montant inventé** — somme absente des passages | **0/45** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 8 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **3/8** |
| Refuse *parfois* | 4/8 |
| Ne refuse **jamais** | **1/8** |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**33/37** cas positifs citent au moins un article attendu.

Latence de génération : médiane **18.8 s** (min 10.0, max 21.8) sur 61 appels.

## Détail des anomalies

- **mp-008** — fantômes : L2161-1 · hors contexte : R2161-1
- **mp-011** — hors contexte : R2122-8
- **mp-012** (exécution 3) — hors contexte : L2112-1, R2122-1
- **mp-013** (exécution 1) — hors contexte : L2111-1
- **mp-013** (exécution 2) — hors contexte : L2113-1
- **mp-013** (exécution 3) — fantômes : R2152-100 · hors contexte : L2151-1, R2152-1, R2152-2, R2152-3
- **mp-019** (exécution 1) — hors contexte : R2143-3
- **mp-019** (exécution 2) — hors contexte : L2141-3, R2143-3
- **mp-019** (exécution 3) — hors contexte : R2143-3
- **mp-020** — hors contexte : L2172-3
- **mp-021** — hors contexte : R2191-10, R2351-12
- **mp-022** — hors contexte : R2182-1
- **mp-025** — hors contexte : L2141-2, L2141-3, R2143-1, R2143-13, R2143-4
- **mp-030** — fantômes : L1414-3
- **mp-031** — hors contexte : R2194-1, R2194-5
- **mp-032** (exécution 1) — hors contexte : R2151-13
- **mp-032** (exécution 3) — hors contexte : R2151-13
- **mp-035** (exécution 1) — hors contexte : R2194-1
- **mp-040** (exécution 3) — hors contexte : R2192-19, R2192-20
- **mp-042** — hors contexte : R2191-7

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
