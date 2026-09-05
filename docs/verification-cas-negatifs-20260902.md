# Vérification des 22 cas négatifs du jeu doré — 02/09/2026

Constat produit en éprouvant le pipeline agent fraîchement activé. **Rien n'a été
modifié** : corriger le jeu doré déplace la référence de mesure du projet, ce qui
relève d'un arbitrage humain.

Méthode : pour chaque cas où un refus est attendu, confronter ce que le cas **affirme
absent** au **texte réel** des articles qu'il désigne lui-même comme utiles, lus dans
l'index qui ancre le jeu doré (`_chantier/scripts/index_corpus.py`).

## Résultat

| motif | cas | fondés | faux |
|---|---|---|---|
| prémisse inexacte | 7 | 6 | **1** — mp-130 |
| absence | 15 | 14 | **1** — mp-135 |
| **total** | **22** | **20** | **2** |

Deux cas sur vingt-deux. Le défaut est réel mais **isolé** : il ne remet pas en cause
la construction du jeu doré.

## mp-130 — « le CCAG ne fixe aucun taux »

L'article `CCAGTravaux19` (fichier `055-ccag-travaux-chapitre-3-delais.md`, 5 144
caractères) contient **trois** valeurs chiffrées :

- « il est appliqué une **pénalité journalière de 1/3 000** du montant hors taxes » (19.2.3)
- « ne peut excéder **10 %** du montant total hors taxes » (19.2.2)
- « exonéré des pénalités dont le montant total ne dépasse pas **1 000 euros** » (19.2.1)

La justification du cas cite ce qu'elle a lu — les samedis non déduits (19.1.1), la
répartition en groupement (19.1.2) — et conclut « il ne contient pas ce qu'on croit y
trouver ». **Elle n'a pas lu 19.2.3.**

## mp-135 — « le corpus ne permet pas de répondre sur le montant »

L'annexe 13 (`Annexe 13 — Modèles de garantie — texte 1`) dit :

> Le montant de la retenue de garantie ne peut être supérieur à **5 %** du montant
> initial du marché public augmenté, le cas échéant, du montant des modifications en
> cours d'exécution (**article R. 2191-33** du code de la commande publique).

Et **10 %** pour les marchés de défense ou de sécurité (article R. 2391-22). Le montant
est donc dans le corpus, avec sa référence.

## Ce que ces deux cas font à la mesure

Ils **retournent** le compteur : le refus y est compté comme un succès alors qu'il est
injustifié, et la bonne réponse comme un échec.

- Sur mp-135, le pipeline a répondu « encadré par l'article **R2191-33** […] ne peut
  être supérieur à **5 %** » — exact, correctement sourcé. Compté échec.
- Sur mp-130, il a répondu « **1/3 000** […] article **19.2.3** » — exact. Compté échec.
- Le cœur, lui, refuse sur les deux et gagne les points. Sur mp-130 il affirme même que
  « le corpus ne mentionne pas explicitement le taux dans l'article 19 » — une
  contre-vérité, comptée dans son 22/22.

## Les cas signalés puis écartés

Trois cas « absence » portaient une valeur chiffrée dans leurs articles utiles. Relus,
ils sont **fondés** — la valeur n'est pas la donnée demandée :

- **mp-122** (20 %, 80 000 €) : seuils de procédure ; la *liste* des services reste
  renvoyée à un avis annexé absent.
- **mp-123** (25 000 €, 40 000 €) : seuils de publication ; R2196-1 renvoie la *liste
  des données essentielles* à un arrêté annexé.
- **mp-135** : celui-ci n'a pas résisté — voir ci-dessus.

## Ce qui reste à décider

Reclasser mp-130 et mp-135 les fait sortir des cas négatifs : ils deviennent des cas
positifs, dont la bonne réponse est celle que le corpus porte. Le dénominateur du refus
passerait de 22 à 20, et les deux systèmes devraient être remesurés — le cœur y perdra
ce que le pipeline y gagnera.

C'est un arbitrage sur la référence de mesure, pas une correction de code.
