"""
Script pour commenter les fonctions qui ont été migrées dans app/commands.py.

Ce script permet d'éviter les duplications de commandes
qui causent des réponses multiples dans le bot.
"""

import re
import os

# Liste des fonctions qui ont été migrées
MIGRATED_FUNCTIONS = [
    "help",
    "doc_query_command",
    "faiss_index_command",
    "handle_attachments_command"
]

def comment_function(content, function_name):
    """Commente une fonction dans le contenu du fichier."""
    # Modèle pour trouver le décorateur register_feature
    decorator_pattern = r'(@register_feature\([^)]*\)\s*\n)(@only_allowed_user\s*\n)(async def ' + function_name + r'\([^:]*:[^}]*?(?=\n\n@|\n\n\s*def|\Z))'
    
    # Fonction de remplacement qui conserve les décorateurs mais commente la fonction
    def replacer(match):
        decorators = match.group(1) + match.group(2)
        function_code = match.group(3)
        commented_code = decorators + "# COMMENTÉ: Cette fonction a été migrée vers un module dédié\n# " + function_code.replace("\n", "\n# ")
        return commented_code
    
    # Appliquer le remplacement
    modified_content = re.sub(decorator_pattern, replacer, content, flags=re.DOTALL)
    
    if modified_content == content:
        print(f"⚠️ La fonction {function_name} n'a pas pu être commentée avec le motif principal.")
        
        # Essayer avec un modèle alternatif plus simple 
        simple_pattern = r'(async def ' + function_name + r'\([^:]*:.*?)(?=\n\n(?:async def|\@|\Z))'
        
        def simple_replacer(match):
            function_code = match.group(1)
            commented_code = "# COMMENTÉ: Cette fonction a été migrée vers un module dédié\n# " + function_code.replace("\n", "\n# ")
            return commented_code
        
        modified_content = re.sub(simple_pattern, simple_replacer, content, flags=re.DOTALL)
        
        if modified_content == content:
            print(f"❌ La fonction {function_name} n'a pas pu être commentée (non trouvée ou format trop différent).")
            return content
        else:
            print(f"✅ La fonction {function_name} a été commentée avec le motif alternatif.")
            return modified_content
    else:
        print(f"✅ La fonction {function_name} a été commentée avec le motif principal.")
        return modified_content

def main():
    """Fonction principale du script."""
    # Chemin vers le fichier app/commands.py
    file_path = "app/commands.py"
    
    if not os.path.exists(file_path):
        print(f"❌ Le fichier {file_path} n'existe pas.")
        return
    
    # Créer une sauvegarde du fichier
    backup_path = file_path + ".bak"
    try:
        with open(file_path, "r", encoding="utf-8") as src, open(backup_path, "w", encoding="utf-8") as dst:
            dst.write(src.read())
        print(f"✅ Sauvegarde créée: {backup_path}")
    except Exception as e:
        print(f"❌ Erreur lors de la création de la sauvegarde: {str(e)}")
        return
    
    # Lire le contenu du fichier
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # Commenter chaque fonction migrée
    for function_name in MIGRATED_FUNCTIONS:
        content = comment_function(content, function_name)
    
    # Écrire le contenu modifié dans le fichier
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    print(f"\n✅ Traitement terminé pour {file_path}.")
    print(f"Une sauvegarde a été créée dans {backup_path}.")
    print("Vous pouvez maintenant redémarrer le bot avec la commande: docker-compose down && docker-compose up -d")

if __name__ == "__main__":
    main() 