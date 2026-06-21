# Colaig — Assistant IA souverain, provider-agnostic

**Colaig** est un assistant IA conversationnel decentralise pour l'administration publique. Il s'integre dans les outils de communication et de stockage existants — quels que soient les providers — et repond aux questions des agents en s'appuyant sur leurs documents, avec citation des sources.

> *"Inviter Colaig, c'est comme inviter un collegue."*

Souverain (LLM Albert / Etalab), sans base de donnees, deployable en un seul container.

---

## Comment ca marche (le modele a comprendre)

Colaig n'est **pas** un bot par utilisateur. C'est **une instance** qui sert N salons et N utilisateurs, et dont le comportement change selon le **workspace** (espace de travail) auquel un salon est lie.

```
1 INSTANCE Colaig  (1 container, 1 compte bot)
   |
   |-- workspace "RH"            <- salons A, B    (docs RH, prompt RH, ACL RH)
   |-- workspace "Urbanisme"     <- salon C        (docs urba, prompt urba)
   |-- workspace "personal-alice" <- DM Alice       (espace prive + memoire)
   |-- workspace "personal-bob"   <- DM Bob          (espace prive + memoire)
```

- **Un salon** (Tchap, Telegram, webchat...) est **lie** a un workspace.
- **Le workspace** (`.colaig/config.yaml`) porte tout : quels documents, quel
  ton, quel prompt systeme, quels outils, qui a le droit.
- Le **meme** Colaig repond donc differemment d'un salon a l'autre.

**3 modes** resolus automatiquement :

| Situation | Mode | Comportement |
|---|---|---|
| Salon lie a un workspace metier | **Assistant** | Repond depuis les documents du workspace |
| Message direct (DM) | **Personnel** | Workspace prive par utilisateur, avec memoire |
| Salon non lie | **Chatbot** | Generique + onboarding (`colaig creer <nom>`) |

Et un **2e niveau** (multi-tenant) : un operateur peut faire tourner plusieurs instances, une par organisation cliente (`config/clients.yml` + provisioning).

Details : [docs/GUIDE_UTILISATEUR.md](docs/GUIDE_UTILISATEUR.md) et [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

---

## Provider-Agnostic

Colaig est **aveugle au provider**. Le code metier (RAG, contexte, reponses) utilise des interfaces abstraites ; l'implementation concrete est choisie a la configuration.

| Couche | Options disponibles |
|--------|---------------------|
| **Storage** | Local, WebDAV (Nextcloud/Bnum), Bigfolder, S3/MinIO, OneDrive/SharePoint (Graph), Box, Google Drive |
| **Messaging** | Matrix/Tchap, Telegram, Web chat (WebSocket) |
| **LLM** | Albert API (souverain), + tout endpoint OpenAI-compatible (Mistral, Azure, Ollama, vLLM/SSP Cloud) avec fallback par capacite |

## Zero Database

```
Persistence 100% via StorageProtocol
├── Documents metier    → backend de stockage
├── Configuration       → .colaig/config.yaml
├── Index vectoriels    → .colaig/indexes/*.faiss + *.pkl
├── Historiques         → .colaig/conversations/*.json
└── Cache local         → ephemere (reconstructible au restart)
```

Aucun PostgreSQL / Redis / Qdrant comme dependance de Colaig. **Un seul container.**

## Capacites

- **RAG source** : recherche hybride (dense + BM25 + RRF), reranking, HyDE, contextual chunking, citations.
- **Pipeline multi-agent** (opt-in) : Analyseur -> Orchestrateur agentique (tool-calling) -> Synthetiseur.
- **MCP** : serveur streamable HTTP (~23 tools) + client de connecteurs MCP externes.
- **Memoire** : conversationnelle semantique + memoire par utilisateur.
- **Taches autonomes** planifiees (Mode C), notifications proactives, delegation inter-workspaces, federation.
- **Auth** : token auto-localisant ou OIDC (RS256/ES256) ; provisioning multi-client + PlatformPolicy.
- **Administration reflexive** : en DM admin, l'agent cree/configure des workspaces et lie des salons en conversation ; droits scopes par owner de workspace.
- **Ops** : probes `/ready` + `/live`, request_id/W3C trace, suivi d'usage LLM par tenant (`/metrics` + `/metrics/prometheus`).
- **Securite** : delimitation anti-injection des documents, audit des citations (anti-hallucination), masquage des secrets en reponse.
- **Auto-specialisation** (opt-in) : derive persona/vocabulaire d'un workspace depuis son corpus.

Details : [docs/REFLEXIF_ET_OPS.md](docs/REFLEXIF_ET_OPS.md).

## Stack

Python 3.11+ · FastAPI · matrix-nio · faiss-cpu · httpx · MCP SDK · sentence-transformers (fallback embeddings) · structlog

## Demarrage rapide

```bash
# 1. Configuration
cp config/.env.example .env
# Choisir STORAGE_BACKEND / MESSAGING_BACKEND / LLM_BACKEND + credentials

# 2. Lancement (Docker)
docker-compose up -d

# 3. C'est tout. Webchat : http://localhost:8000/chat — Admin : http://localhost:8000/
```

Demarrage minimal (dev, sans service externe sauf un endpoint LLM) :

```bash
STORAGE_BACKEND=local
MESSAGING_BACKEND=webchat
LLM_BACKEND=albert
ALBERT_API_KEY=...
```

```bash
pip install -e ".[dev]"
python -m colaig.main
```

## Tests

```bash
pytest -q --ignore=tests/test_live.py   # ~1500 tests, hors smoke reseau
ruff check colaig
```

## Documentation

- [docs/GUIDE_UTILISATEUR.md](docs/GUIDE_UTILISATEUR.md) — guide utilisateur (workspaces, modes, usage)
- [docs/REFLEXIF_ET_OPS.md](docs/REFLEXIF_ET_OPS.md) — admin réflexive, ops/observabilité, sécurité, auto-spé
- [docs/EXPLOITATION.md](docs/EXPLOITATION.md) — runbook (déploiement, sauvegarde, upgrade, dépannage, pilote)
- [docs/SECURITE.md](docs/SECURITE.md) — modèle de menaces & sécurité
- [docs/CONFORMITE_RGPD.md](docs/CONFORMITE_RGPD.md) — conformité & RGPD
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — architecture de reference
- [docs/STORAGE_ABSTRACTION.md](docs/STORAGE_ABSTRACTION.md) — StorageProtocol + MessagingProtocol
- [CLAUDE.md](CLAUDE.md) — principes, stack, conventions
- [colaig/protocols.py](colaig/protocols.py) — contrats d'interface

## Licence

Licence Ouverte 2.0 — CEREMA Mediterranee / GIDI. Voir [LICENSE](LICENSE).
