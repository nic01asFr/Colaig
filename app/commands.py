"""
Module de commandes pour Albert Tchap - FICHIER DÉPRÉCIÉ

ATTENTION: Ce fichier est maintenu uniquement pour la compatibilité avec les imports existants.
Les nouvelles fonctionnalités doivent être ajoutées dans les modules dédiés.

Les implémentations originales ont été déplacées dans app/commands/deprecated/commands.py.
"""

# Importer depuis le module déprécié pour maintenir la compatibilité
from app.commands.deprecated.commands import *

# Note d'avertissement de dépréciation
import warnings
warnings.warn(
    "Le module app.commands est déprécié. Utilisez les modules dédiés dans app.commands.* à la place.",
    DeprecationWarning,
    stacklevel=2
) 