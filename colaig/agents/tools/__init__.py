"""
agents/tools/ — Outils built-in disponibles pour l'Orchestrateur agentique.

Chaque fichier définit :
- Une ou plusieurs ToolDefinition (métadonnées + schéma JSON)
- Une factory `create_<tool>_handler(deps...) -> Callable` qui retourne un handler async

L'ensemble est assemblé par agents/context_builder.build_tool_registry().
"""
