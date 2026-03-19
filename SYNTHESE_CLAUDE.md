# Synthese Claude - Projet Colaig

## Qu'est-ce que Colaig ?

**Colaig** (anciennement "Albert-Tchap") est un **bot conversationnel pour Tchap** (la messagerie instantanee de l'administration francaise, basee sur le protocole Matrix).

Il utilise **Albert** (le LLM souverain de l'administration, base sur Llama) pour repondre aux questions des agents publics, avec un systeme de **RAG** (Retrieval Augmented Generation) qui s'appuie sur des documents stockes en **WebDAV**.

### Stack technique
- **Langage** : Python 3.10+
- **Protocole** : Matrix (via `matrix-nio`)
- **LLM** : Albert API (Llama 3.1 8B Instruct)
- **Embeddings** : BAAI/bge-m3 (dimension 1024)
- **Index vectoriel** : FAISS
- **Stockage documents** : WebDAV (Nextcloud/mDrive)
- **Extraction web** : Playwright + browser-use
- **Config** : pydantic-settings + `.env`
- **Deploiement** : Docker

### Architecture principale
```
app/
  bot.py              # TchapBot - point d'entree principal
  config.py           # Config (pydantic-settings) + EnvConfig (dataclass) - DOUBLON
  core_llm.py         # Logique LLM/Albert
  llm.py              # Couche LLM complementaire
  bot_msg.py          # Gestion des messages
  tchap_utils.py      # Utilitaires Tchap
  iam.py              # Gestion identite/autorisations
  commands/            # Systeme de commandes (registry, decorators, web, document...)
  services/            # Services metier (WebDAV, embeddings, comportements, webhooks...)
  actions/             # Actions RAG (standard, synthese)
  handlers/            # Handlers web
  matrix_bot/          # Lib wrapper matrix-nio (fork simplematrixbotlib)
  index/               # Types d'index
```

### Fonctionnalites cles
1. **Espaces documentaires isoles** : chaque salon Tchap peut etre lie a un espace avec ses propres documents, index et comportements
2. **Commandes web** : `!recherche_web`, `!ajouter_lien`, `!explorer_lien`, etc.
3. **Systeme de comportement** : actions, tools, prompts, rules personnalisables par espace (JSON)
4. **Webhooks** : systeme de notifications entrantes/sortantes
5. **Gestion de contextes** : hierarchie global > espace > salon > utilisateur > session
6. **Extraction web dynamique** : Playwright pour le contenu JavaScript

---

## Travail demande

La branche assignee est `claude/refactor-colaig-tchap-bot-qRqkF`, ce qui indique un **refactoring** du bot Colaig/Tchap.

**Aucune issue GitHub ouverte** n'a ete trouvee pour preciser le perimetre exact.

Le commit unique sur `main` est un mega-commit contenant tout le projet, ce qui confirme que le code n'a pas encore ete structure/refactore.

---

## Problemes identifies dans le code actuel

### 1. Double systeme de configuration
`config.py` contient **deux classes de config** qui font la meme chose :
- `Config(BaseSettings)` avec pydantic-settings (lignes 32-168)
- `EnvConfig` dataclass (lignes 257-299)
- Plus un singleton `env_config` instancie manuellement avec `os.getenv()` partout (lignes 175-224), ce qui **annule l'interet de pydantic-settings**

### 2. Fichier bot.py monolithique
`bot.py` (580+ lignes) melange initialisation, chargement de commandes, callbacks, maintenance, et logique metier.

### 3. Beaucoup de documentation, peu de tests
- Nombreux fichiers `.md` a la racine (12+)
- Seulement 2 fichiers de test (`test_detection_only.py`, `test_dynamic_extraction.py`)

### 4. Structure de packages non standard
- Le package s'appelle `albert-tchap` dans `pyproject.toml` mais le code est dans `app/`
- `setuptools.packages = ["app"]` avec `package-dir = {""="."}` est fragile

### 5. Securite
- `eval()` utilise dans `config.py:181` pour parser `USER_ALLOWED_DOMAINS` - **injection de code possible**

### 6. Fichier .whl commite
- `browser_use-0.1.41-py3-none-any.whl` est directement dans le repo (112 Ko)

---

## Questions ouvertes

### Perimetre du refactoring
1. **Quel est le scope exact du refactoring demande ?** La branche dit "refactor" mais sans issue, le perimetre est flou. Options possibles :
   - Refactoring complet de l'architecture
   - Nettoyage du systeme de configuration (fusionner Config/EnvConfig)
   - Decoupage de `bot.py` en modules
   - Reorganisation des fichiers `.md`
   - Ajout de tests

2. **Faut-il renommer le projet ?** Le `pyproject.toml` dit "albert-tchap", le README dit "Colaig", le code utilise les deux noms.

3. **Faut-il garder la compatibilite avec l'upstream albert-tchap ?** Le projet est un fork de `tchap_bot` (PEREN) enrichi. Un refactoring profond casserait ce lien.

### Questions techniques
4. **Config unifiee** : Peut-on supprimer `EnvConfig` et ne garder que `Config(BaseSettings)` ? Certains modules utilisent l'un, d'autres l'autre.

5. **Le fichier .whl** : Peut-on le retirer et utiliser une dependance pip classique pour `browser-use` ?

6. **Tests** : Y a-t-il un environnement de test (mock Albert API, mock Matrix) ou faut-il le creer ?

7. **WebDAV** : Le systeme WebDAV semble etre un ajout specifique a Colaig (pas dans l'upstream). Est-ce la partie la plus critique a preserver ?

### Questions fonctionnelles
8. **Quels sont les salons/espaces actuellement utilises ?** Pour comprendre les cas d'usage reels.

9. **Le systeme de comportement (behavior)** est-il utilise en production ou est-ce encore experimental ?

10. **Les webhooks** sont-ils actifs ? Quels systemes les declenchent ?

---

## Prochaines etapes possibles (en attente de validation)

| Priorite | Action | Effort |
|----------|--------|--------|
| P0 | Corriger la faille `eval()` dans config.py | Petit |
| P1 | Fusionner Config/EnvConfig en un seul systeme | Moyen |
| P1 | Decouper bot.py en modules coherents | Moyen |
| P2 | Renommer le projet de facon coherente | Petit |
| P2 | Ajouter des tests unitaires | Grand |
| P3 | Nettoyer les .md redondants a la racine | Petit |
| P3 | Retirer le .whl et utiliser pip | Petit |
