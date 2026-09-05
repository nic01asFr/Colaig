# Corpus — Code de la commande publique

Corpus de référence pour l'assistance à la rédaction de marchés publics.

**1021 articles en vigueur**, répartis en 107 documents suivant la structure du code.

## Provenance et licence

Code de la commande publique, version consolidée, extrait du jeu de données `AgentPublic/legi` (Hugging Face), lui-même dérivé de la base **LEGI** publiée par la DILA sur data.gouv.fr.

**Licence Ouverte 2.0 (Etalab)** — réutilisation libre, y compris commerciale, sous réserve de mentionner la source.

## Périmètre

Sont retenus les articles **applicables au 2026-08-23** : `start_date <= 2026-08-23 < end_date`, à l'exclusion des modifications n'ayant jamais pris effet (`MODIFIE_MORT_NE`). Les articles abrogés ou remplacés sont donc écartés — **un assistant qui cite un article abrogé est pire qu'un assistant muet** — mais les versions à **effet différé** déjà entrées en vigueur sont retenues, ce qu'un filtre sur le seul statut `VIGUEUR` manquait.

Ce filtre par statut écartait notamment `R2152-7`, qui définit les **critères d'attribution** : sa version applicable porte le statut `VIGUEUR_DIFF`. Le corpus ne pouvait donc pas répondre sur la question la plus centrale de la rédaction, alors que d'autres articles y renvoient explicitement.

La date de référence est épinglée au même titre que l'instantané : un corpus dont le périmètre dépend du jour de son exécution n'est pas une référence.

## Documents

