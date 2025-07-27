#!/usr/bin/env python3
"""
Script pour mettre à jour tous les imports problématiques
dans le projet Albert Tchap.
"""

import os
import re
import sys
from pathlib import Path

def update_file(file_path):
    """Met à jour les imports dans un fichier spécifique."""
    print(f"Traitement du fichier: {file_path}")
    
    # Lire le contenu du fichier
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Liste des modules à corriger (modules qui sont directement dans app/)
    modules_to_fix = [
        "matrix_bot", 
        "config", 
        "utils", 
        "bot_msg",
        "services",
        "commands",
        "actions",
        "bot",
        "core_llm",
        "handlers",
        "iam",
        "llm",
        "run_albert",
        "tchap_utils"
    ]
    
    # Créer un modèle regex pour matcher tous les modules
    modules_pattern = "|".join(modules_to_fix)
    
    # Remplacer les imports directs
    updated_content = re.sub(
        fr'^from ({modules_pattern})([\.\w]*) import (.*)$',
        r'from app.\1\2 import \3',
        content,
        flags=re.MULTILINE
    )
    
    # Remplacer les imports directs de modules complets
    updated_content = re.sub(
        fr'^import ({modules_pattern})$',
        r'import app.\1',
        updated_content,
        flags=re.MULTILINE
    )
    
    # Vérifier si des modifications ont été apportées
    if content != updated_content:
        # Écrire le contenu mis à jour
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(updated_content)
        print(f"  => Imports mis à jour dans {file_path}")
        return True
    
    print(f"  => Aucun import à mettre à jour dans {file_path}")
    return False

def process_directory(directory):
    """Traite tous les fichiers Python dans un répertoire récursivement."""
    modified_files = 0
    
    # Parcourir récursivement tous les fichiers Python
    for path in Path(directory).rglob('*.py'):
        if update_file(path):
            modified_files += 1
    
    return modified_files

if __name__ == "__main__":
    # Répertoire à traiter
    app_dir = "app"
    
    # Vérifier que le répertoire existe
    if not os.path.isdir(app_dir):
        print(f"Erreur: Le répertoire {app_dir} n'existe pas.")
        sys.exit(1)
    
    # Traiter les fichiers
    modified_files = process_directory(app_dir)
    
    print(f"\nTerminé! {modified_files} fichiers modifiés.") 