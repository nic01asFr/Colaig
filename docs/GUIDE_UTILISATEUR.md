# Guide d'utilisation de Colaig

## Qu'est-ce que Colaig ?

Colaig est un assistant IA conversationnel decentralise. Il s'integre dans vos
outils de communication (Tchap, Telegram, web chat...) et accede a vos documents
sur l'espace de stockage configure (Nextcloud/Bnum, OneDrive, S3, filesystem...).

Colaig n'est pas un simple chatbot : c'est un **collegue virtuel** qui connait vos
documents et repond en s'appuyant sur eux, avec citation des sources. Par defaut
il utilise l'**Albert API** (Etalab/DINUM) — souverain, sans cloud non-souverain.

## Concepts cles

### Une instance, plusieurs workspaces

Un meme Colaig (une instance) sert plusieurs salons et utilisateurs. Ce qui
change son comportement, c'est le **workspace** (espace de travail) auquel un
salon est lie.

```
1 INSTANCE
   |-- workspace "RH"             <- salons A, B
   |-- workspace "Urbanisme"      <- salon C
   |-- workspace "personal-alice" <- DM Alice
```

### Workspace

Un workspace est un espace de travail associe a un ou plusieurs salons. Il contient :

- des **documents** (PDF, DOCX, ODT, Markdown, TXT, HTML) indexes automatiquement ;
- un fichier de configuration `.colaig/config.yaml` qui definit le comportement
  (prompt systeme, ton, langue, outils, ACL) ;
- un **index vectoriel FAISS** construit depuis les documents (`.colaig/indexes/`).

Quand vous posez une question dans un salon lie a un workspace, Colaig recherche
dans les documents de ce workspace pour formuler sa reponse.

### Les 3 modes

| Situation | Mode | Comportement |
|---|---|---|
| Salon lie a un workspace | **Assistant** | Repond depuis les documents du workspace |
| Message direct (DM) | **Personnel** | Espace prive par utilisateur, avec memoire |
| Salon non lie | **Chatbot** | Generique + propose de creer/lier un workspace |

### RAG (Retrieval-Augmented Generation)

1. Votre question est transformee en vecteur (embeddings `BAAI/bge-m3`).
2. Les passages de documents les plus pertinents sont retrouves (recherche dense
   + BM25 + fusion RRF, puis reranking).
3. Ces extraits sont passes en contexte au LLM (`openai/gpt-oss-120b` via Albert).
4. Le LLM genere une reponse **sourcee**, ancree dans vos documents reels.

## Lier un salon a un workspace (onboarding)

Dans un salon non encore configure, plusieurs options :

1. **Commande directe** dans le salon :
   ```
   colaig creer <nom de l'espace>
   ```
   Exemple : `colaig creer Equipe RH` — cree un espace et lie le salon.
   Ou : `colaig lier <workspace_id>` pour rattacher a un workspace existant.

2. **Dashboard admin** (`/`) : creation/liaison de workspaces, re-indexation.

3. **API admin** : `POST /workspaces` avec `storage_path`, `name`, `conversations`.

4. **Manuellement** : deposer un `.colaig/config.yaml` dans le dossier de stockage
   avec `conversations: ["<conversation_id>"]`.

## Comment interagir

- **En message direct (DM)** : ecrivez votre question, Colaig repond.
- **Dans un salon de groupe** : mentionnez Colaig (`@colaig ...`) ou son nom.
- **Web chat** : ouvrez `/chat` dans le navigateur.

### Exemples de questions

- "Quels sont les documents disponibles sur ce sujet ?"
- "Resume-moi le guide de procedure."
- "Quelle est la reglementation applicable pour... ?"
- "Quel est le montant du forfait de remboursement ?"

## Formats de documents supportes

| Format | Extension | Notes |
|--------|-----------|-------|
| PDF | `.pdf` | Extraction texte (+ OCR si scanne) |
| Word | `.docx` | Paragraphes complets |
| LibreOffice | `.odt` | OpenDocument natif |
| Markdown | `.md` | Decoupe par sections |
| Texte | `.txt` | UTF-8 / latin-1 |
| HTML | `.html` | Scripts et styles supprimes |

Les fichiers images, videos et archives ne sont pas traites comme documents.

## Pour les administrateurs / operateurs

- **Configuration** : voir `config/.env.example` (choix des backends + credentials).
- **Multi-tenant** : `config/clients.yml` declare N instances clientes ;
  provisioning via `POST /api/platform/provision` (voir [docs/ARCHITECTURE.md](ARCHITECTURE.md)).
- **Securite** : definir `COLAIG_PLATFORM_API_KEY` en production (protege le
  dashboard et les routes plateforme).

## Administration en conversation (DM admin)

Si votre `user_id` figure dans `COLAIG_ADMIN_USER_IDS` (ou que vous êtes owner d'un
workspace), Colaig peut **administrer en conversation** depuis un message direct :

- « Crée un espace RH au chemin /espace-rh/, ton formel » → crée le workspace
  (vous en devenez owner).
- « Lie ce salon au workspace espace-rh » → rattache une conversation.
- « Définis le prompt de espace-rh : tu es un assistant RH… » → configure l'agent.
- « Liste mes espaces » → workspaces que vous pouvez administrer.

La gestion des owners (`manage_workspace_owners`) est réservée aux **admins globaux**.
Dans un salon métier (mode Assistant) ou pour un utilisateur final, ces capacités
sont masquées. Détails : [REFLEXIF_ET_OPS.md](REFLEXIF_ET_OPS.md).
