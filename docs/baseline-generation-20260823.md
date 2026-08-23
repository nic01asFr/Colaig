# Palier génération — première mesure

**23/08/2026.** Complète `baseline-20260823.md`.

Montage : découpage par article (D12), `bge-m3` 1024 dim, k=6, génération par
**`qwen3-6-35b-moe` sur SSPCloud** — la cible de production (D3). Prompt système : celui
de l'espace, mot pour mot.

La génération varie ; **son évaluation est mécanique**. Tout ce qui suit se vérifie
sur le texte produit, sans juge.

## Ce qui compte le plus : le modèle invente-t-il ?

| | cas concernés |
|---|---|
| **Citation fantôme** — article cité inexistant dans le corpus | **1/45** |
| **Citation hors contexte** — article réel, absent des passages fournis | **10/45** |
| **Montant inventé** — somme absente des passages | **0/45** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 8 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **0/8** |
| Refuse *parfois* | 5/8 |
| Ne refuse **jamais** | **3/8** |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**29/37** cas positifs citent au moins un article attendu.

Latence de génération : médiane **18.1 s** (min 9.4, max 21.3) sur 61 appels.

## Détail des anomalies

- **mp-011** — hors contexte : R2122-8
- **mp-012** (exécution 2) — hors contexte : R2122-1
- **mp-013** (exécution 1) — hors contexte : R2162-1, R2162-2, R2162-4
- **mp-013** (exécution 2) — hors contexte : L2111-1, R2161-1, R2161-2
- **mp-017** — hors contexte : L2113-1
- **mp-019** (exécution 1) — hors contexte : R2143-3
- **mp-019** (exécution 2) — hors contexte : R2143-3
- **mp-019** (exécution 3) — hors contexte : R2143-3
- **mp-020** — hors contexte : L2172-3
- **mp-021** — hors contexte : R2351-12
- **mp-022** — hors contexte : R2182-1
- **mp-030** — fantômes : L1414-3
- **mp-035** (exécution 1) — hors contexte : R2191-1, R2191-10
- **mp-039** — hors contexte : L2191-1


## Lecture — trois résultats, dont un qui interdit la mise en service

### 1. L'assistant ne refuse jamais de façon fiable

**Aucun** des 8 cas négatifs n'obtient un refus aux trois exécutions. Cinq refusent
*parfois*, trois ne refusent *jamais*.

Le refus intermittent est presque pire que son absence : rien n'indique à l'utilisateur
dans quel cas il se trouve, et deux personnes posant la même question obtiennent des
comportements différents. Sur un corpus juridique, la conséquence est directe — un seuil
extrapolé produit une procédure irrégulière.

### 2. Le modèle puise dans sa mémoire, pas dans le corpus

**10 cas sur 45** citent un article **réel mais absent des passages fournis**. Le RAG est
alors contourné : la réponse ne vient pas des documents, elle vient de l'entraînement.

Trois observations rendent le phénomène concret :

- **`mp-030` cite `L1414-3`** — qui n'existe pas dans le corpus. C'est un article du Code
  général des collectivités territoriales. Le modèle a changé de code sans le dire.
- **`mp-013`**, cas négatif, cite `R2162-1, R2162-2, R2162-4` à une exécution, puis
  `L2111-1, R2161-1, R2161-2` à la suivante. **Des références différentes à chaque fois** :
  la signature d'une fabrication, pas d'une erreur.
- **`mp-022` cite `R2182-1` — l'article attendu — alors qu'il n'était pas dans les
  passages.** C'est le cas le plus insidieux : **bonne réponse, mauvaise provenance.**
  Elle est juste aujourd'hui ; le jour où l'article change, le modèle citera encore
  l'ancienne version, et le corpus mis à jour n'y changera rien.

Ce dernier point invalide toute confiance fondée sur la justesse apparente des réponses.
Seule la vérification de la **provenance** la détecte — et c'est exactement ce que cette
mesure fait.

### 3. La latence dépasse le budget de 80 %

**18,1 s de médiane**, contre les 10 s posées par H3. Et **22 réponses tronquées** à
`max_tokens=4000`, soit environ un tiers des appels — le raisonnement du modèle consomme
un budget qu'on ne lui a pas donné.

Les deux sont liés : ce modèle raisonne longuement. C'est ce qui explique à la fois la
durée et la troncature.

## Ce qu'il faut en conclure

**En l'état, cet assistant ne peut pas être mis entre les mains d'un rédacteur de marchés
publics.** Non parce qu'il répond mal — il cite l'article attendu dans 29 cas sur 37 —
mais parce qu'il **ne sait pas s'arrêter** quand l'information manque, et qu'il complète
avec sa mémoire sans le signaler.

Ce n'est pas un défaut du corpus ni de la récupération : la récupération remonte le bon
article dans 82 % des cas. **C'est un défaut de consigne et de garde-fou côté génération.**

Trois pistes, à mesurer contre cette référence, dans cet ordre :

1. **Durcir le prompt système** sur le refus, et l'éprouver — c'est le levier le moins
   coûteux, et il n'a jamais été testé.
2. **Contraindre la citation** : n'accepter dans la réponse que des numéros d'article
   présents dans les passages fournis. Vérifiable mécaniquement, donc applicable en
   post-traitement plutôt qu'en espérant que le modèle obéisse.
3. **Établir le bon `max_tokens`**, un tiers des réponses étant tronquées.

**Réserve : une seule exécution** pour les 37 cas positifs, trois pour les négatifs. Les
chiffres de citation demandent confirmation ; ceux du refus, mesurés trois fois, sont
déjà suffisamment nets.

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```