| fichier | articles | position dans le code |
|---|---|---|
| `001-annexe-12-signature-electronique-texte.md` | 12 | Texte |
| `002-annexe-13-modeles-de-garantie-annexe.md` | 1 | Annexe |
| `003-annexe-13-modeles-de-garantie-texte.md` | 5 | Texte |
| `004-annexe-2-seuils-de-procedure-texte.md` | 1 | Texte |
| `005-annexe-7-profils-d-acheteurs-texte.md` | 8 | Texte |
| `006-ccag-fournitures-et-services-annexe.md` | 1 | Annexe |
| `007-ccag-fournitures-et-services-chapitre-1er-generalites.md` | 9 | Chapitre 1ER : GÉNÉRALITÉS |
| `008-ccag-fournitures-et-services-chapitre-2-prix-et-reglement.md` | 3 | Chapitre 2 : PRIX ET RÈGLEMENT |
| `009-ccag-fournitures-et-services-chapitre-3-delais.md` | 3 | Chapitre 3 : DÉLAIS |
| `010-ccag-fournitures-et-services-chapitre-4-execution.md` | 11 | Chapitre 4 : EXÉCUTION |
| `011-chapitre-5-constatation-de-l-execution-d-maintenance.md` | 7 | Chapitre 5 : CONSTATATION DE L'EXÉCUTION DES PRESTATIONS › GARANTIE › MAINTENANCE |
| `012-ccag-fournitures-et-services-chapitre-6-propriete-intellectuelle.md` | 4 | Chapitre 6 : PROPRIÉTÉ INTELLECTUELLE |
| `013-ccag-fournitures-et-services-chapitre-7-resiliation.md` | 8 | Chapitre 7 : RÉSILIATION |
| `014-ccag-fournitures-et-services-chapitre-8-differends.md` | 1 | Chapitre 8 : DIFFÉRENDS |
| `015-ccag-marches-industriels-annexe.md` | 1 | Annexe |
| `016-ccag-marches-industriels-chapitre-1er-generalites.md` | 10 | Chapitre 1ER : GÉNÉRALITÉS |
| `017-ccag-marches-industriels-chapitre-2-prix-et-reglement.md` | 3 | Chapitre 2 : PRIX ET RÈGLEMENT |
| `018-ccag-marches-industriels-chapitre-3-delais.md` | 3 | Chapitre 3 : DÉLAIS |
| `019-ccag-marches-industriels-chapitre-4-execution.md` | 15 | Chapitre 4 : EXÉCUTION |
| `020-ccag-marches-industriels-chapitre-5-constatation-de-l-execution-des-prestat.md` | 5 | Chapitre 5 : CONSTATATION DE L'EXÉCUTION DES PRESTATIONS ET GARANTIE |
| `021-ccag-marches-industriels-chapitre-6-utilisation-des-resultats.md` | 4 | Chapitre 6 : UTILISATION DES RÉSULTATS |
| `022-ccag-marches-industriels-chapitre-7-resiliation.md` | 8 | Chapitre 7 : RÉSILIATION |
| `023-ccag-marches-industriels-chapitre-8-differends.md` | 1 | Chapitre 8 : DIFFÉRENDS |
| `024-ccag-marches-industriels-chapitre-9-stipulations-speciales-aux-marches-de-r.md` | 6 | Chapitre 9 : STIPULATIONS SPÉCIALES AUX MARCHÉS DE RÉPARATION ET DE MODIFICATION |
| `025-ccag-maitrise-d-uvre-annexe.md` | 1 | Annexe |
| `026-ccag-maitrise-d-uvre-chapitre-1er-generalites.md` | 9 | Chapitre 1ER : GÉNÉRALITÉS |
| `027-ccag-maitrise-d-uvre-chapitre-2-prix-et-reglement.md` | 3 | Chapitre 2 : PRIX ET RÈGLEMENT |
| `028-ccag-maitrise-d-uvre-chapitre-3-execution-et-perimetre-des-prestations.md` | 7 | Chapitre 3 : EXÉCUTION ET PÉRIMÈTRE DES PRESTATIONS |
| `029-ccag-maitrise-d-uvre-chapitre-4-constatation-de-l-execution-des-prestat.md` | 2 | Chapitre 4 : CONSTATATION DE L'EXÉCUTION DES PRESTATIONS |
| `030-ccag-maitrise-d-uvre-chapitre-5-utilisation-des-resultats.md` | 3 | Chapitre 5 : UTILISATION DES RÉSULTATS |
| `031-chapitre-6-interruption-et-suspension-de-resiliation.md` | 10 | Chapitre 6 : INTERRUPTION ET SUSPENSION DES PRESTATIONS › RÉSILIATION |
| `032-ccag-maitrise-d-uvre-chapitre-7-differends.md` | 1 | Chapitre 7 : DIFFÉRENDS |
| `033-ccag-prestations-intellectuelles-annexe.md` | 1 | Annexe |
| `034-ccag-prestations-intellectuelles-chapitre-1er-generalites.md` | 9 | Chapitre 1ER : Généralités |
| `035-ccag-prestations-intellectuelles-chapitre-2-prix-et-reglement.md` | 3 | Chapitre 2 : Prix et règlement |
| `036-ccag-prestations-intellectuelles-chapitre-3-delais.md` | 3 | Chapitre 3 : Délais |
| `037-ccag-prestations-intellectuelles-chapitre-4-execution.md` | 12 | Chapitre 4 : Exécution |
| `038-chapitre-5-constatation-de-l-execution-d-garantie.md` | 4 | Chapitre 5 : Constatation de l'exécution des prestations › garantie |
| `039-ccag-prestations-intellectuelles-chapitre-6-utilisation-des-resultats.md` | 4 | Chapitre 6 : Utilisation des résultats |
| `040-ccag-prestations-intellectuelles-chapitre-7-resiliation.md` | 7 | Chapitre 7 : Résiliation |
| `041-ccag-prestations-intellectuelles-chapitre-8-differends.md` | 1 | Chapitre 8 : Différends |
| `042-ccag-techniques-de-l-information-annexe.md` | 1 | Annexe |
| `043-ccag-techniques-de-l-information-chapitre-1er-generalites.md` | 9 | Chapitre 1er : GÉNÉRALITÉS |
| `044-ccag-techniques-de-l-information-chapitre-2-prix-et-reglement.md` | 3 | Chapitre 2 : PRIX ET RÈGLEMENT |
| `045-ccag-techniques-de-l-information-chapitre-3-delais.md` | 3 | Chapitre 3 : DÉLAIS |
| `046-ccag-techniques-de-l-information-chapitre-4-execution.md` | 13 | Chapitre 4 : EXÉCUTION |
| `047-chapitre-5-constatation-de-l-execution-d-garantie.md` | 9 | Chapitre 5 : CONSTATATION DE L'EXÉCUTION DES PRESTATIONS › GARANTIE |
| `048-ccag-techniques-de-l-information-chapitre-6-maintenance-tierce-maintenance-applicat.md` | 5 | Chapitre 6 : MAINTENANCE, TIERCE MAINTENANCE APPLICATIVE ET INFOGÉRANCE |
| `049-ccag-techniques-de-l-information-chapitre-7-utilisation-des-resultats.md` | 4 | Chapitre 7 : UTILISATION DES RÉSULTATS |
| `050-ccag-techniques-de-l-information-chapitre-8-resiliation.md` | 8 | Chapitre 8 : RÉSILIATION |
| `051-ccag-techniques-de-l-information-chapitre-9-differends.md` | 1 | Chapitre 9 : DIFFÉRENDS |
| `052-ccag-travaux-annexe.md` | 1 | Annexe |
| `053-ccag-travaux-chapitre-1er-generalites.md` | 8 | Chapitre 1ER : Généralités |
| `054-ccag-travaux-chapitre-2-prix-et-reglement.md` | 9 | Chapitre 2 : Prix et règlement |
| `055-ccag-travaux-chapitre-3-delais.md` | 2 | Chapitre 3 : Délais |
| `056-ccag-travaux-chapitre-4-realisation-des-ouvrages.md` | 21 | Chapitre 4 : Réalisation des ouvrages |
| `057-ccag-travaux-chapitre-5-reception-et-garanties.md` | 4 | Chapitre 5 : Réception et garanties |
| `058-ccag-travaux-chapitre-6-propriete-intellectuelle.md` | 4 | Chapitre 6 : Propriété intellectuelle |
| `059-chapitre-7-resiliation-du-marche-interru-interruption-des-travaux.md` | 6 | Chapitre 7 : Résiliation du marché › Interruption des travaux |
| `060-ccag-travaux-chapitre-8-differends.md` | 1 | Chapitre 8 : : DIFFÉRENDS |
| `061-deuxieme-partie-marches-publics-livre-ie-livre-ier-dispositions-generales.md` | 2 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES |
| `062-deuxieme-partie-marches-publics-livre-ie-titre-ii-choix-de-la-procedure-de-passation.md` | 8 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre II : CHOIX DE LA PROCÉDURE DE PASSATION |
| `063-deuxieme-partie-marches-publics-livre-ie-titre-iii-engagement-de-la-procedure-de-passation.md` | 3 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre III : ENGAGEMENT DE LA PROCÉDURE DE PASSATION |
| `064-deuxieme-partie-marches-publics-livre-ie-titre-iv-phase-de-candidature.md` | 18 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IV : PHASE DE CANDIDATURE |
| `065-deuxieme-partie-marches-publics-livre-ie-chapitre-ii-modalites-de-facturation-et-de-paiemen.md` | 13 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre II : Modalités de facturation et de paiement |
| `066-deuxieme-partie-marches-publics-livre-ie-chapitre-iii-sous-traitance.md` | 14 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre III : Sous-traitance |
| `067-deuxieme-partie-marches-publics-livre-ie-chapitre-iv-modification-du-marche.md` | 3 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre IV : Modification du marché |
| `068-deuxieme-partie-marches-publics-livre-ie-chapitre-ier-execution-financiere.md` | 8 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre Ier : EXÉCUTION FINANCIÈRE |
| `069-deuxieme-partie-marches-publics-livre-ie-chapitre-v-resiliation-du-marche.md` | 6 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre V : Résiliation du marché |
| `070-deuxieme-partie-marches-publics-livre-ie-chapitre-vi-informations-relatives-a-l-achat.md` | 7 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre VI : Informations relatives à l'achat |
| `071-deuxieme-partie-marches-publics-livre-ie-chapitre-vii-reglement-alternatif-des-differends.md` | 7 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre VII : Règlement alternatif des differends |
| `072-deuxieme-partie-marches-publics-livre-ie-titre-ier-preparation-du-marche.md` | 28 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre Ier : PRÉPARATION DU MARCHÉ |
| `073-deuxieme-partie-marches-publics-livre-ie-titre-v-phase-d-offre.md` | 13 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre V : PHASE D'OFFRE |
| `074-deuxieme-partie-marches-publics-livre-ie-titre-vii-regles-applicables-a-certains-marches.md` | 14 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VII : RÈGLES APPLICABLES À CERTAINS MARCHÉS |
| `075-deuxieme-partie-marches-publics-livre-ie-titre-viii-achevement-de-la-procedure.md` | 3 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VIII : ACHÈVEMENT DE LA PROCÉDURE |
| `076-partie-legislative-titre-preliminaire.md` | 7 | Titre Préliminaire |
| `077-deuxieme-partie-marches-publics-livre-ie-livre-ier-dispositions-generales.md` | 1 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES |
| `078-deuxieme-partie-marches-publics-livre-ie-titre-ii-choix-de-la-procedure-de-passation.md` | 33 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre II : CHOIX DE LA PROCÉDURE DE PASSATION |
| `079-deuxieme-partie-marches-publics-livre-ie-titre-iii-engagement-de-la-procedure-de-passation.md` | 34 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre III : ENGAGEMENT DE LA PROCÉDURE DE PASSATION |
| `080-deuxieme-partie-marches-publics-livre-ie-chapitre-ii-conditions-de-participation.md` | 27 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IV : PHASE DE CANDIDATURE › Chapitre II : CONDITIONS DE PARTICIPATION |
| `081-deuxieme-partie-marches-publics-livre-ie-chapitre-iii-contenu-des-candidatures.md` | 16 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IV : PHASE DE CANDIDATURE › Chapitre III : CONTENU DES CANDIDATURES |
| `082-deuxieme-partie-marches-publics-livre-ie-chapitre-iv-examen-des-candidatures.md` | 9 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IV : PHASE DE CANDIDATURE › Chapitre IV : EXAMEN DES CANDIDATURES |
| `083-deuxieme-partie-marches-publics-livre-ie-chapitre-ii-modalites-de-facturation-et-de-paiemen.md` | 31 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre II : MODALITÉS DE FACTURATION ET DE PAIEMENT |
| `084-deuxieme-partie-marches-publics-livre-ie-chapitre-iii-sous-traitance.md` | 22 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre III : SOUS-TRAITANCE |
| `085-deuxieme-partie-marches-publics-livre-ie-chapitre-iv-modification-du-marche.md` | 10 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre IV : MODIFICATION DU MARCHÉ |
| `086-deuxieme-partie-marches-publics-livre-ie-chapitre-ier-execution-financiere.md` | 2 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre Ier : EXÉCUTION FINANCIÈRE |
| `087-deuxieme-partie-marches-publics-livre-ie-section-1-avances.md` | 14 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre Ier : EXÉCUTION FINANCIÈRE › Section 1 : Avances |
| `088-deuxieme-partie-marches-publics-livre-ie-section-2-acomptes.md` | 3 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre Ier : EXÉCUTION FINANCIÈRE › Section 2 : Acomptes |
| `089-deuxieme-partie-marches-publics-livre-ie-section-3-regime-des-paiements.md` | 9 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre Ier : EXÉCUTION FINANCIÈRE › Section 3 : Régime des paiements |
| `090-deuxieme-partie-marches-publics-livre-ie-section-4-garanties.md` | 13 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre Ier : EXÉCUTION FINANCIÈRE › Section 4 : Garanties |
| `091-deuxieme-partie-marches-publics-livre-ie-section-5-cession-ou-nantissement-des-creances.md` | 19 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre Ier : EXÉCUTION FINANCIÈRE › Section 5 : Cession ou nantissement des créances |
| `092-deuxieme-partie-marches-publics-livre-ie-chapitre-vi-informations-relatives-a-l-achat.md` | 11 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre VI : INFORMATIONS RELATIVES À L'ACHAT |
| `093-deuxieme-partie-marches-publics-livre-ie-chapitre-vii-reglement-alternatif-des-differends.md` | 25 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre IX : EXÉCUTION DU MARCHÉ › Chapitre VII : RÈGLEMENT ALTERNATIF DES DIFFÉRENDS |
| `094-deuxieme-partie-marches-publics-livre-ie-chapitre-ii-contenu-du-marche.md` | 18 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre Ier : PRÉPARATION DU MARCHÉ › Chapitre II : CONTENU DU MARCHÉ |
| `095-deuxieme-partie-marches-publics-livre-ie-chapitre-iii-organisation-de-l-achat.md` | 8 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre Ier : PRÉPARATION DU MARCHÉ › Chapitre III : ORGANISATION DE L'ACHAT |
| `096-deuxieme-partie-marches-publics-livre-ie-chapitre-ier-definition-du-besoin.md` | 17 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre Ier : PRÉPARATION DU MARCHÉ › Chapitre Ier : DÉFINITION DU BESOIN |
| `097-deuxieme-partie-marches-publics-livre-ie-titre-v-phase-d-offre.md` | 35 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre V : PHASE D'OFFRE |
| `098-deuxieme-partie-marches-publics-livre-ie-section-1-accords-cadres.md` | 14 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VI : RÈGLES APPLICABLES AUX PROCÉDURES DE PASSATION ET AUX TECHNIQUES D'ACHAT › Chapitre II : RÈGLES APPLICABLES AUX TECHNIQUES D'ACHAT › Section 1 : Accords-cadres |
| `099-deuxieme-partie-marches-publics-livre-ie-section-2-concours.md` | 12 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VI : RÈGLES APPLICABLES AUX PROCÉDURES DE PASSATION ET AUX TECHNIQUES D'ACHAT › Chapitre II : RÈGLES APPLICABLES AUX TECHNIQUES D'ACHAT › Section 2 : Concours |
| `100-deuxieme-partie-marches-publics-livre-ie-section-3-systeme-de-qualification-des-entites-adj.md` | 10 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VI : RÈGLES APPLICABLES AUX PROCÉDURES DE PASSATION ET AUX TECHNIQUES D'ACHAT › Chapitre II : RÈGLES APPLICABLES AUX TECHNIQUES D'ACHAT › Section 3 : Système de qualification des entités adjudicatrices |
| `101-deuxieme-partie-marches-publics-livre-ie-section-4-systeme-d-acquisition-dynamique.md` | 15 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VI : RÈGLES APPLICABLES AUX PROCÉDURES DE PASSATION ET AUX TECHNIQUES D'ACHAT › Chapitre II : RÈGLES APPLICABLES AUX TECHNIQUES D'ACHAT › Section 4 : Système d'acquisition dynamique |
| `102-deuxieme-partie-marches-publics-livre-ie-section-5-catalogues-electroniques.md` | 5 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VI : RÈGLES APPLICABLES AUX PROCÉDURES DE PASSATION ET AUX TECHNIQUES D'ACHAT › Chapitre II : RÈGLES APPLICABLES AUX TECHNIQUES D'ACHAT › Section 5 : Catalogues électroniques |
| `103-deuxieme-partie-marches-publics-livre-ie-section-6-encheres-electroniques.md` | 10 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VI : RÈGLES APPLICABLES AUX PROCÉDURES DE PASSATION ET AUX TECHNIQUES D'ACHAT › Chapitre II : RÈGLES APPLICABLES AUX TECHNIQUES D'ACHAT › Section 6 : Enchères électroniques |
| `104-deuxieme-partie-marches-publics-livre-ie-chapitre-ier-regles-applicables-aux-procedures-for.md` | 31 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VI : RÈGLES APPLICABLES AUX PROCÉDURES DE PASSATION ET AUX TECHNIQUES D'ACHAT › Chapitre Ier : RÈGLES APPLICABLES AUX PROCÉDURES FORMALISÉES |
| `105-deuxieme-partie-marches-publics-livre-ie-chapitre-ii-regles-applicables-a-certains-marches.md` | 34 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VII : RÈGLES APPLICABLES A CERTAINS MARCHÉS › Chapitre II : RÈGLES APPLICABLES À CERTAINS MARCHÉS EN FONCTION DE LEUR OBJET |
| `106-deuxieme-partie-marches-publics-livre-ie-chapitre-ier-regles-applicables-a-certains-marches.md` | 23 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VII : RÈGLES APPLICABLES A CERTAINS MARCHÉS › Chapitre Ier : RÈGLES APPLICABLES À CERTAINS MARCHÉS GLOBAUX |
| `107-deuxieme-partie-marches-publics-livre-ie-titre-viii-achevement-de-la-procedure.md` | 34 | DEUXIÈME PARTIE : MARCHÉS PUBLICS › Livre Ier : DISPOSITIONS GÉNÉRALES › Titre VIII : ACHÈVEMENT DE LA PROCÉDURE |
