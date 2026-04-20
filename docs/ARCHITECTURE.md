# COLAIG — Synthèse Complète & Architecture de Référence

## Document de refactorisation pour repartir de zéro

---

# PARTIE 1 : QU'EST-CE QUE COLAIG ?

## 1.1 Vision fondamentale

**Colaig est un assistant IA conversationnel personnel, décentralisé et provider-agnostic.** C'est un "collègue virtuel" qui s'intègre dans les outils de communication et de stockage de l'utilisateur — quels qu'ils soient.

Le principe fondateur est radical dans sa simplicité : **"Inviter Colaig, c'est comme inviter un collègue."** On lui donne accès à ses documents (via n'importe quel provider), on lui parle (via n'importe quel canal de messagerie), et il devient opérationnel dans ce contexte.

### Cas d'usage principal : administration publique française
Le premier déploiement cible l'écosystème numérique de l'État (Bnum) : Tchap pour communiquer, Nextcloud/Bnum pour les documents, Albert API pour le LLM souverain. Mais l'architecture est conçue pour fonctionner avec d'autres providers.

### Provider-agnostic
Colaig est **aveugle au provider**. Le code métier (RAG, contexte, réponses) utilise des interfaces abstraites (`StorageProtocol`, `MessagingProtocol`). L'implémentation concrète est choisie à la configuration :

| Couche | Options |
|--------|---------|
| **Storage** | WebDAV (Nextcloud), Bigfolder (multi-provider), Filesystem local, S3 |
| **Messaging** | Matrix/Tchap, Slack, Web chat |
| **LLM** | Albert API (souverain) |

Voir [docs/STORAGE_ABSTRACTION.md](STORAGE_ABSTRACTION.md) pour la spécification technique complète.

## 1.2 Le problème résolu

Les organisations souffrent de :

- **Silos informationnels** : chaque service détient sa documentation sans la partager efficacement
- **Expertise fragmentée** : le savoir-faire est dans les têtes, pas dans les systèmes
- **Procédures complexes** : des milliers de documents réglementaires, techniques, administratifs
- **Turnover et départs** : l'expertise part avec les agents (retraites, mutations)
- **Outils inadaptés** : les solutions IA génériques ne comprennent pas le contexte métier
- **Documents éparpillés** : OneDrive ici, Nextcloud là, SharePoint ailleurs — pas de vue unifiée

## 1.3 La réponse Colaig

Colaig transforme la documentation statique en connaissance accessible par la conversation. Au lieu de chercher dans 15 systèmes différents, l'utilisateur pose une question en langage naturel et obtient une réponse sourcée, contextualisée et adaptée à son espace de travail — quels que soient les providers sous-jacents.

---

# PARTIE 2 : LES 5 NIVEAUX D'ÉVOLUTION

Le livre blanc Colaig définit une trajectoire évolutive en 5 niveaux, chacun s'appuyant sur le précédent. C'est la colonne vertébrale conceptuelle du projet.

## Niveau 1 — RAG : Le Conseiller Documentaire

**Principe** : Indexation sémantique des documents + recherche contextuelle + génération de réponses sourcées.

**Capacités** :
- Indexation automatique des documents présents dans l'espace Nextcloud partagé
- Recherche sémantique dans la base documentaire (embeddings + vector store)
- Génération de réponses basées sur les documents officiels avec citation des sources
- Adaptation au contexte de la conversation

**Cas d'usage** : "Quelle est la procédure pour valider un dossier de type X ?" → Colaig identifie le document pertinent, extrait le passage, synthétise la réponse avec la référence.

**C'est le socle indispensable.** Sans RAG fonctionnel, rien d'autre n'a de sens.

## Niveau 2 — Workflow : Le Planificateur d'Actions

**Principe** : Colaig acquiert la capacité d'orchestrer des séquences d'actions via des outils connectés.

**Capacités** :
- Catalogue d'actions connectées aux systèmes existants (via MCP tools, n8n, APIs)
- Génération dynamique de workflows pour résoudre des problèmes
- Exécution séquentielle d'opérations sur services internes et externes
- Composition et réutilisation de workflows

**Cas d'usage** : "Génère-moi un rapport de synthèse sur le projet X" → Colaig identifie les sources, extrait les données, génère des visualisations, compile un document.

## Niveau 3 — Personnalisation : L'Expert Configurable

**Principe** : Les experts métier peuvent configurer Colaig sans compétences techniques pour créer des assistants spécialisés.

**Capacités** :
- Interface de configuration conversationnelle (on configure Colaig en lui parlant)
- Templates métier adaptables (juridique, technique, RH, budget...)
- Spécialisation par domaine d'expertise
- Capture et encapsulation du savoir-faire des experts

**Cas d'usage** : Un expert juridique configure son Colaig pour analyser les contrats selon une grille spécifique. Il partage cette configuration → tous ses collègues bénéficient de son expertise.

## Niveau 4 — Réseau : L'Écosystème Interconnecté

**Principe** : Les instances Colaig communiquent entre elles via Tchap, formant un réseau qui reflète la structure organisationnelle.

