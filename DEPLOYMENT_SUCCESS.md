# 🎉 WebExtract Service - Déploiement Réussi !

## ✅ Status : DÉPLOYÉ SUR GITHUB

**Repository GitHub** : https://github.com/nic01asFr/webtools

**Date de création** : 6 Novembre 2025
**Push réussi** : ✅ Tous les fichiers uploadés

---

## 📊 Ce Qui a Été Créé

### Service Complet WebExtract

Un **service autonome d'extraction de contenu web** adapté depuis [Colaig](https://github.com/etalab-ia/albert-tchap), maintenant disponible comme service standalone.

### Statistiques

- **38 fichiers** poussés sur GitHub
- **27 fichiers Python** (~2076 lignes de code)
- **3 commits** initiaux
- **Architecture complète** : API REST, extractors, multi-LLM, Docker, tests, docs

### Structure du Repository

```
webtools/
├── app/
│   ├── api/v1/endpoints/     # API REST (POST /api/v1/extract)
│   ├── core/
│   │   ├── browser/          # Playwright manager
│   │   ├── llm/              # Multi-LLM clients
│   │   │   ├── albert.py     # Albert (LLM gouvernemental)
│   │   │   ├── openai.py     # OpenAI (GPT-4, GPT-4o)
│   │   │   ├── anthropic.py  # Anthropic (Claude)
│   │   │   ├── factory.py    # LLM Factory
│   │   │   └── base.py       # Interface BaseLLMClient
│   │   └── config.py         # Configuration
│   ├── extractors/
│   │   ├── direct_extractor.py   # Playwright direct (rapide)
│   │   └── agent_extractor.py    # Agent IA (intelligent)
│   ├── utils/
│   │   ├── content_detector.py   # Détection auto du type
│   │   └── prompts.py            # Templates de prompts
│   ├── manager.py            # ExtractorManager (orchestrateur)
│   └── main.py               # Application FastAPI
├── tests/
│   └── test_basic.py         # Tests unitaires
├── docker/
│   ├── Dockerfile            # Image Docker optimisée
│   └── .dockerignore
├── docker-compose.yml        # Stack Docker complète
├── pyproject.toml            # Dépendances Python
├── Makefile                  # Commandes utiles
├── README.md                 # Documentation principale
├── GETTING_STARTED.md        # Guide de démarrage
├── PUSH_GUIDE.md             # Guide de push GitHub
├── LICENSE                   # MIT License
├── .env.example              # Template configuration
├── .gitignore                # Exclusions git
└── push-to-github-api.sh     # Script de push via API
```

---

## 🚀 Fonctionnalités

### 1. Support Multi-LLM

✅ **OpenAI** : GPT-4, GPT-4o, GPT-4-turbo
✅ **Anthropic** : Claude 3, Claude 3.5 Sonnet
✅ **Albert** : LLM gouvernemental français (Etalab)
✅ **Extensible** : Facile d'ajouter d'autres providers

### 2. Extraction Multi-Stratégies

✅ **Direct Playwright** : Extraction rapide pour sites statiques
✅ **Agent IA (browser-use)** : Navigation intelligente pour contenu dynamique
✅ **Fallback automatique** : Si agent échoue → direct
✅ **OCR support** : Extraction de contenu dans les images (prévu)

### 3. Détection Automatique

Le service détecte automatiquement le type de contenu :

- **Repository** : GitHub, GitLab, Bitbucket
- **Documentation** : Sites docs, API references
- **Produit** : Sites e-commerce (Amazon, etc.)
- **Article** : Blogs, actualités, Medium
- **Page générique** : Tout autre type

### 4. API REST Complète

- **Endpoint principal** : `POST /api/v1/extract`
- **Documentation Swagger** : `/docs`
- **Health checks** : `/health`, `/ready`
- **Validation Pydantic** : Requêtes et réponses typées

### 5. Déploiement Production-Ready

✅ **Docker** : Dockerfile + docker-compose.yml
✅ **Variables d'environnement** : Configuration flexible
✅ **Healthchecks** : Monitoring intégré
✅ **Logging structuré** : Logs JSON configurables
✅ **Kubernetes ready** : Readiness/Liveness probes

---

## 🔗 Liens GitHub

- **Repository** : https://github.com/nic01asFr/webtools
- **README** : https://github.com/nic01asFr/webtools/blob/main/README.md
- **Code source** : https://github.com/nic01asFr/webtools/tree/main/app
- **Documentation** : https://github.com/nic01asFr/webtools/blob/main/GETTING_STARTED.md
- **Docker** : https://github.com/nic01asFr/webtools/blob/main/docker-compose.yml

---

## 📖 Documentation

### Sur GitHub

