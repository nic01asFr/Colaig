# colaig/ — contrats transverses

Ce fichier décrit les modules qui ne relèvent d'aucun sous-paquet et dont **tout le
reste du projet dépend**. Les autres modules ont leur propre `CLAUDE.md`.

---

## paths.py — source unique des chemins `.colaig/`

**Contrat.** Aucun fichier du projet, hors `colaig/paths.py`, ne construit un chemin
`.colaig/…` ou `.albert/…`. C'est le principe 3 de `CLAUDE.md` racine, et il est vérifié
mécaniquement par `tests/test_paths_source_unique.py` : le test analyse l'AST de tout
`colaig/` et échoue sur tout littéral de chaîne fautif.

```python
from colaig import paths

# ── Racine et configuration ─────────────────────────────────────────────
paths.colaig_dir("/espace-rh/")            # → "/espace-rh/.colaig/"
paths.config_file("/espace-rh")            # → "/espace-rh/.colaig/config.yaml"
paths.ignore_file("/espace-rh/")           # → "/espace-rh/.colaig-ignore"

# ── Conversations et tâches ─────────────────────────────────────────────
paths.conversations_dir(ws)                # → "{ws}/.colaig/conversations/"
paths.conversation_file(ws, conv_id)       # → ".../conversations/{id}.json"
paths.trame_file(ws, conv_id)              # → ".../conversations/{id}_trame.json"
paths.tasks_dir(ws)                        # → "{ws}/.colaig/tasks/"
paths.task_file(ws, task_id)               # → ".../tasks/{id}.json"
paths.task_dir(ws, task_id)                # → ".../tasks/{id}/"
paths.task_current_file(ws, tid, "plan.json")     # → ".../tasks/{id}/current/plan.json"
paths.task_subtask_file(ws, tid, sid)             # → ".../current/subtasks/{sid}.json"
paths.task_runs_dir(ws, tid)                      # → ".../tasks/{id}/runs/"
paths.task_run_file(ws, tid, run, "summary.json") # → ".../runs/{run}/summary.json"

# ── Index ───────────────────────────────────────────────────────────────
paths.indexes_dir(ws)                      # → "{ws}/.colaig/indexes/"
paths.index_file(ws, "index.faiss")        # → ".../indexes/index.faiss"
paths.workspace_knowledge_file(ws)         # → "{ws}/.colaig/workspace_knowledge.json"

# ── Utilisateurs, profil, compétences ───────────────────────────────────
paths.users_dir(ws) / paths.user_dir(ws, uid) / paths.user_file(ws, uid, nom)
paths.profile_dir(ws) / paths.identity_file(ws) / paths.behaviors_dir(ws)
paths.prompts_dir(ws) / paths.prompt_file(ws, role) / paths.skills_dir(ws)
paths.tokens_dir(ws) / paths.mcp_configs_dir(ws)

# ── Fédération (racine du storage par défaut) ───────────────────────────
paths.federation_dir() / paths.federation_peers_file()
faiss, meta = paths.federation_index_files()

# ── Sous-dossier libre, poste local, inspection ─────────────────────────
paths.instance_subdir(ws, _SKILLS_DIR)     # module définissant son propre sous-dossier
paths.local_file("matrix_token.json")      # ~/.colaig/… — machine hôte, pas un espace
paths.is_instance_path(chemin)             # segment == .colaig ou .albert
paths.is_reserved_path(chemin)             # segment commençant par — sécurité
paths.legacy_albert_path(ws, "config.yaml")
```

### Deux règles qui évitent des bugs silencieux

**1. Slash final.** Les fonctions de **dossier** en retournent un, les fonctions de
**fichier** non. Ne jamais écrire `f"{paths.indexes_dir(ws)}/{nom}"` : cela produit
`//`, qui désigne un objet **distinct** sur certains backends de stockage — l'index
s'écrit à un endroit et se relit à un autre, sans la moindre erreur. Utiliser
`paths.index_file(ws, nom)`. Un test de contrat refuse cette concaténation.

**2. Normalisation de la base.** `paths` applique `rstrip('/')` une fois pour toutes.
Un espace peut donc être déclaré `"/equipe-rh"` ou `"/equipe-rh/"` indifféremment. Avant
L0.2, la moitié des appelants oubliaient ce `rstrip` et produisaient des `//`.

### `is_instance_path` vs `is_reserved_path`

Les deux ne sont **pas** interchangeables. `is_instance_path` exige l'égalité stricte du
segment (`.colaig`). `is_reserved_path` accepte tout segment *commençant par*, donc
aussi `.colaig-ignore` : c'est le prédicat des contrôles de sécurité. Y substituer la
version stricte autoriserait la lecture de `.colaig-ignore` comme document ordinaire.

### `.albert`

`legacy_albert_path()` existe, mais **rien ne l'appelle encore**. Le code ne contient
aucun littéral `.albert` : un espace resté sous l'ancien nom de dossier n'est pas
lisible aujourd'hui. C'est l'objet du lot L1.7 (migration `.albert` → `.colaig`), dont
cette fonction est la brique de base.

---

## protocols.py — frontière d'injection

**Ne jamais modifier sans arbitrage humain explicite** (`CLAUDE.md` racine §5). Toute
I/O passe par un Protocol ; l'implémentation concrète n'est injectée que dans `main.py`.

## config.py — chargement de la configuration

`env > yaml > défauts`. Valide les champs requis backend par backend et rejette un
backend inconnu. Sélecteurs : `STORAGE_BACKEND` (`local`, `webdav`, `bigfolder`, `s3`,
`msgraph`, `box`, `gdrive`), `MESSAGING_BACKEND` (`matrix`, `webchat`, `telegram`,
`slack`, `none`), `LLM_BACKEND` (`albert`, `openai`, `azure`, `ollama`).