**Capacités** :
- Communication inter-instances via le protocole Matrix
- Organisation hiérarchique reflétant la structure administrative
- Contrôle précis des flux d'information (qui peut interroger qui)
- Cartographie dynamique des compétences disponibles

**Cas d'usage** : L'assistant d'un agent ne trouve pas l'info → il consulte automatiquement le Colaig du service compétent, qui lui fournit la réponse selon les règles de partage établies.

## Niveau 5 — Intelligence Collective : Le Système Vivant

**Principe** : Amélioration continue des connaissances par les contributions des utilisateurs.

**Capacités** :
- Détection et signalement des informations obsolètes
- Système de vote pour valider les corrections
- Scores de confiance adaptés à l'expertise des contributeurs
- Propagation des améliorations à travers le réseau

**Cas d'usage** : Un agent remarque qu'une procédure a changé → il signale la correction → validation par les experts → mise à jour automatique pour tous.

---

# PARTIE 3 : ARCHITECTURE TECHNIQUE DE RÉFÉRENCE

## 3.1 Principes architecturaux fondamentaux

### Séparation des préoccupations
Le système repose sur une séparation complète entre :
1. **Construction autonome du contexte** (système indépendant)
2. **Utilisation du contexte par les LLM** (via interface standardisée)

Cette séparation permet au contexte d'évoluer selon ses propres règles et à n'importe quel LLM de s'y connecter.

### Monolithe décentralisé — Zero Database, Provider-Agnostic
Le choix architectural est celui d'un **monolithe modulaire** déployé de manière décentralisée : chaque structure héberge sa propre instance autonome. C'est un choix délibéré — la simplicité de déploiement prime sur la sophistication architecturale.

Le principe **"Zero Database"** est fondamental : Colaig n'utilise **aucune base de données propre** (pas de PostgreSQL, pas de SQLite, pas de Qdrant, pas de ChromaDB). La persistence passe par le `StorageProtocol` : documents, configuration, index FAISS (fichiers binaires), métadonnées (JSON/pickle), historiques. Le cache local est éphémère et reconstructible.

Le principe **"Provider-Agnostic"** complète le Zero Database : le code métier utilise des interfaces abstraites (`StorageProtocol`, `MessagingProtocol`) et ne connaît jamais l'implémentation concrète. Changer de provider = changer une variable d'environnement, pas du code.

### Intégration native dans l'écosystème existant
Colaig n'ajoute AUCUNE complexité technique : il utilise les permissions natives du provider (Nextcloud, OneDrive...), les canaux natifs de messagerie (Tchap, Slack...), et les documents existants. Zéro système ACL custom.

### Bigfolder comme passerelle multi-provider
Pour les déploiements multi-provider, Colaig peut utiliser **Bigfolder (Archivist)** comme backend de stockage. Bigfolder gère la complexité multi-provider (OneDrive, Box, Google Drive, WebDAV, S3) et expose une API REST unifiée. Colaig le voit comme un simple `StorageProtocol` — il ne sait pas ce qu'il y a derrière.

## 3.2 Stack technique cible

```yaml
Core :
  - Language : Python 3.11+
  - Framework : FastAPI (web UI + API)
  - Abstractions : StorageProtocol + MessagingProtocol (provider-agnostic)

Persistence — Philosophie "Zero Database, Provider-Agnostic" :
  - Base données : AUCUNE dans Colaig
  - Vector store : FAISS (fichiers .faiss + .pkl via StorageProtocol)
  - Cache : In-memory (asyncio) — perdu au restart, acceptable
  - Métadonnées : JSON via StorageProtocol

Storage backends (interchangeables) :
  - WebDAV/Nextcloud : Déploiement admin publique (Bnum)
  - Bigfolder API : Multi-provider (OneDrive, Box, Google Drive, WebDAV, S3)
  - Filesystem local : Dev, tests, démo
  - S3/MinIO : Déploiement cloud

Messaging backends (interchangeables) :
  - Matrix/Tchap : Admin publique (via matrix-nio)
  - Slack : Entreprise (futur)
  - Web chat : Interface web intégrée (futur)

LLM :
  - Albert API : LLM souverain pour génération + embeddings (Etalab/DINUM)

Packaging :
  - Docker : Image all-in-one (~2GB)
  - Déploiement : docker-compose.yml
  - Empreinte : 1-2 vCPU, 2-4GB RAM, 10-20GB disk
```

### Pourquoi FAISS et pas une base vectorielle (Qdrant, ChromaDB, etc.) ?

Le choix FAISS est un pilier architectural fondamental de Colaig, directement lié à la philosophie **"Zero Database"** :

1. **Pas de service supplémentaire à déployer** — FAISS est une bibliothèque Python pure, pas un serveur. Pas de container Qdrant, pas de port à ouvrir, pas de service à monitorer.

2. **Index = fichiers sur WebDAV** — Les index FAISS se sérialisent en fichiers binaires (.faiss) avec leurs métadonnées (.pkl ou .json). Ces fichiers vivent sur Nextcloud/WebDAV comme n'importe quel document, dans le dossier `/.colaig/indexes/` de chaque workspace.