1. **README.md** : Vue d'ensemble, installation, utilisation, exemples
2. **GETTING_STARTED.md** : Guide détaillé de démarrage pas-à-pas
3. **PUSH_GUIDE.md** : Instructions de déploiement
4. **.env.example** : Template de configuration

### API Documentation

Une fois le service lancé :
- **Swagger UI** : http://localhost:8000/docs
- **ReDoc** : http://localhost:8000/redoc
- **OpenAPI Schema** : http://localhost:8000/openapi.json

---

## 🚀 Démarrage Rapide

### Via Docker (Recommandé)

```bash
# Cloner le repository
git clone https://github.com/nic01asFr/webtools.git
cd webtools

# Configurer
cp .env.example .env
# Éditer .env avec vos clés API (OpenAI, Claude, ou Albert)

# Lancer
docker-compose up -d

# Tester
curl http://localhost:8000/health
curl http://localhost:8000/docs
```

### Via Installation Locale

```bash
# Cloner le repository
git clone https://github.com/nic01asFr/webtools.git
cd webtools

# Installer les dépendances
pip install -e .
playwright install chromium

# Configurer
cp .env.example .env
# Éditer .env

# Lancer
uvicorn app.main:app --reload

# Accéder à l'API
open http://localhost:8000/docs
```

---

## 🧪 Exemple d'Utilisation

### Extraction d'un Article

```bash
curl -X POST "http://localhost:8000/api/v1/extract" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://blog.example.com/mon-article",
    "extraction_type": "article",
    "llm_config": {
      "provider": "openai",
      "api_key": "sk-...",
      "model": "gpt-4o"
    }
  }'
```

### Extraction d'un Repository GitHub

```bash
curl -X POST "http://localhost:8000/api/v1/extract" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://github.com/python/cpython",
    "extraction_type": "repository"
  }'
```

---

## 🔧 Configuration

### Variables d'Environnement Minimales

```bash
# Pour OpenAI
DEFAULT_LLM_PROVIDER=openai
DEFAULT_LLM_MODEL=gpt-4o
OPENAI_API_KEY=sk-votre-clé

# OU pour Anthropic
DEFAULT_LLM_PROVIDER=anthropic
DEFAULT_LLM_MODEL=claude-3-5-sonnet-20241022
ANTHROPIC_API_KEY=sk-ant-votre-clé

# OU pour Albert
DEFAULT_LLM_PROVIDER=albert
ALBERT_API_KEY=votre-clé
ALBERT_API_URL=https://albert.api.etalab.gouv.fr
```

---

## 🎯 Différences avec Colaig

| Aspect | Colaig | WebExtract Service |
|--------|--------|-------------------|
| **Dépendance LLM** | Albert uniquement | Multi-provider |
| **Interface** | Bot Matrix/Tchap | API REST HTTP |
| **Configuration** | Config Colaig globale | Standalone .env simple |
| **Déploiement** | Intégré à Colaig | Service Docker indépendant |
| **Complexité** | ~26K lignes | ~2K lignes focused |
| **Réutilisabilité** | Couplé à Colaig | Service générique universel |

---

## 📜 Origine et License

### Adapté Depuis

Ce service a été **extrait et adapté** depuis [Colaig (Albert Tchap)](https://github.com/etalab-ia/albert-tchap), développé par :
- **Pôle d'Expertise de la Régulation Numérique (PEREN)**
- **Etalab** (Direction interministérielle du numérique)

### Ce Qui a Été Extrait

1. **Système d'extraction browser-use** avec agent IA
2. **Client Albert** adapté pour être standalone et multi-LLM
3. **Détection de contenu** et patterns de reconnaissance
4. **Templates de prompts** optimisés par type de contenu
5. **Gestion Playwright** avec configuration headless

### License

**MIT License** - Voir [LICENSE](https://github.com/nic01asFr/webtools/blob/main/LICENSE)

Le projet original Colaig est également sous license MIT.

---

## 🤝 Contribution

Les contributions sont bienvenues !

1. Fork le repository
2. Crée une branche pour ta feature (`git checkout -b feature/ma-feature`)
3. Commit tes changements (`git commit -m 'Add ma-feature'`)
4. Push vers la branche (`git push origin feature/ma-feature`)
5. Ouvre une Pull Request

---

## 🐛 Issues et Support

- **Issues GitHub** : https://github.com/nic01asFr/webtools/issues
- **Documentation** : Voir README.md et GETTING_STARTED.md
- **Examples** : Voir les tests dans `tests/`

---

## 🎉 Succès du Déploiement

✅ **Repository créé** : https://github.com/nic01asFr/webtools
✅ **38 fichiers poussés** avec succès
✅ **Documentation complète** disponible
✅ **Prêt pour utilisation** immédiate
✅ **Docker ready** pour déploiement facile

---

**Date de déploiement** : 6 Novembre 2025
**Version** : 1.0.0
**Status** : Production-Ready ✅
