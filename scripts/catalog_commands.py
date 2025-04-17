#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Script pour cataloguer les commandes d'Albert.

Ce script analyse le code source pour identifier toutes les commandes,
leurs propriétés et générer un catalogue structuré.
"""

import os
import sys
import json
import re
import ast
import argparse
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional, Set
from pathlib import Path


@dataclass
class CommandInfo:
    """Information sur une commande"""
    name: str
    group: str
    function_name: str
    aliases: List[str] = field(default_factory=list)
    help_text: Optional[str] = None
    file_path: Optional[str] = None
    line_number: int = 0
    is_thread_command: bool = False
    for_geek: bool = False
    thread_name: Optional[str] = None
    decorator_type: str = "albert_command"
    function_docstring: Optional[str] = None
    timeout: Optional[float] = None
    preserve_context: bool = True
    
    def to_dict(self) -> Dict[str, Any]:
        """Convertit l'information en dictionnaire."""
        return {k: v for k, v in asdict(self).items() if v is not None}
    
    def to_behavior_action(self) -> Dict[str, Any]:
        """Génère une configuration de behavior action pour cette commande."""
        # Nettoyer le help_text pour la description
        description = self.help_text
        if description and description.startswith(f"!{self.name}"):
            description = description.split("-", 1)[1].strip() if "-" in description else description
        
        # Créer la configuration
        return {
            "type": "action",
            "id": f"cmd_{self.name}",
            "description": description or f"Exécute la commande {self.name}",
            "priority": 0.7,  # Priorité par défaut
            "configuration": {
                "command": self.name,
                "examples": [
                    f"Je veux utiliser la commande {self.name}",
                    f"Exécute {self.name}",
                    f"Comment puis-je utiliser {self.name}?",
                ],
                "parameters": [],
                "response_template": "{{result}}"
            }
        }


