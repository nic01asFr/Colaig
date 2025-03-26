"""
Script de migration des commandes.

Ce script facilite la transition entre l'ancien format monolithique
de commands.py et la nouvelle structure modulaire.

Note: Une fois la migration terminée, ce script peut être supprimé.
"""

import inspect
import os
import sys
from pathlib import Path
import re

def extract_command_function(source_file, function_name):
    """Extrait le code d'une fonction spécifique depuis le fichier source"""
    with open(source_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Trouver le bloc de code complet (décorateurs + fonction)
    pattern = r'(@[^\n]+\n)+async def ' + function_name + r'\([^)]*\):.*?(?=\n\n@|$)'
    match = re.search(pattern, content, re.DOTALL)
    
    if match:
        return match.group(0)
    return None

def create_module_for_command(source_file, function_name, target_module):
    """Crée un nouveau module pour une commande spécifique"""
    # Extraire le code de la fonction
    function_code = extract_command_function(source_file, function_name)
    
    if not function_code:
        print(f"Fonction {function_name} non trouvée dans {source_file}")
        return False
    
    # Convertir les décorateurs register_feature
    function_code = function_code.replace(
        '@register_feature', 
        'from ..registry import register_feature, only_allowed_user\n\n@register_feature'
    )
    
    # Créer le module cible
    target_dir = os.path.dirname(target_module)
    os.makedirs(target_dir, exist_ok=True)
    
    with open(target_module, 'w', encoding='utf-8') as f:
        f.write('"""\n')
        f.write(f'Module pour la commande {function_name}.\n')
        f.write('"""\n\n')
        f.write('from matrix_bot.client import MatrixClient\n')
        f.write('from matrix_bot.config import logger\n')
        f.write('from matrix_bot.eventparser import EventParser\n')
        f.write('from nio import RoomMessageText\n\n')
        f.write('from bot_msg import AlbertMsg\n')
        f.write('from config import Config\n\n')
        f.write(function_code)
    
    print(f"Module créé pour {function_name} dans {target_module}")
    return True

def list_commands_in_file(source_file):
    """Liste toutes les commandes dans le fichier source"""
    commands = []
    with open(source_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Trouver toutes les fonctions décorées avec register_feature
    pattern = r'@register_feature\([^)]*\)\s+@only_allowed_user\s+async def ([^\(]+)\('
    matches = re.findall(pattern, content)
    
    if matches:
        commands = matches
    
    return commands

def suggest_migration_plan(source_file):
    """Suggère un plan de migration pour commands.py"""
    commands = list_commands_in_file(source_file)
    
    print(f"Plan de migration pour {source_file}")
    print(f"Total des commandes trouvées: {len(commands)}")
    print("\nGroupes de commandes suggérés:")
    
    # Regrouper par préfixe commun
    groups = {}
    for cmd in commands:
        prefix = cmd.split('_')[0] if '_' in cmd else 'misc'
        if prefix not in groups:
            groups[prefix] = []
        groups[prefix].append(cmd)
    
    for prefix, cmds in groups.items():
        print(f"\n- Groupe '{prefix}' ({len(cmds)} commandes):")
        for cmd in cmds:
            print(f"  - {cmd}")
    
    print("\nStructure de fichiers suggérée:")
    for prefix, cmds in groups.items():
        if prefix == 'misc':
            print(f"app/commands/misc_commands.py")
        else:
            print(f"app/commands/{prefix}_commands.py")
            
            # Pour les grands groupes, suggérer un sous-package
            if len(cmds) > 3:
                print(f"  ou")
                print(f"app/commands/{prefix}_commands/")
                print(f"  __init__.py")
                for cmd in cmds:
                    print(f"  {cmd.replace(prefix + '_', '')}.py")

if __name__ == "__main__":
    # Chemin du fichier commands.py
    source_file = os.path.join(os.path.dirname(__file__), "..", "commands.py")
    
    if len(sys.argv) > 1:
        if sys.argv[1] == "plan":
            suggest_migration_plan(source_file)
        elif sys.argv[1] == "migrate" and len(sys.argv) > 2:
            function_name = sys.argv[2]
            target_module = os.path.join(
                os.path.dirname(__file__), 
                f"{function_name.split('_')[0]}_commands",
                f"{function_name.replace(function_name.split('_')[0] + '_', '')}.py"
            )
            create_module_for_command(source_file, function_name, target_module)
    else:
        print("Usage:")
        print("  python -m app.commands.migration plan")
        print("  python -m app.commands.migration migrate function_name") 