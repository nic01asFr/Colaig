# Découpage par article — mesure contre la référence

**23/08/2026.** Première utilisation de la référence L1.5 pour arbitrer une modification
du pipeline. Hypothèse issue du diagnostic de la référence, mesurée avant d'être appliquée.

## L'hypothèse

La référence du 23/08 a diagnostiqué **deux échecs sur trois** comme des défauts de
granularité, pas de sémantique : pour `mp-004` et `mp-009`, le **bon document** remontait
mais pas le passage portant l'article. À 800 caractères, un document de 31 articles
produit une vingtaine de chunks et celui qui porte l'article se fait devancer par ses
voisins.

Hypothèse : un chunk = un article, préfixé du titre du document et de sa position dans
le code, remettrait chaque article en concurrence pour lui-même.

## Le résultat

| | `Chunker(800, 100)` — témoin | **par article** |
|---|---|---|
| chunks | 2 124 | **1 762** |
| index float32 | 8,7 Mo | **7,2 Mo** |
| récupération complète | 11/17 — 65 % | **13/17 — 76 %** |
| partielle | 3 | 2 |
| nulle | 3 | 2 |
| rang médian | 1 | 1 |

## L'hypothèse est confirmée par le mécanisme, pas seulement par l'agrégat

C'est le point qui compte. **Les deux cas diagnostiqués sont précisément ceux qui se
corrigent** :

| cas | témoin | par article |
|---|---|---|
| `mp-004` — délai minimal de réception | ❌ absent | ✅ R2161-2, rang 3 |
| `mp-009` — offre irrégulière | ❌ absent | ✅ L2152-1, rang 5 |
| `mp-002` — écrit obligatoire, cas croisé | ⚠️ partiel | ✅ complet |

Un gain agrégé aurait pu venir du hasard. Un gain **sur les cas exactement prédits** vient
du mécanisme.

Les rangs s'améliorent aussi largement : `mp-005` 2 → 1, `mp-008` 3 → 1, `mp-010` 4 → 3.

## Mais il y a une régression, et elle apprend quelque chose

**`mp-015` passe de ✅ (rang 6) à ❌.** La question — *« comment dois-je fixer les délais
de réception des offres ? »* — attend R2151-1 : *« L'acheteur fixe les délais de réception
des offres en tenant compte de la complexité du marché et du temps nécessaire aux
opérateurs économiques pour préparer leur offre. »*

C'est un article **court et général**. Au découpage par document, il bénéficiait du
contexte de ses voisins ; isolé, il entre en concurrence avec 1 761 autres articles dont
beaucoup parlent de délais de façon plus spécifique, et il sort du top 6.

**Le compromis est donc réel** : le découpage par article gagne en précision sur les
articles identifiables, et perd sur les articles courts qui vivaient de leur contexte.
Ce n'est pas un défaut de la mesure, c'est une propriété du choix.

## Ce que j'en conclus, et ce que je ne conclus pas

**Ce qui est établi :** le mécanisme diagnostiqué est bien la cause. Le découpage par
article corrige exactement les cas prédits, réduit l'index de 17 %, et améliore les rangs.

**Ce qui ne l'est pas :** que ce soit le bon défaut. Le solde est de **+3 cas gagnés, −1
perdu** sur 17 — soit un écart net de deux cas, précisément le seuil en dessous duquel la
référence elle-même déclare qu'il n'y a pas de signal. Je l'ai écrit avant de mesurer, et
je m'y tiens : **un cas pèse 6 points de pourcentage ici.**

**Recommandation :** en faire un paramètre, défaut inchangé, et trancher quand le jeu doré
aura son volume. La régression sur `mp-015` suggère par ailleurs une troisième voie à
éprouver — un article enrichi de ses voisins immédiats, plutôt qu'isolé — qui prendrait
les deux gains sans la perte.

## Rejouer

```bash
python _chantier/scripts/reference_l15.py            # témoin, 800/100
python _chantier/scripts/reference_l15.py article    # par article
```

Les deux lisent le même corpus figé, vérifié par manifeste. Toute la différence entre les
deux rapports vient de la stratégie de découpage — c'est ce qui rend la comparaison
interprétable.