class CommandCatalogBuilder:
    """Générateur de catalogue de commandes Albert"""
    
    def __init__(self, source_dir: str):
        self.source_dir = Path(source_dir)
        self.commands: List[CommandInfo] = []
        self.processed_files: Set[str] = set()
        
    def scan_directory(self, dir_path: Optional[Path] = None) -> None:
        """Parcourt récursivement un répertoire à la recherche de fichiers Python."""
        if dir_path is None:
            dir_path = self.source_dir
            
        for item in dir_path.iterdir():
            if item.is_dir() and not item.name.startswith('.') and not item.name == '__pycache__':
                self.scan_directory(item)
            elif item.is_file() and item.suffix == '.py' and str(item) not in self.processed_files:
                self.process_file(item)
                self.processed_files.add(str(item))
    
    def process_file(self, file_path: Path) -> None:
        """Analyse un fichier Python pour trouver des définitions de commandes."""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
                
            # Analyser le fichier avec AST
            tree = ast.parse(file_content)
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef):
                    # Vérifier si la fonction a un décorateur albert_command
                    for decorator in node.decorator_list:
                        # Vérifier les décorateurs albert_command et albert_thread_command
                        if (isinstance(decorator, ast.Call) and 
                            isinstance(decorator.func, ast.Name) and
                            decorator.func.id in ['albert_command', 'albert_thread_command', 'albert_thread_response']):
                            
                            # Extraire les informations de cette commande
                            cmd_info = self._extract_command_info(decorator, node, file_path)
                            if cmd_info:
                                self.commands.append(cmd_info)
                                
                        # Les anciens décorateurs register_feature peuvent aussi définir des commandes
                        elif (isinstance(decorator, ast.Call) and 
                              isinstance(decorator.func, ast.Name) and
                              decorator.func.id == 'register_feature'):
                            
                            # Traiter l'ancien format de décorateur
                            cmd_info = self._extract_old_command_info(decorator, node, file_path)
                            if cmd_info:
                                self.commands.append(cmd_info)
                                
        except Exception as e:
            print(f"Erreur lors de l'analyse de {file_path}: {str(e)}")
    
    def _extract_command_info(self, decorator: ast.Call, func_node: ast.FunctionDef, 
                             file_path: Path) -> Optional[CommandInfo]:
        """Extrait les informations d'une commande à partir d'un décorateur modern."""
        try:
            # Initialiser les valeurs par défaut
            command_info = CommandInfo(
                name="",
                group="",
                function_name=func_node.name,
                file_path=str(file_path),
                line_number=func_node.lineno
            )
            
            # Extraire la docstring si présente
            if ast.get_docstring(func_node):
                command_info.function_docstring = ast.get_docstring(func_node)
            
            # Détecter le type de décorateur
            decorator_name = decorator.func.id
            command_info.decorator_type = decorator_name
            command_info.is_thread_command = decorator_name == 'albert_thread_command'
            
            # Extraire les arguments du décorateur
            for kw in decorator.keywords:
                if kw.arg == 'command' and isinstance(kw.value, ast.Str):
                    command_info.name = kw.value.s
                elif kw.arg == 'group' and isinstance(kw.value, ast.Str):
                    command_info.group = kw.value.s
                elif kw.arg == 'aliases':
                    if isinstance(kw.value, ast.List):
                        command_info.aliases = [elt.s for elt in kw.value.elts if isinstance(elt, ast.Str)]
                elif kw.arg == 'help_text' and isinstance(kw.value, ast.Str):
                    command_info.help_text = kw.value.s
                elif kw.arg == 'for_geek' and isinstance(kw.value, ast.Constant):
                    command_info.for_geek = bool(kw.value.value)
                elif kw.arg == 'thread_name' and isinstance(kw.value, ast.Str):
                    command_info.thread_name = kw.value.s
                elif kw.arg == 'timeout' and isinstance(kw.value, (ast.Num, ast.Constant)):
                    command_info.timeout = float(kw.value.n if hasattr(kw.value, 'n') else kw.value.value)
                elif kw.arg == 'preserve_context' and isinstance(kw.value, ast.Constant):
                    command_info.preserve_context = bool(kw.value.value)
            
            # Pour les Thread Response, le nom de commande est vide
            if decorator_name == 'albert_thread_response':
                command_info.name = f"[Thread Response: {command_info.thread_name}]"
                
            return command_info if command_info.name else None
            
        except Exception as e:
            print(f"Erreur extraction info commande: {str(e)}")
            return None
    
    def _extract_old_command_info(self, decorator: ast.Call, func_node: ast.FunctionDef, 
                                file_path: Path) -> Optional[CommandInfo]:
        """Extrait les informations d'une commande à partir d'un ancien décorateur."""
        try:
            # Initialiser les valeurs par défaut
            command_info = CommandInfo(
                name="",
                group="",
                function_name=func_node.name,
                file_path=str(file_path),
                line_number=func_node.lineno,
                decorator_type="register_feature"
            )
            
            # Extraire la docstring si présente
            if ast.get_docstring(func_node):
                command_info.function_docstring = ast.get_docstring(func_node)
            
            # Extraire les arguments du décorateur
            for i, kw in enumerate(decorator.keywords):
                if kw.arg == 'command' and isinstance(kw.value, ast.Str):
                    command_info.name = kw.value.s
                elif kw.arg == 'group' and isinstance(kw.value, ast.Str):
                    command_info.group = kw.value.s
                elif kw.arg == 'aliases':
                    if isinstance(kw.value, ast.List):
                        command_info.aliases = [elt.s for elt in kw.value.elts if isinstance(elt, ast.Str)]
                elif kw.arg == 'help' and isinstance(kw.value, ast.Str):
                    command_info.help_text = kw.value.s
                elif kw.arg == 'for_geek' and isinstance(kw.value, ast.Constant):
                    command_info.for_geek = bool(kw.value.value)
            
            # Vérifier si c'est un thread_command via les autres décorateurs
            for decorator in func_node.decorator_list:
                if (isinstance(decorator, ast.Call) and 
                    isinstance(decorator.func, ast.Name) and
                    decorator.func.id == 'thread_command'):
                    
                    command_info.is_thread_command = True
                    
                    # Extraire le nom du thread
                    if decorator.args and isinstance(decorator.args[0], ast.Str):
                        command_info.thread_name = decorator.args[0].s
                        
            return command_info if command_info.name else None
            
        except Exception as e:
            print(f"Erreur extraction info ancienne commande: {str(e)}")
            return None
    
    def generate_catalog(self) -> Dict[str, Any]:
        """Génère un catalogue de toutes les commandes."""
        return {
            "total_commands": len(self.commands),
            "timestamp": "2024-07-01T12:00:00",  # À remplacer par timestamp actuel
            "commands": [cmd.to_dict() for cmd in self.commands]
        }
    
    def generate_behavior_configs(self, output_dir: str) -> None:
        """Génère des configurations behavior pour toutes les commandes."""
        # Créer le répertoire de sortie s'il n'existe pas
        os.makedirs(output_dir, exist_ok=True)
        
        # Générer un fichier de configuration pour chaque commande
        for cmd in self.commands:
            # Ignorer les thread_response et les commandes sans nom
            if cmd.decorator_type == 'albert_thread_response' or not cmd.name:
                continue
                
            # Créer la configuration behavior
            behavior_config = cmd.to_behavior_action()
            
            # Écrire dans un fichier
            file_path = os.path.join(output_dir, f"{cmd.name.replace('-', '_')}.json")
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(behavior_config, f, indent=2, ensure_ascii=False)
                
            print(f"Fichier de configuration généré : {file_path}")


def main():
    """Fonction principale."""
    parser = argparse.ArgumentParser(description="Catalogue de commandes Albert")
    parser.add_argument('--source', default='app', help='Répertoire source à analyser')
    parser.add_argument('--output', default='command_catalog.json', help='Fichier de sortie pour le catalogue JSON')
    parser.add_argument('--behaviors', default='behaviors/actions', help='Répertoire de sortie pour les fichiers de behavior')
    args = parser.parse_args()
    
    # Créer le générateur de catalogue
    catalog_builder = CommandCatalogBuilder(args.source)
    
    # Scanner les fichiers
    print(f"Analyse du répertoire {args.source}...")
    catalog_builder.scan_directory()
    
    # Afficher quelques statistiques
    print(f"\nCatalogue généré avec {len(catalog_builder.commands)} commandes:")
    for i, cmd in enumerate(sorted(catalog_builder.commands, key=lambda x: x.name), 1):
        print(f"{i:2d}. {'[Thread]' if cmd.is_thread_command else '        '} "
              f"{cmd.name:20s} - {cmd.group:10s} - {cmd.help_text or 'Sans description'}")
    
    # Générer et sauvegarder le catalogue
    catalog = catalog_builder.generate_catalog()
    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
    print(f"\nCatalogue sauvegardé dans {args.output}")
    
    # Générer les fichiers de configuration behavior
    if args.behaviors:
        print(f"\nGénération des fichiers de behavior dans {args.behaviors}...")
        catalog_builder.generate_behavior_configs(args.behaviors)
    
if __name__ == "__main__":
    main() 