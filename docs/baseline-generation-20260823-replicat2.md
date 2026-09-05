# Palier génération — **réplicat du témoin** (exécution 2)

> **Ce rapport devait mesurer la variante à prompt durci. Il n'en est rien.**
>
> Le script écrasait `sys.argv` pour piloter la stratégie de découpage avant de lire
> l'argument de variante : `VARIANTE` valait donc « article » et le durcissement n'a
> jamais été appliqué. **Une exécution entière a produit un réplicat du témoin sous le
> nom de la variante, sans qu'aucune erreur ne le signale.**
>
> Le défaut est corrigé. Et le rapport est conservé : il donne la **variance** du témoin,
> qu'aucune mesure ne fournissait jusqu'ici.

**23/08/2026.** Complète `baseline-20260823.md`.

Variante de consigne : **témoin** (le libellé « article » était le symptôme du défaut).

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
| Refuse **à chaque fois** | **0/8** |
| Refuse *parfois* | 6/8 |
| Ne refuse **jamais** | **2/8** |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**29/37** cas positifs citent au moins un article attendu.

Latence de génération : médiane **19.4 s** (min 8.5, max 22.0) sur 61 appels.

## Détail des anomalies

- **mp-008** — fantômes : L2162-1 · hors contexte : R2162-1
- **mp-011** — hors contexte : R2122-8
- **mp-012** (exécution 1) — hors contexte : R2122-1
- **mp-012** (exécution 3) — hors contexte : R2122-1
- **mp-013** (exécution 2) — hors contexte : L2112-1, R2162-1, R2162-2, R2162-3
- **mp-016** — hors contexte : R2322-14
- **mp-019** (exécution 2) — hors contexte : R2143-3
- **mp-019** (exécution 3) — hors contexte : R2143-3
- **mp-020** — hors contexte : L2172-3
- **mp-022** — hors contexte : R2182-1, R2362-1
- **mp-025** — hors contexte : L2141-1, L2141-10, L2141-2, L2141-3, L2141-4, L2141-5, L2341-3, R2143-10, R2143-12
- **mp-027** — hors contexte : R2393-25
- **mp-030** — fantômes : L1414-3
- **mp-034** — fantômes : L2161-1 · hors contexte : R2161-1
- **mp-035** (exécution 1) — hors contexte : R2194-1
- **mp-039** — hors contexte : R2191-32
- **mp-040** (exécution 1) — hors contexte : R2192-19, R2192-20
- **mp-040** (exécution 3) — hors contexte : R2192-19, R2192-20

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
