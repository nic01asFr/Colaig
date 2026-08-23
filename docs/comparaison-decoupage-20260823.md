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
