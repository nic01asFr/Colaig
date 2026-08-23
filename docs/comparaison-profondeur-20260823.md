# k=6 contre k=15 à la génération — 23/08/2026

Le banc des leviers donne un gagnant net **à la recherche** : `dense k=15` remonte
95 cas complets sur 103, contre 88 à `k=6`. Sept points, gratuits.

Cette page mesure ce que cela devient **à la sortie**, seule chose qui compte pour qui
rédige. Même prompt durci, même corpus, 122 cas dorés, négatifs rejoués trois fois.

## Les deux lectures, et il faut les deux

| | k=6 | k=15 |
|---|---|---|
| **cite l'article attendu — sur tous les cas** | **66/101 (65 %)** | 59/101 (58 %) |
| **cite l'article attendu — réponses complètes seulement** | 66/75 (88 %) | **59/63 (94 %)** |
| observations tronquées | 39 | **47** |
| citation fantôme | 1 | **0** |
| citation hors contexte | 0 | 0 |
| montant inventé | 0 | 0 |
| refuse à chaque fois / négatifs jugeables | 15/18 | 16/20 |
| ne refuse jamais | 2 | **1** |

## Ce que cela dit

**k=15 répond mieux quand il répond, et il répond moins souvent.**

Sur les réponses qui arrivent complètes, il est supérieur sur tous les indicateurs :
94 % contre 88 % de citation attendue, zéro fantôme, un seul cas négatif jamais refusé
contre deux. Le rappel supplémentaire de la recherche se transmet bien à la génération.

Mais il tronque davantage — **47 observations coupées contre 39** — et l'utilisateur ne
reçoit alors rien d'exploitable. Net à net, il perd : 58 % contre 65 %.

## Ce que cela ne dit pas, et qu'il ne faut pas conclure trop vite

**La comparaison est confondue par la troncature**, qui n'est pas une propriété de k=15
mais du budget de sortie. `max_tokens` vaut 4000, et `qwen3-6-35b-moe` est un modèle à
raisonnement : le raisonnement et la réponse puisent au même budget. Plus de contexte,
c'est plus de raisonnement, donc moins de place pour répondre.

Conclure « k=6 est meilleur » serait donc exact aujourd'hui et faux comme principe. Le
bon énoncé est : **à budget de sortie insuffisant, la profondeur se paie plus qu'elle ne
rapporte.**

## Décision

**k=6 reste la profondeur de production**, parce que c'est ce qui sert le mieux
l'utilisateur dans la configuration actuelle.

**Et la mesure suivante est écrite** : relever `max_tokens` jusqu'à ce que le taux de
troncature devienne négligeable, puis rejouer k=6 contre k=15. Si k=15 conserve son
avance sur les réponses complètes, il devient le bon choix. Tant que 27 à 33 % des
réponses sont coupées, aucune comparaison de profondeur n'est concluante — et ce taux
est de toute façon un défaut de service en lui-même, indépendant de k.