---

## Diagnostic de la régression — ce n'est pas ce que je croyais

J'avais écrit que R2151-1, « court et général », se faisait devancer par des articles
plus spécifiques. **Vérifié, c'est faux.** Voici ce que remonte réellement la question
*« comment dois-je fixer les délais de réception des offres ? »* :

```
 1. 0.6979  R2161-7      6. 0.6864  R2161-6
 2. 0.6973  R2162-50     7. 0.6863  R2151-2
 3. 0.6950  R2161-10     8. 0.6859  R2161-22
 4. 0.6944  R2143-1      9. 0.6857  R2161-14
 5. 0.6901  R2361-3     10. 0.6854  R2161-12
```

R2151-1 n'est pas dans les dix premiers. Et surtout : **les dix scores tiennent dans
0,0125 point** — 1,8 % d'écart relatif du premier au dixième.

**L'embedding ne discrimine pas.** Sur ce corpus, tous les articles qui traitent de
délais occupent quasiment le même point de l'espace vectoriel. Le classement à
l'intérieur de cette bande n'est pas une hiérarchie de pertinence, c'est du bruit — et
un article y entre ou en sort selon des variations qui ne veulent rien dire.

Signe qui confirme : **R2151-2, l'article immédiatement voisin de celui attendu, est au
rang 7.** Le moteur a trouvé le bon endroit du code, pas le bon article.

### Ce que cela change pour le projet

**Ce n'est pas un défaut du découpage.** Le découpage par article a fait ce qu'on
attendait de lui — il a corrigé les deux cas diagnostiqués. La régression vient d'ailleurs :
de la **capacité de la recherche dense à séparer des textes juridiquement proches**.

Deux mécanismes existent précisément pour ça dans le tronc, et aucun n'est activé dans
cette référence :

| mécanisme | disponible | mesuré |
|---|---|---|
| **BM25 + fusion RRF** — capte les correspondances exactes (numéros, termes rares) que l'embedding manque | `bm25_store` dans `indexer.py` | non |
| **Reranking cross-encoder** — rescore les candidats, brise les égalités | `bge-reranker-v2-m3` chez Albert | non |

**Cela reclasse l'arbitrage du reranker (H2).** Il était noté « à mesurer, gain le mieux
documenté des benchmarks ». Sur ce corpus, il ne s'agit plus d'un gain marginal : quand
dix candidats tiennent dans 1,8 %, **le rerankeur ne polit pas le classement, il le
produit**. Et SSPCloud, cible de production D3, n'en sert aucun.

### Ce que je ne conclus pas

Une mesure de dispersion **sur un seul cas**. Il faudrait la calculer sur l'ensemble du
jeu doré pour savoir si l'écrasement des scores est général ou propre aux questions de
délais. C'est la prochaine mesure à faire, et elle est peu coûteuse.

---

## Reprise à 45 cas — la comparaison devient concluante

Le jeu doré est passé de 20 à 45 cas, dont **39 avec articles attendus**. Les deux
stratégies ont été rejouées à l'identique.

| | témoin `Chunker(800,100)` | **par article** |
|---|---|---|
| complets | 28/39 — 72 % | **32/39 — 82 %** |
| partiels | 4 | 3 |
| **nuls** | **7** | **4** |
| rang médian | 1 | 1 |

**+4 cas complets, −3 échecs totaux.** Sur 39 cas, un cas pèse 2,6 points : l'écart
dépasse le seuil de deux cas que la référence s'était fixé, et il va dans le même sens
sur les deux indicateurs.

### Ce qui a changé entre les deux mesures

Rien, sinon le nombre de cas. Même corpus figé, même manifeste, mêmes embeddings, même
script. **C'est le volume de l'échantillon qui a rendu la décision possible** — pas une
amélioration du pipeline.

À 17 cas, le solde était de +3/−1 : réel mais indistinguable du bruit, et j'ai refusé de
conclure. À 39, la même modification donne +4 complets et −3 nuls. La modification n'a
pas changé ; la capacité à en juger, si.

C'est exactement ce à quoi sert un jeu doré à son volume, et pourquoi 45 cas ne suffisent
toujours pas pour des écarts plus fins.
