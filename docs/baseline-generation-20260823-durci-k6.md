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
| **Citation fantôme** — article cité inexistant dans le corpus | **3/122** |
| **Citation hors contexte** — article réel, absent des passages fournis | **0/122** |
| **Montant inventé** — somme absente des passages | **0/122** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 21 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **15/18** |
| Refuse *parfois* | 1/18 |
| Ne refuse **jamais** | **2/18** |
| *Observations écartées car tronquées* | *13* |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**82/101** cas positifs citent au moins un article attendu.

Latence de génération : médiane **15.0 s** (min 5.5, max 20.9) sur 164 appels.

## Détail des anomalies

- **mp-008** — fantômes : L2161-1
- **mp-088** — fantômes : L5132-4
- **mp-091** — fantômes : L5132-4, L5213-13

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```

---

## Correction de la métrique « citation fantôme » — 23/08/2026, après coup

Ce rapport annonce **3/122 citations fantômes**. Vérification faite article par
article, **une seule l'est**.

| référence | verdict après vérification |
|---|---|
| `L5132-4` (mp-088, mp-091) | **pas un fantôme** — article du *code du travail*, cité mot pour mot dans `L2113-13`, qui était dans les passages |
| `L5213-13` (mp-091) | **pas un fantôme** — idem, cité dans `L2113-12` |
| `L2161-1` (mp-008) | **fantôme réel** — le code écrit `R2161-1`. Un L à la place d'un R |

La métrique comparait les références citées au seul corpus du Code de la commande
publique. Or **le corpus est un seul code ; les articles qu'il cite ne le sont pas.**
Un renvoi correctement relayé vers le code du travail était compté comme une invention.

Une référence **présente dans les passages fournis** ne peut pas être un fantôme, quel
que soit le code dont elle relève. La règle est désormais celle-là dans
`reference_generation.py`.

**Le chiffre à retenir pour cette exécution est donc 1/122**, et il est du même ordre
que les deux autres indicateurs — 0 citation hors contexte, 0 montant inventé.

Ce que révèle le seul fantôme réel mérite d'être noté : le modèle n'a pas inventé un
article, il a **changé une lettre**. `L2161-1` pour `R2161-1`. C'est le mode d'erreur le
plus difficile à repérer à la lecture, et celui qu'aucune vraisemblance ne trahit.
