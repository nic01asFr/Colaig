---
# Identifiant unique de la skill (doit être stable, pas d'espaces)
name: instruction_opah

# Description courte (utilisée dans les logs et l'observabilité)
description: Procédure d'instruction d'un dossier OPAH ou OPAH-RU

# Priorité d'application (entier, plus haut = appliqué en premier).
# Si plusieurs skills matchent, elles sont injectées dans l'ordre.
priority: 50

# Liste de patterns regex Python qui activent la skill quand ils matchent
# le message utilisateur. Insensible à la casse via (?i).
triggers:
  - "(?i)\\bOPAH\\b"
  - "(?i)opah[- ]ru"
  - "(?i)opération programmée d'amélioration"

# Outils dont la présence est garantie quand cette skill est active.
# Override le filtrage par mots-clés/embeddings.
prefers_tools:
  - search_documents
  - datagouv__search_datasets
---

## Procédure d'instruction d'un dossier OPAH

Cette procédure s'applique aux dossiers d'instruction d'aide à la rénovation
dans le cadre d'une **Opération Programmée d'Amélioration de l'Habitat**.

### Étape 1 — Vérifier l'éligibilité du logement

- Le logement doit être situé dans le périmètre de l'opération (consulter
  l'arrêté préfectoral d'OPAH en vigueur)
- Logement principal occupé par le propriétaire (sauf cas OPAH-RU)
- Construction de plus de 15 ans
- Référence : article R.321-12 du Code de la Construction et de l'Habitation

### Étape 2 — Consulter le règlement de l'opération

Chaque OPAH dispose d'un règlement local fixant :
- Les plafonds de ressources des bénéficiaires
- Les taux d'aide selon les travaux
- Les pièces obligatoires du dossier

### Étape 3 — Vérifier les pièces du dossier

Pièces obligatoires :
- Demande signée + RIB
- Avis d'imposition N-1 et N-2
- Devis détaillés des entreprises (RGE pour les travaux énergie)
- Diagnostic technique du logement
- Justificatifs de propriété

### Étape 4 — Statut d'occupation

Vérifier que le logement reste occupé pendant la durée d'engagement
(généralement 9 ans pour les aides Anah).

### Sources

Cherche d'abord dans les documents indexés du workspace (règlement local,
arrêté préfectoral, guide instructeur). Si insuffisant, complète avec les
référentiels nationaux (Anah, data.gouv.fr).