3. **Portabilité totale** — Chaque instance Colaig porte ses index dans son espace WebDAV. Migrer une instance = copier un dossier. Sauvegarder = backup WebDAV standard.

4. **Cohérence avec la décentralisation** — Pas besoin d'un cluster vectoriel partagé. Chaque structure a ses fichiers FAISS dans son Nextcloud.

5. **Simplicité de déploiement** — `pip install faiss-cpu` et c'est tout. Compatible avec la promesse "déploiement en 5 minutes".

### Fonctionnement concret des index FAISS dans Colaig

```
DÉMARRAGE INSTANCE :
  1. Connexion WebDAV
  2. Téléchargement /.colaig/indexes/documents.faiss + documents.pkl
  3. Chargement en mémoire : faiss.read_index("documents.faiss")
  4. Prêt à répondre

INDEXATION NOUVEAU DOCUMENT :
  1. Extraction texte (PDF, DOCX, etc.)
  2. Chunking intelligent selon type
  3. Embeddings via Albert API /embeddings
  4. faiss.index.add(embeddings_array)
  5. Sauvegarde metadata dans .pkl
  6. Upload .faiss + .pkl sur WebDAV

RECHERCHE :
  1. Embedding de la question via Albert API
  2. faiss.index.search(query_embedding, k=5)
  3. Récupération métadonnées des chunks trouvés
  4. Construction prompt avec contexte documentaire
  5. Appel Albert API /chat/completions
```

## 3.3 Architecture contextuelle hiérarchisée — Les 5 couches

C'est le cœur conceptuel de Colaig. Chaque message reçu déclenche la construction d'un contexte en 5 couches :

### Couche 1 — Comportement (Fondation)
Définit le ton, la formalité, le niveau d'expertise, les contraintes réglementaires. Adapté selon l'espace de travail (formel en RH, technique en IT, collaboratif sur un projet).

### Couche 2 — Capacités (Outils disponibles)
Identifie les outils et fonctionnalités pertinents pour cet espace. Filtrage selon les habilitations. Exposition sélective (outils RH dans espace RH uniquement).

### Couche 3 — Conversation (Historique et intentions)
Récupération de l'historique pertinent par espace. Analyse des intentions en contexte. Continuité entre salon Tchap et messages directs.

### Couche 4 — Connaissances (Base documentaire)
Recherche RAG dans les documents autorisés de l'espace WebDAV. Priorisation selon critères comportementaux. Sécurité par héritage des permissions Nextcloud.

### Couche 5 — Profil Utilisateur (Préférences)
Informations utilisateur pertinentes. Construction en parallèle des autres couches. Personnalisation de l'expérience.

## 3.4 Le Context Resolver — Cœur nerveux

Le Context Resolver est le composant le plus critique de Colaig. Pour chaque message reçu, il détermine :

**Qui parle ?** → Utilisateur Matrix identifié
**D'où ?** → Salon Tchap (public, équipe, DM)
**Quel espace de travail ?** → Mapping salon→dossier Nextcloud
**Quelles ressources mobiliser ?** → Documents, outils, prompt, persona

### Algorithme de résolution

```
MESSAGE REÇU
    ↓
1. Extraction : user_id + conversation_id + conversation_type
    ↓
2. Cache rapide : conversation_id → workspace ?
   OUI → mode = ASSISTANT (chemin fast, <5ms)
    ↓
3. Cache expiré → list_workspaces() depuis storage
   a. conversation_id dans ws.conversations → mode = ASSISTANT
   b. DM → find_workspace_for_user(user_id) → mode = ASSISTANT (si trouvé)
   c. DM sans workspace → get_or_create_personal_workspace() → mode = PERSONAL
   d. Salon public/privé non mappé → mode = CHATBOT
    ↓
4. Construire les 5 couches contextuelles
    ↓
5. [PreExecution] Charger en parallèle :
   - UserProfile (profile.json via user_memory)
   - Faits mémoire user (FAISS search via FaissIndexRegistry)
   - Behaviors + Skills du workspace
    ↓
6. Appeler pipeline agents → réponse Albert API
    ↓
7. [Post-turn] schedule_extract() → asyncio.create_task()
   (extraction faits LLM en fire-and-forget)
```

### Gestion des cas limites (graceful degradation)

Aucune interaction ne doit échouer silencieusement. Le système prévoit :

- **Salon public inconnu** → Comportement chatbot FAQ, invitation à configurer
- **DM sans workspace** → `get_or_create_personal_workspace()` crée le workspace sur storage ; si storage down → workspace en mémoire uniquement
- **Storage indisponible** → Cache TTL reste valide, réponse dégradée avec message explicatif
- **Albert API down** → Queue de messages, retry avec backoff
- **Document non indexé** → Indexation à la volée si possible
- **UserMemory indisponible** → Réponse sans mémoire user (enrichissement facultatif)

## 3.5 Le modèle Workspace (Espace de Travail)

Un workspace Colaig = 1 espace documentaire (sur n'importe quel StorageProtocol) + 1 conversation (sur n'importe quel MessagingProtocol) + N membres.

