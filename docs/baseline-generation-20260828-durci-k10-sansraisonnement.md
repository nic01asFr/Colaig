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

**87/113** cas positifs citent au moins un article attendu.

Latence de génération : médiane **2.1 s** (min 0.5, max 9.7) sur 179 appels.

## Détail des anomalies

- **mp-005** — fantômes : L2161-1, L2161-2
- **mp-007** — hors contexte : R2193-10
- **mp-008** — hors contexte : R2162-1
- **mp-020** — hors contexte : R2122-1
- **mp-023** — hors contexte : L2123-1, R2123-1
- **mp-024** — hors contexte : L2131-1
- **mp-030** — fantômes : L2162-1 · hors contexte : R2162-1
- **mp-031** — hors contexte : R2194-2, R2194-5
- **mp-037** — hors contexte : R2191-2 · montants : 90 000
- **mp-041** — hors contexte : R2161-6
- **mp-047** — fantômes : R2141-1 · hors contexte : L2141-1
- **mp-048** — fantômes : L21-1
- **mp-056** — fantômes : L311-3, L311-4, L312-1, L312-10, L312-100, L312-11, L312-12, L312-13, L312-14, L312-15, L312-16, L312-17, L312-18, L312-19, L312-2, L312-20, L312-21, L312-22, L312-23, L312-24, L312-25, L312-26, L312-27, L312-28, L312-29, L312-3, L312-30, L312-31, L312-32, L312-33, L312-34, L312-35, L312-36, L312-37, L312-38, L312-39, L312-4, L312-40, L312-41, L312-42, L312-43, L312-44, L312-45, L312-46, L312-47, L312-48, L312-49, L312-5, L312-50, L312-51, L312-52, L312-53, L312-54, L312-55, L312-56, L312-57, L312-58, L312-59, L312-6, L312-60, L312-61, L312-62, L312-63, L312-64, L312-65, L312-66, L312-67, L312-68, L312-69, L312-7, L312-70, L312-71, L312-72, L312-73, L312-74, L312-75, L312-76, L312-77, L312-78, L312-79, L312-8, L312-80, L312-81, L312-82, L312-83, L312-84, L312-85, L312-86, L312-87, L312-88, L312-89, L312-9, L312-90, L312-91, L312-92, L312-93, L312-94, L312-95, L312-96, L312-97, L312-98, L312-99
- **mp-057** — hors contexte : L2111-1
- **mp-060** — hors contexte : R2143-1
- **mp-074** — hors contexte : L2112-1
- **mp-104** — hors contexte : L2124-1, R2161-16
- **mp-106** — hors contexte : R2122-2
- **mp-109** — fantômes : R2173-1 · hors contexte : R2172-1
- **mp-112** — fantômes : L2121-1 · hors contexte : L2122-1
- **mp-113** — hors contexte : L2122-1
- **mp-115** — hors contexte : R2122-2
- **mp-119** (exécution 3) — fantômes : L2123-2
- **mp-125** — hors contexte : R2152-1
- **mp-127** — hors contexte : R2191-1
- **mp-129** — hors contexte : R2162-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
