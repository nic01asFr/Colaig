# Configuration de workspace Colaig

Chaque workspace Colaig (typiquement, chaque salon Tchap ou groupe de salons)
peut disposer de sa propre **carte d'identité agentique** : un fichier YAML
qui personnalise le ton, le périmètre, les outils accessibles et les comportements
du bot pour ce workspace, **sans toucher au code Colaig** ni au déploiement global.

## Emplacement

```
{workspace}/.albert/config/workspace.yaml
```

Le `{workspace}` est le chemin WebDAV mappé à la room (par défaut
`rooms/{room_id}`, configurable via `Config.workspace_path_template`).

## Statut du fichier

- **Optionnel** : si absent, le workspace utilise les défauts d'instance.
- **Hot-reload** : modifications prises en compte sans restart du bot
  (cache mtime opportuniste, vérification toutes les 30 s max).
- **Isolé** : strictement par workspace, jamais partagé entre rooms.
- **Additif** : ce qui n'est pas défini hérite des défauts.

## Format

Voir [exemple complet](exemples/workspace.yaml).

### Sections supportées

| Section | Rôle |
|---|---|
| `identity.persona_override` | Remplace l'identité Colaig par défaut dans le system prompt |
| `tools.enabled` | Whitelist d'outils (glob supporté), `[]` = tous |
| `tools.disabled` | Blacklist d'outils |
| `tools.always_included` | Outils du noyau jamais filtrés |
| `tools.keywords_extra` | Mots-clés additionnels par outil |
| `limits.max_turns` | Override de `loop.max_turns` global |

### Squelette minimal

```yaml
identity:
  persona_override: |
    Tu es l'assistant IA de la préfecture de XXX.
    Tu réponds aux agents publics sur les procédures de YYY.
```

C'est suffisant pour customiser le comportement d'un workspace.
Les outils, les behaviors, les MCP servers continuent d'utiliser
les défauts d'instance.

## Articulation avec les autres fichiers `.albert/`

```
{workspace}/.albert/
├── config/
│   ├── mcp_servers.json       ← Serveurs MCP du workspace (override des défauts)
│   └── workspace.yaml          ← Identité, scoping, limites du workspace (CE FICHIER)
│
├── behavior/                   ← Behaviors RAG sémantiques (intent matching)
│   ├── actions/*.json
│   ├── tools/*.json
│   ├── prompts/*.json
│   └── rules/*.json
│
├── skills/                     ← Skills déclaratives Markdown (à venir)
│   └── *.md
│
├── index/
│   ├── faiss.index             ← Index documentaire (RAG sur les docs utilisateur)
│   ├── document_map.json
│   └── behavior.faiss          ← Index sémantique des behaviors
│
└── contexts/
    └── {room_id}_{user_id}.json
```

| Fichier | Que définit-il ? |
|---|---|
| `workspace.yaml` | **Comment** le bot se présente et **quels outils** il a |
| `mcp_servers.json` | **Quels serveurs MCP** sont déclarés pour ce workspace |
| `behavior/` | **Quels intents RAG** sont reconnus (recherche sémantique) |
| `skills/` (à venir) | **Quelles procédures** sont activées par regex trigger |
| `index/` | **Index FAISS** des documents et des behaviors |
| `contexts/` | **État de session** persistant par utilisateur |

## Quand utiliser quoi ?

| Besoin | Fichier à modifier |
|---|---|
| Changer le ton du bot pour ce workspace | `workspace.yaml` (`identity.persona_override`) |
| Désactiver un outil MCP global | `workspace.yaml` (`tools.disabled`) |
| Ajouter un serveur MCP propre au workspace | `mcp_servers.json` |
| Définir une procédure métier ("comment instruire X") | `skills/` (à venir) |
| Indexer un nouveau type de document | `behavior/actions/` + `!index rebuild` |

## Sécurité

Le contenu de `.albert/` doit être **lecture seule pour les utilisateurs
normaux du workspace**, écriture réservée aux **admins**. Sinon n'importe
quel membre du salon peut modifier le persona du bot, désactiver des outils
ou injecter des skills malveillantes.

À terme : permissions ACL au niveau WebDAV. Pour l'instant : convention
documentaire et déploiement par script admin.