### Structure standard d'un workspace

```
/espace-de-travail/                 (via StorageProtocol — WebDAV, Bigfolder, local...)
├── documents/                      # Documents métier (base RAG)
│   ├── procedures/
│   ├── guides/
│   └── templates/
├── .colaig/                        # Configuration Colaig
│   ├── config.yaml                 # Config principale (comportement, RAG, capabilities)
│   ├── behavior.yaml               # Comportements détaillés
│   ├── permissions.yaml            # Accès utilisateurs
│   ├── vocabulary.yaml             # Vocabulaire spécialisé
│   └── indexes/                    # Index FAISS stockés
│       ├── documents.faiss         # Index vectoriel binaire
│       ├── documents.pkl           # Métadonnées des chunks (texte, source, page)
│       ├── embeddings_cache/       # Cache des embeddings calculés
│       └── ocr_results/            # Cache OCR Albert
├── .colaig_shared/                 # Coordination inter-instances (Niveau 4)
│   ├── accessible_spaces.yaml
│   └── collaboration_cache/
└── (tout autre contenu)
```

### Configuration workspace (.colaig-config.json)

```json
{
  "workspace_id": "projet-covid-vaccins",
  "name": "Projet COVID Vaccins",
  "behavior": {
    "system_prompt": "Tu es l'assistant du projet COVID...",
    "tone": "formal_medical",
    "expertise_level": "expert",
    "vocabulary_set": "medical_administratif",
    "language": "fr"
  },
  "rag": {
    "search_strategy": "hybrid",
    "max_results": 5,
    "similarity_threshold": 0.7,
    "priority_documents": ["protocole-2025.pdf"]
  },
  "capabilities": {
    "tools_enabled": ["search", "summarize", "compare"],
    "tools_disabled": ["write", "delete"],
    "external_apis": []
  },
  "security": {
    "classification_level": "confidential",
    "allowed_domains": ["sante.gouv.fr"]
  }
}
```

## 3.6 Modes d'interaction

### Mode Assistant (workspace trouvé)
- Accès complet aux documents de l'espace
- Persona spécialisé selon la config
- Outils contextuels activés
- Réponses sourcées avec références

### Mode Chatbot (salon public non configuré)
- Comportement généraliste
- Invitation à associer un espace de travail
- Fonctionnalités limitées au minimum
- Guidage vers la configuration

### Mode Personnel (DM)
- Workspace personnel automatiquement créé et persisté sur storage
- Chemin : `/.colaig/personal/{safe_user_id}/` — vrai workspace, pas un espace virtuel
- `rag_enabled: false` par défaut (pas de RAG documentaire sur le workspace personnel)
- Mémoire sémantique per-user activée : extraction de faits à chaque échange, consolidation ~1h
- UserProfile structuré (rôle, compétences, préférences) persisté dans `.colaig/users/{safe_user_id}/profile.json`

---

# PARTIE 4 : COMPOSANTS TECHNIQUES DÉTAILLÉS

## 4.1 Couche Messaging (Provider-Agnostic)

Le code métier utilise `MessagingProtocol` — interface abstraite indépendante du canal de communication. L'implémentation concrète est injectée au démarrage.

### MessagingProtocol — Interface
```python
class MessagingProtocol(Protocol):
    async def connect(self) -> None: ...
    async def run(self) -> None: ...
    async def send(self, conversation_id: str, text: str) -> None: ...
    async def send_typing(self, conversation_id: str) -> None: ...
    def on_message(self, callback) -> None: ...
```

### Implémentation Matrix/Tchap (principale)
Client Matrix standard (matrix-nio) qui :
- Se connecte au homeserver Matrix/Tchap comme un utilisateur normal
- Écoute les messages dans les salons où il est invité
- Répond aux mentions (@colaig) et aux DM
- Envoie des messages formatés avec sources

```yaml
Identité :
  user_id : "@colaig:tchap.gouv.fr"
  display_name : "Colaig"

Événements traités :
  - m.room.message (texte, fichier, image)
  - m.room.member (invitation, départ)
  - m.room.name (renommage salon)

Configuration :
  auto_join : true (accepte toutes les invitations)
  response_delay : 1s (anti-spam)
  typing_indicator : true (indicateur de frappe)
```

### Implémentations futures
- **Slack** : Via Slack Bolt SDK
- **Web chat** : WebSocket intégré dans FastAPI

## 4.2 Couche Storage (Provider-Agnostic)

Le code métier utilise `StorageProtocol` — interface abstraite indépendante du backend de stockage. L'implémentation concrète est injectée au démarrage.

### StorageProtocol — Interface
```python
class StorageProtocol(Protocol):
    async def list_files(self, path: str, recursive: bool = False) -> list[StorageFile]: ...
    async def download(self, path: str) -> bytes: ...
    async def download_if_changed(self, path: str, known_etag: str) -> bytes | None: ...
    async def upload(self, path: str, content: bytes) -> None: ...
    async def mkdir(self, path: str) -> None: ...
    async def exists(self, path: str) -> bool: ...
    async def get_etag(self, path: str) -> str | None: ...
    async def delete(self, path: str) -> None: ...
```

