# Synthese Claude - Projet Colaig APF Platform

## 1. Etat actuel du projet

**Colaig** (anciennement "Albert-Tchap") est un **bot conversationnel mono-instance pour Tchap** (messagerie Matrix de l'administration francaise) utilisant **Albert** (LLM souverain, Llama 3.1 8B) avec du **RAG** sur documents stockes en **WebDAV**.

### Stack technique actuelle
- Python 3.10+ / pydantic-settings / matrix-nio / FAISS / Playwright+browser-use
- Deploiement Docker mono-instance

### Architecture actuelle
```
app/
  bot.py              # TchapBot (21k) - monolithique
  config.py           # 2 systemes dupliques : Config Pydantic + EnvConfig dataclass
  core_llm.py         # Client Albert API (23k)
  iam.py              # Auth via Grist (dependance externe)
  commands/            # Commandes (!aide, !indexer, !recherche_web...)
  services/
    webdav.py              # Client WebDAV (66k - monolithique)
    document_index.py      # Index FAISS (65k)
    behavior_manager.py    # Comportements bot (38k)
    browser_extraction.py  # Extraction web Playwright (101k)
    embedding_service.py   # Embeddings Albert
    ... (30+ fichiers)
  matrix_bot/          # Wrapper matrix-nio
```

### Problemes structurels identifies
1. **Double config** : `Config(BaseSettings)` + `EnvConfig(dataclass)` dans le meme fichier
2. **`eval()` dangereux** dans config.py:181 pour parser USER_ALLOWED_DOMAINS
3. **Dependance Grist** pour la gestion des utilisateurs (non souverain)
4. **Mono-instance** : une seule paire de credentials possible
5. **browser_extraction.py a 101k** : Playwright + Xvfb, trop lourd pour plateforme partagee
6. **Aucune interface web**, aucun concept de plateforme
7. **Fichier .whl commite** dans le repo

---

## 2. Vision cible : Colaig APF Platform

Transformer le bot mono-instance en **plateforme d'instanciation** : une interface web admin centralisee permettant aux responsables de ministeres de creer et gerer leurs bots Colaig sans intervention technique.

```
+------------------------------------------------------+
|              COLAIG APF -- Plateforme                 |
|                                                       |
|  Interface web (FastAPI + Jinja2 + HTMX)              |
|  Auth : AgentConnect OIDC (agents publics FR)         |
|                                                       |
|  Operateur DINUM       Admin Ministere                |
|  -----------------     ------------------             |
|  Creer admins          Creer/gerer ses bots           |
|  Vue globale           Configurer workspaces          |
|  Politiques            Superviser/redemarrer          |
|                                                       |
|  ======== Moteur bot (existant, refactorise) ======   |
|  N instances async concurrentes                       |
|  Instance 1 : Bot DGFIP  -> Tchap #1 + WebDAV #1     |
|  Instance 2 : Bot MTE    -> Tchap #2 + WebDAV #2     |
|  Instance N : ...                                     |
+------------------------------------------------------+
```

### Contraintes absolues
- Aucun LLM autre qu'Albert API (souverainete)
- Aucun storage autre que WebDAV
- Aucun messaging autre que Matrix/Tchap
- Aucune dependance cloud non-souveraine
- Jamais de donnees documentaires dans la DB plateforme (SQLite = registre seulement)
- Auth : AgentConnect uniquement (fallback mot de passe pour dev/test)
- Un seul container Docker
- Deployable sur infra DINUM

---

## 3. Architecture cible

### Nouveaux fichiers a creer

```
platform/
  __init__.py
  registry.py          # PlatformRegistry (aiosqlite) : admins + instances + sessions
  session.py           # Sessions web (cookie signe HMAC-SHA256, expiry 8h)
  agentconnect.py      # Client OIDC AgentConnect
  provisioner.py       # Creation/demarrage/arret des instances bot

web/
  __init__.py
  app.py               # FastAPI create_app() + montage routes
  routes_admin.py      # Routes interface admin
  templates/
    base.html
    login.html
    dashboard.html             # Vue operateur DINUM
    admin/
      dashboard.html           # Vue admin ministere
      instances.html           # Liste ses bots
      instance_new.html        # Wizard creation (4 etapes)
      instance_detail.html     # Config + statut + actions
      partials/
        test_webdav.html       # Badge test connexion HTMX
        test_tchap.html        # Badge test connexion HTMX
        instance_status.html
    operator/
      dashboard.html
      admins.html
```

### Fichiers existants a refactoriser

| Fichier | Action |
|---------|--------|
| `app/config.py` | Fusionner Config+EnvConfig, ajouter BotInstanceConfig |
| `app/bot.py` | Extraire BotRunner(config: BotInstanceConfig) pour N instances |
| `app/iam.py` | Supprimer Grist, verification domaine via platform registry |
| `app/__main__.py` | Remplacer par main.py qui demarre web + N bots |

### BotInstanceConfig (dataclass cible)

```python
@dataclass
class BotInstanceConfig:
    instance_id: str
    matrix_homeserver: str
    matrix_username: str
    matrix_password: str
    webdav_url: str
    webdav_username: str
    webdav_password: str
    webdav_root_path: str
    albert_api_url: str
    albert_api_token: str
    albert_model: str
    albert_model_embedding: str
    admin_email: str
    created_at: str
    status: str = "stopped"  # running | stopped | error
```

---

## 4. Feuille de route

### Phase 0 -- Preparation et nettoyage (P1)
- [ ] Fusionner Config + EnvConfig en une seule classe Pydantic
- [ ] Creer BotInstanceConfig
- [ ] Extraire BotRunner depuis bot.py (accepte BotInstanceConfig, pas env_config global)
- [ ] Supprimer dependance Grist dans iam.py (verification domaine simple)
- [ ] Desactiver browser_extraction par defaut (BROWSER_EXTRACTION_ENABLED=false)

### Phase 1 -- Registre plateforme (P2)
- [ ] platform/registry.py : PlatformRegistry (aiosqlite)
  - Tables : platform_admins, bot_instances, platform_sessions
  - CRUD admins + instances
- [ ] platform/session.py : sessions cookie signe HMAC-SHA256

### Phase 2 -- Auth AgentConnect (P3)
- [ ] platform/agentconnect.py : flux OIDC Authorization Code
- [ ] Routes auth : /auth/login, /auth/callback, /auth/logout
- [ ] Middleware session sur /platform/ et /admin/

### Phase 3 -- Interface web admin (P4)
- [ ] web/app.py : FastAPI create_platform_app()
- [ ] Routes operateur DINUM (/platform/) : dashboard, gestion admins
- [ ] Routes admin ministere (/admin/) : dashboard, liste instances, wizard creation
- [ ] Wizard creation 4 etapes HTMX :
  1. Identite (nom bot, description, domaines)
  2. Espace WebDAV (URL, credentials, test connexion)
  3. Bot Tchap (homeserver, credentials, test connexion)
  4. Resume + confirmation
- [ ] Routes test HTMX : POST /admin/test/webdav, POST /admin/test/tchap

### Phase 4 -- Multi-instance runner (P5)
- [ ] platform/provisioner.py : BotRunnerManager
  - start/stop/status/restart par instance
  - Chaque bot = asyncio.Task independante
- [ ] main.py : demarre web + reprend instances actives au redemarrage

### Phase 5 -- Finalisation (P6)
- [ ] Dockerfile : retirer Playwright/Xvfb si desactive (~500MB -> ~200MB)
- [ ] docker-compose.yml : volumes SQLite + sessions Matrix
- [ ] .env.example : toutes les nouvelles variables
- [ ] README.md : guide deploiement + guide admin

---

## 5. Variables d'env cibles

```bash
# Plateforme
PLATFORM_SESSION_SECRET=xxx
PLATFORM_OPERATOR_EMAILS=admin@dinum.gouv.fr
PLATFORM_DB_PATH=/app/data/platform.db

# AgentConnect
AGENTCONNECT_CLIENT_ID=xxx
AGENTCONNECT_CLIENT_SECRET=xxx
AGENTCONNECT_REDIRECT_URI=https://colaig.din.gouv.fr/auth/callback
AGENTCONNECT_ISSUER=https://auth.agentconnect.gouv.fr/api/v2

# Albert (partage, surchargeable par instance)
ALBERT_API_URL=https://albert-api.etalab.gouv.fr
ALBERT_API_TOKEN=xxx
ALBERT_MODEL=meta-llama/Llama-3.1-8B-Instruct
ALBERT_MODEL_EMBEDDING=BAAI/bge-m3

# Feature flags
BROWSER_EXTRACTION_ENABLED=false
```

Les credentials Matrix et WebDAV sont geres **par instance** dans la DB plateforme, pas dans le .env.

---

## 6. Questions ouvertes

1. **Ordre d'execution** : Faut-il livrer phase par phase ou tout d'un bloc ?
2. **Tests** : Quel niveau de couverture attendu ? Mocks Albert API + Matrix ?
3. **AgentConnect integration** : Acces aux credentials d'integration deja disponible ?
4. **Fallback auth** : Le mode PLATFORM_ADMIN_PASSWORD pour dev/test est-il suffisant pour commencer ?
5. **Donnees existantes** : Y a-t-il des instances en production a migrer ?
6. **browser_extraction** : Supprimer le code ou juste le desactiver par feature flag ?
7. **WebDAV monolithique (66k)** : Faut-il le decouper dans cette iteration ou plus tard ?
