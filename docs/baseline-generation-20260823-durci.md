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
| **Citation fantôme** — article cité inexistant dans le corpus | **0/45** |
| **Citation hors contexte** — article réel, absent des passages fournis | **1/45** |
| **Montant inventé** — somme absente des passages | **0/45** |

Une citation fantôme est le pire résultat possible : elle est indétectable pour qui
ne vérifie pas, et elle fonde une procédure sur un texte qui n'existe pas.

## Le refus sur cas négatif — 8 cas, 3 exécutions chacun

| | cas |
|---|---|
| Refuse **à chaque fois** | **6/8** |
| Refuse *parfois* | 1/8 |
| Ne refuse **jamais** | **1/8** |

Le refus intermittent est presque aussi problématique que l'absence de refus : on ne
peut pas s'y fier, et rien n'indique à l'utilisateur dans quel cas il se trouve.

## Citation de l'article attendu

**31/37** cas positifs citent au moins un article attendu.

Latence de génération : médiane **15.0 s** (min 4.8, max 20.5) sur 61 appels.

## Détail des anomalies

- **mp-017** — hors contexte : L2113-1

## Rejouer

```bash
python _chantier/scripts/reference_generation.py
```

---

## Audit de la mesure — trois corrections, dont une qui invalide la conclusion précédente

### 1. La moitié du corpus était mal reconnue

Le harnais employait **deux expressions régulières différentes** de part et d'autre de la
comparaison : celle des réponses tolérait « L. 2113-10 », celle des passages non. Or
**2 425 références du corpus sont écrites avec un point, contre 2 094 sans** — 53,7 %.

Un article cité par le modèle **et pourtant présent dans un passage** sous cette graphie
était donc compté hors contexte à tort.

| | mesure erronée | **mesure corrigée** |
|---|---|---|
| citations hors contexte | 14 | **1** |
| citations fantômes | 3 | **0** |

**La conclusion « le modèle puise dans sa mémoire » était fausse.** Elle reposait
entièrement sur cet artefact. Sur 45 cas, une seule citation ne provient pas des
passages, et aucune ne désigne un article inexistant.

Comparer deux ensembles construits par des règles différentes ne mesure pas ce qu'on
croit ; ici l'écart portait sur la moitié du corpus.

### 2. La troncature corrompait la métrique de refus

`mp-044` a été comptée « ne refuse pas » sur une réponse de **neuf caractères** :
`« Cette` — coupée au milieu de la formule de refus elle-même.

Une réponse tronquée ne peut pas être jugée sur son refus. Les observations tronquées
sont désormais **écartées et comptées à part**, plutôt que comptées comme des échecs
qui n'en sont pas.

### 3. `mp-044` était mal conçu

Le cas exigeait un refus en bloc alors que **la moitié de la question est répondable** :
le modèle citait correctement `L2141-7`, qui fonde l'exclusion d'un candidat ayant mal
exécuté un contrat antérieur. Le jeu doré pénalisait donc le bon comportement.

Requalifié en **cas mixte** : répondre sur l'article, signaler l'absence de jurisprudence.
Le jeu compte désormais 7 cas négatifs stricts et 17 cas croisés.

## Ce que la mesure corrigée montre

| | témoin ×2 | **durci, corrigé** |
|---|---|---|
| citations hors contexte | — *(mesure faussée)* | **1/45** |
| citations fantômes | — *(mesure faussée)* | **0/45** |
| refuse aux 3 exécutions | 0/8, 0/8 | **6/8** |
| cite l'article attendu | 29/37 | 31/37 |
| latence médiane | 18,1 s · 19,4 s | 15,0 s |

Les refus obtenus sont **exemplaires** : formule prescrite, puis indication de l'endroit
où chercher — l'avis annexé pour les seuils, les arrêtés pour les CCAG.

## Le cas qui résiste, et que le garde-fou ne rattrape pas

**`mp-032` ne refuse jamais, sur trois exécutions** : *« quel taux d'avance appliquer, et
sur quelle base ? »*. Le corpus contient `R2191-3`, qui dit **quand** l'avance est
obligatoire, mais ni son taux ni son assiette.

Le modèle répond, et affirme s'appuyer sur les passages :

> « Voici les règles applicables, **extraites strictement des passages fournis** »
> « Le calcul de l'avance et son taux sont **strictement encadrés par les passages
> fournis** »

C'est le piège que ce cas était conçu pour tendre — une question qui **voisine** un
article présent sans y trouver sa réponse — et il fonctionne à tous les coups.

**Et le garde-fou mécanique ne le rattrape pas.** La réponse cite `R2191-3`, qui est bien
dans les passages : la **provenance est irréprochable**, c'est l'**inférence** qui est
fautive. Le module le dit dans sa docstring — il juge la provenance, pas la véracité —
mais on en a maintenant un exemple concret et reproductible.

C'est la limite honnête de tout contrôle mécanique de provenance : il attrape ce qui
vient d'ailleurs, jamais ce qui va trop loin.