### Implémentation WebDAV/Nextcloud (principale)
Parle directement à Nextcloud via le protocole WebDAV :
- **PROPFIND** : Lister les fichiers et métadonnées d'un dossier
- **GET** : Télécharger un document pour indexation
- **PUT** : Écrire des fichiers (index, config, historique)
- **MKCOL** : Créer des répertoires
- Surveillance des modifications (etag) pour ré-indexation

```yaml
Endpoint : https://bnum.din.gouv.fr/remote.php/dav/files/colaig/
Auth : Basic HTTP ou App Password
Timeout : 30s
SSL : Vérifié
```

### Implémentation Bigfolder (multi-provider)
Parle à l'API REST d'Archivist/Bigfolder. Bigfolder gère la complexité multi-provider (OneDrive, Box, Google Drive, WebDAV, S3) en interne. Colaig voit un seul espace de fichiers unifié.

```yaml
Endpoint : http://bigfolder:8002/api
Auth : API Key (X-API-Key: ark_xxxxx)
Timeout : 30s
```

### Implémentation Local (filesystem)
Lit/écrit sur le système de fichiers local. Utilisé pour le développement, les tests, et les démos sans infrastructure.

### Implémentation S3 (optionnel)
Parle à un bucket S3 ou MinIO. Utilisé pour le déploiement cloud.

Voir [docs/STORAGE_ABSTRACTION.md](STORAGE_ABSTRACTION.md) pour la spécification technique complète des implémentations.

## 4.3 Pipeline RAG

Le pipeline RAG est le moteur de recherche documentaire :

