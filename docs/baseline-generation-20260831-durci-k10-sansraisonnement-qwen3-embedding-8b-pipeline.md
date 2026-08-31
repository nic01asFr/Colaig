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
| **Citation fantôme** — article cité inexistant dans le corpus | **11/135** |
| **Citation hors contexte** — article réel, absent des passages fournis | **21/135** |
| **Montant inventé** — somme absente des passages | **1/135** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 22 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **18/22** |
| Refuse *parfois* | 4/22 |
| Ne refuse **jamais** | **0/22** |
| *Observations écartées car tronquées* | *0* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**92/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **7.2 s** (min 4.8, max 19.2) sur 179 appels.

## Détail des anomalies

- **mp-006** — hors contexte : L2122-1
- **mp-008** — hors contexte : L2122-1 · montants : 144 000
- **mp-025** — hors contexte : R2143-1
- **mp-034** — fantômes : L2121-1
- **mp-035** (exécution 1) — fantômes : L215, R215
- **mp-035** (exécution 2) — hors contexte : L2151-1, R2152-7
- **mp-035** (exécution 3) — fantômes : R215-1, R215-2
- **mp-039** — hors contexte : L2191-1
- **mp-046** — fantômes : L2162-1 · hors contexte : L2111-1, L2111-2
- **mp-047** — hors contexte : L2112-1
- **mp-050** — fantômes : L2111-4
- **mp-054** — fantômes : L2143-1
- **mp-056** — hors contexte : L2111-1
- **mp-065** — hors contexte : L2112-1
- **mp-066** — hors contexte : L2112-3
- **mp-074** — hors contexte : L2124-1
- **mp-078** (exécution 1) — hors contexte : L2112-1
- **mp-078** (exécution 2) — hors contexte : L2112-1
- **mp-078** (exécution 3) — hors contexte : L2194-1
- **mp-086** — fantômes : L2161-1
- **mp-098** (exécution 3) — fantômes : R2113-10
- **mp-104** — fantômes : L2144-1 · hors contexte : R2144-1
- **mp-107** — hors contexte : L2124-1
- **mp-110** — hors contexte : L2122-1, R2122-1
- **mp-113** — hors contexte : L2122-1
- **mp-120** (exécution 2) — fantômes : L300-1
- **mp-123** (exécution 1) — hors contexte : L5
- **mp-123** (exécution 2) — hors contexte : L2112-1, R2112-1
- **mp-123** (exécution 3) — fantômes : L2162-1
- **mp-125** — hors contexte : R2152-7
- **mp-132** (exécution 1) — hors contexte : L2111-1
- **mp-133** (exécution 1) — hors contexte : L2112-1
- **mp-133** (exécution 2) — fantômes : R2173-1, R2174-1, R2175-1 · hors contexte : R2172-1
- **mp-133** (exécution 3) — hors contexte : L2112-1
- **mp-134** (exécution 2) — hors contexte : R2112-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
