# WebExtract Service

Service autonome d'extraction de contenu web avec support multi-LLM et stratégies d'extraction intelligentes.

## 🚀 Fonctionnalités

- **Extraction Multi-Stratégies** : Direct Playwright, Agent IA, HTTP fallback, OCR
- **Support Multi-LLM** : Albert, OpenAI, Anthropic (Claude), ou tout LLM compatible OpenAI
- **Détection Automatique** : Identifie le type de contenu (article, produit, documentation, etc.)
- **API REST** : Interface HTTP simple et universelle
- **Containerisé** : Déploiement Docker prêt pour la production
- **Prompts Optimisés** : Templates spécialisés par type de contenu

## 🏗️ Architecture

```
URL + Prompt → ExtractorManager
                    ↓
              Détection du type
                    ↓
         ┌──────────┴──────────┐
         ↓                     ↓
    DirectExtractor      AgentExtractor
    (Playwright)       (browser-use + LLM)
         ↓                     ↓
         └──────────┬──────────┘
                    ↓
              WebResult (JSON)
```

## 📋 Prérequis

- Python 3.11+
- Docker (optionnel, recommandé)
- Clé API pour votre LLM préféré (OpenAI, Anthropic, ou Albert)

## 🔧 Installation

### Option 1 : Installation Locale

```bash
# Cloner le repository
git clone https://github.com/your-org/webextract-service.git
cd webextract-service

# Installer les dépendances
pip install -e .

# Installer Playwright
playwright install chromium

# Configurer les variables d'environnement
cp .env.example .env
# Éditer .env avec vos clés API

# Lancer le service
uvicorn app.main:app --reload
```

### Option 2 : Docker (Recommandé)

```bash
# Construire l'image
docker-compose build

# Lancer le service
docker-compose up -d

# Vérifier les logs
docker-compose logs -f
```

## 🎯 Utilisation

### API REST

**Endpoint principal :**

```http
POST /api/v1/extract
Content-Type: application/json

{
  "url": "https://example.com/article",
  "prompt": "Extrait le contenu principal de cet article",
  "extraction_type": "article",
  "llm_config": {
    "provider": "openai",
    "api_key": "sk-...",
    "model": "gpt-4o"
  },
  "options": {
    "use_agent": true,
    "timeout": 45,
    "headless": true
  }
}
```

**Réponse :**

```json
{
  "success": true,
  "url": "https://example.com/article",
  "content_type": "article",
  "title": "Titre de l'article",
  "content": "Contenu extrait...",
  "metadata": {
    "extraction_method": "agent",
    "extraction_duration_ms": 3500,
    "content_length": 5000
  },
  "error": null
}
```

### Types d'Extraction Supportés

- `general` : Extraction générique (détection automatique)
- `article` : Article de blog ou actualité
- `product` : Page produit e-commerce
- `repository` : Dépôt de code (GitHub, GitLab)
- `documentation` : Documentation technique

### Providers LLM Supportés

- `openai` : OpenAI (GPT-4, GPT-4o, etc.)
- `anthropic` : Anthropic (Claude 3, Claude 3.5)
- `albert` : Albert API (LLM gouvernemental français)
- Tout provider compatible OpenAI API

## 🐳 Déploiement Docker

```yaml
# docker-compose.yml
version: '3.8'

services:
  webextract:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DEFAULT_LLM_PROVIDER=openai
      - DEFAULT_LLM_API_KEY=${OPENAI_API_KEY}
      - DEFAULT_LLM_MODEL=gpt-4o
      - PLAYWRIGHT_HEADLESS=true
    restart: unless-stopped
```

## 📚 Documentation API

Une fois le service lancé, accédez à :

- Documentation interactive : `http://localhost:8000/docs`
- Schéma OpenAPI : `http://localhost:8000/openapi.json`

## 🧪 Tests

```bash
# Installer les dépendances de dev
pip install -e ".[dev]"

# Lancer les tests
pytest

# Avec couverture
pytest --cov=app --cov-report=html
```

## 🔒 Sécurité

- Ne committez jamais vos clés API
- Utilisez des variables d'environnement ou des secrets managers
- En production, utilisez HTTPS et authentification

## 📖 Origine

Ce service a été extrait et adapté depuis le projet [Colaig](https://github.com/etalab-ia/albert-tchap),
un assistant IA pour l'administration française. Le système d'extraction web de Colaig utilise
des techniques avancées d'automation de navigateur avec des agents IA.

## 📝 License

MIT License

## 🤝 Contribution

Les contributions sont bienvenues ! Ouvrez une issue ou une pull request.

## 📧 Support

Pour toute question ou problème, ouvrez une issue sur GitHub.