### Indexation
1. **Détection** : Surveillance des modifications WebDAV (polling PROPFIND ou checksum)
2. **Extraction** : Extraction texte selon format (PDF via Albert OCR, DOCX, ODT, TXT, MD, HTML)
3. **Chunking** : Découpage intelligent multi-stratégie adapté au type de document
   - Markdown : chunking par sections (titres #, ##, ###)
   - PDF : OCR Albert puis chunking par paragraphes
   - Générique : sliding window (800 tokens, overlap 100)
4. **Embedding** : Génération de vecteurs via Albert API /embeddings (ou SentenceTransformer local en fallback)
5. **Stockage** : `faiss.index.add()` + métadonnées dans .pkl (source, page, date, workspace)
6. **Persistance** : Sérialisation `faiss.write_index()` → upload sur WebDAV dans `/.colaig/indexes/`

### Recherche
1. **Embedding query** : Vectorisation de la question via Albert API
2. **Recherche FAISS** : `faiss.index.search(query_embedding, k)` — similarité L2 ou cosinus
3. **Recherche hybride** : Combinaison vectorielle + mots-clés (boost BM25)
4. **Reranking** : MMR (Maximum Marginal Relevance) pour diversité + boost métier (documents récents, même service)
5. **Filtrage** : Par workspace, classification, date via métadonnées .pkl
6. **Extraction** : Passages les plus pertinents avec score de confiance

### Génération
1. **Construction prompt** : System prompt (workspace) + contexte documentaire + question
2. **Appel Albert API** : POST /v1/chat/completions
3. **Post-traitement** : Vérification hallucinations, ajout citations sources
4. **Formatage** : Markdown adapté à Tchap

## 4.4 Albert API (LLM Souverain)

Albert est le LLM souverain développé par le Lab IA d'Etalab (DINUM).

```yaml
Endpoint : https://albert-api.etalab.gouv.fr
Auth : API Key par structure

Modèles :
  - AgentPublic/albertlight-7b : Rapide (~1-2s), réponses courtes
  - AgentPublic/albert-large : Précis (~3-5s), analyses complexes

Capabilities :
  - Chat completions (génération texte)
  - Embeddings (vectorisation texte)
  - Streaming (optionnel)

Spécialisation :
  - Fine-tuning corpus administratif français
  - Vocabulaire technique admin maîtrisé
  - Ton adapté communication publique
```

---

# PARTIE 5 : STRUCTURE PROJET CIBLE

## 5.1 Arborescence monolithe modulaire

```
colaig/
├── colaig/
│   ├── __init__.py
│   ├── main.py                    # FastAPI app + messaging runner + factory
│   ├── config.py                  # Configuration management
│   ├── models.py                  # Dataclasses partagées
│   ├── protocols.py               # Interfaces (Protocol classes)
│   ├── exceptions.py              # Hiérarchie d'exceptions
│   │
│   ├── messaging/                 # Canaux de communication (provider-agnostic)
│   │   ├── __init__.py
│   │   ├── matrix.py              # Implémentation Matrix/Tchap
│   │   └── handlers.py            # Routage messages → resolver → réponse
│   │
│   ├── context/                   # Context Resolver (cœur)
│   │   ├── __init__.py
│   │   ├── resolver.py            # Algorithme résolution workspace (→ mode ASSISTANT/CHATBOT/PERSONAL)
│   │   ├── layers.py              # Les 5 couches contextuelles
│   │   ├── workspace.py           # Gestion workspaces + get_or_create_personal_workspace()
│   │   └── user_memory.py         # Mémoire sémantique per-user (3 rythmes : read/extract/consolidate)
│   │
│   ├── rag/                       # Pipeline RAG
│   │   ├── __init__.py
│   │   ├── indexer.py             # Indexation documents
│   │   ├── chunker.py             # Chunking intelligent
│   │   ├── embeddings.py          # Embeddings (Albert/SentenceTransformer)
│   │   ├── faiss_store.py         # Gestion index FAISS — API sync + async, public internals
│   │   ├── index_registry.py      # Registre centralisé FAISS — search_multi() parallèle
│   │   ├── retriever.py           # Recherche hybride + reranking
│   │   └── generator.py           # Génération réponses
│   │
│   ├── integrations/              # Implémentations concrètes
│   │   ├── __init__.py
│   │   ├── storage/               # Implémentations StorageProtocol
│   │   │   ├── __init__.py
│   │   │   ├── webdav.py          # WebDAVStorage (Nextcloud/Bnum)
│   │   │   ├── bigfolder.py       # BigfolderStorage (API Archivist)
│   │   │   ├── local.py           # LocalStorage (filesystem)
│   │   │   └── s3.py              # S3Storage (MinIO/S3)
│   │   └── albert.py              # Client Albert API (chat + embeddings + OCR)
│   │
│   ├── storage/                   # Cache en mémoire
│   │   ├── __init__.py
│   │   └── cache.py               # Cache in-memory avec TTL
│   │
│   ├── web/                       # Interface admin web
│   │   ├── __init__.py
│   │   ├── routes.py              # Routes FastAPI
│   │   ├── templates/             # Jinja2 (HTMX)
│   │   └── static/                # CSS/JS minimal
│   │
│   └── utils/
│       ├── __init__.py
│       ├── logging.py
│       └── text.py
│
├── data/                          # Volume Docker monté (cache local uniquement)
│   ├── faiss_cache/               # Cache local des index FAISS
│   └── logs/                      # Logs applicatifs
│
├── config/                        # Configuration
│   ├── default.yml                # Config par défaut
│   └── .env.example               # Variables d'environnement
│
├── docs/                          # Documentation
│   ├── ARCHITECTURE.md            # Ce fichier
│   ├── STORAGE_ABSTRACTION.md     # Spec technique StorageProtocol + MessagingProtocol
│   ├── USER_MEMORY.md             # Mémoire sémantique per-user + FaissIndexRegistry + concurrence FAISS
│   ├── PHASE6_ARCHITECTURE.md     # Pipeline Phase 6 (Trame, Profile, PreExecution)
│   └── PIPELINE_GRAPH.md          # Cartographie des configurations pipeline
│
├── Dockerfile                     # Image all-in-one
├── docker-compose.yml             # Déploiement
├── pyproject.toml
└── README.md
```

## 5.2 Docker Compose de déploiement

```yaml
# Phase 1 : Un seul container, Zero Database
version: '3.8'
services:
  colaig:
    build: .
    ports:
      - "8000:8000"
    environment:
      - MATRIX_HOMESERVER=${MATRIX_HOMESERVER}
      - MATRIX_USERNAME=${MATRIX_USERNAME}
      - MATRIX_PASSWORD=${MATRIX_PASSWORD}
      - ALBERT_API_KEY=${ALBERT_API_KEY}
      - NEXTCLOUD_URL=${NEXTCLOUD_URL}
      - NEXTCLOUD_USERNAME=${NEXTCLOUD_USERNAME}
      - NEXTCLOUD_PASSWORD=${NEXTCLOUD_PASSWORD}
    volumes:
      - ./data:/app/data    # Cache local uniquement (source de vérité = WebDAV)
      - ./config:/app/config
    restart: unless-stopped
    # Note : PAS de PostgreSQL, PAS de Redis, PAS de Qdrant
    # Tout est sur WebDAV : documents, config, index FAISS, métadonnées
```

---

# PARTIE 6 : CAPACITÉS PAR COMBINAISON

Le génie de l'architecture Colaig réside dans les capacités émergentes par combinaison de ses composants de base.

## 6.1 Capacités Niveau 1 (RAG pur)

| Composant | Capacité débloquée |
|-----------|-------------------|
| WebDAV + Indexer | Documents automatiquement indexés dès qu'ils sont dans le dossier partagé |
| FAISS + Retriever | Recherche sémantique dans tous les documents du workspace |
| Albert + Generator | Réponses en langage naturel avec citations des sources |
| Bot Tchap | Interface conversationnelle naturelle dans l'outil quotidien |
| Context Resolver | Réponses adaptées au workspace (contexte projet, équipe, FAQ) |

## 6.2 Capacités Niveau 2 (Workflow)

| Combinaison | Capacité débloquée |
|-------------|-------------------|
| RAG + MCP Tools | Recherche documentaire + actions concrètes (créer fichier, envoyer mail) |
| Albert + Tool calling | Le LLM décide quels outils appeler selon la demande |
| n8n + Webhooks | Automatisation de processus complexes déclenchés par Colaig |
| Grist + Albert | Interrogation et mise à jour de bases de données collaboratives |

## 6.3 Capacités Niveau 3 (Personnalisation)

| Combinaison | Capacité débloquée |
|-------------|-------------------|
| .colaig-config + Prompts | Comportement sur mesure par workspace sans code |
| Templates + Expert | Un expert métier crée un assistant spécialisé en décrivant son besoin |
| Config conversationnelle | "Colaig, à partir de maintenant dans cet espace, tu dois..." |

## 6.4 Capacités Niveau 4 (Réseau)

| Combinaison | Capacité débloquée |
|-------------|-------------------|
| Multi-instances + Matrix | Les Colaig se parlent entre eux via le protocole fédéré |
| Routage vectoriel | Requête routée vers l'instance la plus compétente sémantiquement |
| Permissions Tchap | Le réseau respecte naturellement les structures hiérarchiques |

## 6.5 Capacités Niveau 5 (Intelligence Collective)

| Combinaison | Capacité débloquée |
|-------------|-------------------|
| Feedback + Scoring | Les utilisateurs améliorent les réponses, les meilleurs contributeurs ont plus de poids |
| Propagation réseau | Une correction validée localement se propage à toutes les instances |
| Détection obsolescence | Colaig signale quand un document n'a pas été mis à jour depuis longtemps |

---

# PARTIE 7 : INTÉGRATIONS AVANCÉES (CEREMA)

## 7.1 Colaig + Colette (RAG Multimodal)

Colette (open-source, Etalab) apporte des capacités RAG avancées à Colaig :
- Traitement multimodal (images, tableaux, graphiques dans les PDF)
- Chunking intelligent adapté aux documents administratifs
- Reranking avancé

## 7.2 Colaig + LEANN (Indexation Ultra-Compacte)

LEANN (UC Berkeley) apporte une compression d'index révolutionnaire :
- Réduction 97% de la taille des index par rapport à FAISS standard
- Recherche sub-seconde sur millions de documents
- Déploiement edge possible (instances ultra-légères par collectivité)
- Potentiel remplacement des fichiers .faiss par des fichiers .leann encore plus compacts

## 7.3 Colaig + Grist (Données Structurées)

Grist apporte la dimension données structurées :
- Interrogation de bases de données collaboratives
- Mise à jour de tableaux de bord
- Formulaires intelligents

## 7.4 Colaig + n8n (Orchestration)

n8n apporte l'automatisation de workflows :
- Connexion à des APIs tierces
- Déclenchement d'actions complexes
- Pipelines de traitement documentaire

---

# PARTIE 8 : ROADMAP DE DÉPLOIEMENT

## Phase 1 — POC (0-3 mois) : 50 agents, 10 req/jour

```yaml
Architecture : Monolithe modulaire, Zero Database
Déploiement : 1 container Docker
Coût infra : ~15-30€/mois (1 VPS)
Focus :
  - Bot Tchap fonctionnel
  - WebDAV connecté (docs + config + index FAISS)
  - RAG basique avec FAISS in-memory + persistance WebDAV
  - Albert API pour génération + embeddings
  - 2-3 workspaces pilotes
```

## Phase 2 — Pilote (3-12 mois) : 200 agents

```yaml
Architecture : Monolithe + Redis (cache/events seulement)
Déploiement : Docker Compose (2 containers : Colaig + Redis)
Coût infra : ~100-200€/mois
Focus :
  - Context Resolver complet
  - Interface admin web (FastAPI + HTMX)
  - Monitoring et observabilité
  - Multi-workspaces avec index FAISS séparés
  - Début Niveau 2 (outils MCP)
  - Cache Redis pour embeddings fréquents
```

## Phase 3 — Production (12+ mois) : 500+ agents

```yaml
Architecture : Évaluer micro-services si >5000 users
Déploiement : Docker Compose ou Kubernetes
Focus :
  - Niveau 3 (personnalisation sans code)
  - Début réseau inter-instances
  - Intégration Colette/LEANN
  - Fédération inter-structures
```

---

# PARTIE 9 : PRINCIPES DE CONCEPTION INVIOLABLES

1. **Provider-Agnostic** : Colaig est aveugle au provider. Toute I/O passe par des interfaces abstraites (StorageProtocol, MessagingProtocol). Changer de backend = changer une variable d'environnement, pas du code.

2. **Souveraineté LLM** : Albert API exclusivement pour le LLM. Les backends de stockage (Bigfolder, etc.) peuvent utiliser d'autres services en interne — c'est leur affaire, pas celle de Colaig.

3. **Zero Database** : Colaig n'utilise aucune base de données propre. La persistence passe par le StorageProtocol.

4. **Simplicité** : Un `docker-compose up` doit suffire à démarrer. Si c'est trop compliqué à déployer, c'est trop compliqué.

5. **Naturalité** : L'UX est "inviter un collègue", pas "configurer un système IA". Zero formation technique requise pour les utilisateurs.

6. **Décentralisation** : Chaque structure est autonome. Pas de point central de défaillance. Chaque instance est souveraine sur ses données.

7. **Évolution organique** : Chaque niveau augmente le précédent sans le remplacer. On peut rester au Niveau 1 indéfiniment si c'est suffisant.

8. **Sécurité par héritage** : Colaig hérite des permissions du provider de stockage et de messagerie. Si un utilisateur n'a pas accès à un dossier, Colaig non plus.

9. **Open Source** : Code ouvert, Licence Ouverte 2.0 (Etalab). Pas de vendor lock-in.

10. **RGPD natif** : Pas de transfert de données hors UE. Logs minimaux. Droit à l'oubli respecté.

---

# PARTIE 10 : CE QUI A ÉTÉ EXPLORÉ MAIS DOIT ÊTRE SIMPLIFIÉ

## Approches testées dans les itérations précédentes

| Approche | Verdict | Raison |
|----------|---------|--------|
| CrewAI (multi-agents) | ❌ Abandonner | Trop complexe, overhead inutile pour le Niveau 1-2 |
| Architecture micro-services | ❌ Prématuré | Monolithe modulaire suffit jusqu'à >5000 users |
| Redis comme event bus | ⏳ Phase 2 | Pas nécessaire pour le POC (asyncio.Queue suffit) |
| Kubernetes | ⏳ Phase 3+ | Overkill tant qu'on n'a pas validé le produit |
| MCP servers séparés | ⏳ Phase 2 | Un seul process monolithe d'abord |
| Mistral AI | ❌ Remplacer | Albert API est le choix souverain |
| Qdrant / ChromaDB / bases vectorielles | ❌ Abandonner | FAISS + fichiers via StorageProtocol = Zero Database |
| SQLite / PostgreSQL dans Colaig | ❌ Abandonner | Pas de DB dans Colaig (Bigfolder utilise PostgreSQL en interne, c'est son affaire) |
| WebDAV câblé en dur | ❌ Remplacer | StorageProtocol abstrait — WebDAV est UNE implémentation parmi d'autres |
| Matrix câblé en dur | ❌ Remplacer | MessagingProtocol abstrait — Matrix est UNE implémentation parmi d'autres |
| Système de routage vectoriel inter-instances | ⏳ Niveau 4 | Pas avant d'avoir un Niveau 1-2 solide (utilisera des index FAISS répertoires) |
| LEANN | ⏳ Niveau 2-3 | Optimisation qui n'a de sens qu'avec volume |

## Ce qu'il faut garder absolument

- Le concept de **Context Resolver** avec les 5 couches
- Le modèle **Workspace = Espace documentaire + Conversation** (provider-agnostic)
- La résolution **conversation/utilisateur → workspace** avec graceful degradation
- L'approche **.colaig-config.yaml** pour la configuration sans code
- Le pipeline RAG **Storage → Chunking → Embedding → FAISS → Albert**
- La philosophie **Zero Database** : pas de DB dans Colaig, persistence via StorageProtocol
- La philosophie **Provider-Agnostic** : StorageProtocol + MessagingProtocol abstraits
- L'identité **"collègue virtuel"** avec permissions natives du provider
- L'intégration possible avec **Bigfolder** comme passerelle multi-provider

---

# PARTIE 11 : PLAN D'ACTION POUR REPARTIR DE ZÉRO

## Sprint 0 (1 semaine) : Fondations

- [ ] Initialiser le repo Python avec la structure monolithe
- [ ] Configurer Docker + docker-compose
- [ ] Implémenter le client Matrix minimal (connexion + écoute + réponse)
- [ ] Implémenter le client WebDAV minimal (liste fichiers + téléchargement)
- [ ] Test end-to-end : envoyer un message Tchap → recevoir une réponse echo

## Sprint 1 (2 semaines) : RAG Basique

- [ ] Indexeur de documents (PDF via Albert OCR, DOCX, TXT → texte)
- [ ] Chunker simple (par paragraphes/sections, adapté au type)
- [ ] Embeddings via Albert API /embeddings
- [ ] FAISS : création index, ajout vecteurs, recherche, sérialisation/désérialisation
- [ ] Persistance index sur WebDAV (upload/download .faiss + .pkl)
- [ ] Générateur : construction prompt + appel Albert API
- [ ] Test end-to-end : question → recherche FAISS → réponse sourcée

## Sprint 2 (2 semaines) : Context Resolver

- [ ] Modèle Workspace (config, mapping salon→dossier)
- [ ] Résolution context : salon → workspace → documents
- [ ] Modes : Assistant / Chatbot / Personnel
- [ ] Graceful degradation pour tous les cas limites
- [ ] Historique conversations (fichiers JSON sur WebDAV dans /.colaig/conversations/)

## Sprint 3 (2 semaines) : Production-ready

- [ ] Interface admin web (FastAPI + HTMX)
- [ ] Monitoring et health checks
- [ ] Ré-indexation automatique (détection modifications)
- [ ] Documentation déploiement
- [ ] Pilote avec 2-3 workspaces réels

---

*Document généré le 17 février 2026 — Synthèse de ~15 conversations Claude couvrant février 2025 à février 2026.*
