# Avancement

**Ce fichier est le mécanisme de reprise entre sessions.** Une session qui démarre le lit
et sait où reprendre sans relire le dépôt. Chaque lot terminé y inscrit : ID, date,
critère de fin atteint, commit, points ouverts.

**Format d'une entrée :**

```
## Lxx.y — Titre · JJ/MM/AAAA · TERMINÉ|EN COURS|BLOQUÉ
Critère de fin : <énoncé> → <atteint / pourquoi pas>
Commit : <sha court>
Ouvert : <ce qui reste, ou "rien">
```

---

## État global

| | |
|---|---|
| **Phase en cours** | 0 — Socle |
| **Branche** | `chantier/tronc-unique` (compte Onyxia retenu : **`nicolaslaval`**) |
| **Lot en cours** | L1.5 — palier génération mesuré. **L'assistant n'est pas déployable en l'état** : refus non fiable. |
| **Bloqué par** | H4/H5 (accès `colaig-0`), H3ter (corpus représentatif pour le listing récursif) |
| **Arbitrages en attente** | reranker absent de SSPCloud (voir HYPOTHESES) |
| **Dernière mise à jour** | 22/08/2026 |

---

## Journal

## Cadrage — 22/08/2026 · TERMINÉ

Analyse complète des 16 générations antérieures de Colaig (POC fév. 2025 → v3 juin 2026),
comparaison fonction par fonction, choix du tronc, plan en 41 lots.

Livrables :
- `colaig-etat-des-lieux-fonctionnel.md` — 17 domaines comparés, optimum désigné par fonction
- `colaig-recherche-cible-methode.md` — SOTA vérifié, structure cible, méthode
- `colaig-plan-construction-agentique.md` — fiches détaillées des 41 lots
- `CLAUDE.md`, `_chantier/{DECISIONS,PLAN,HYPOTHESES,CONVENTIONS}.md` — ce dépôt

Décisions actées : D1 (tronc = v3), D2 (PROD gelée), D3 (LLM = SSPCloud),
D4 (portée interministérielle + auto-hébergeable), D5 (dépôt unique), D7 (pod séparé).
D6 reportée à L5.1.

**Ouvert :** H1, H2, H3, H4, H5 non levées. H6 partielle.

---

## Sonde environnement — 22/08/2026 · TERMINÉ

Pod `proj-colaig-refonte-jupyter-python-0` (namespace `user-nic01asfr`) :
Python 3.13.13, git 2.55.0, node absent, 9,8 Go libres, sortie réseau OK
(github 200, pypi 200, llm.lab.sspcloud.fr 200).

**Trois blocages identifiés :**
1. `GET /api/models` → **401** : la clé LLM n'est pas dans l'environnement du pod.
2. `kubectl get secrets` → **403** : le service account ne peut pas lire
   `*-secretassistant`. L'auto-découverte de `platform/sspcloud.py` suppose le rôle `edit`
   du chart Helm — à reproduire au lot L3.6.
3. Aucun token GitHub, aucune clé SSH.

**Ouvert :** fournir clé LLM, token GitHub, credentials WebDAV et Matrix de test.

---

## Sonde LLM — 22/08/2026 · TERMINÉ · **H1 et H2 levées (sur Albert)**

Exécutée depuis le poste local, avec les credentials trouvés dans `colaig-v3/.env`
(sur instruction explicite). Aucune valeur de clé n'apparaît ni dans le transcript ni
dans les fichiers produits. Résultat complet : `_chantier/mesures/llm-capabilities-albert.md`.

**⚠️ Ce paragraphe a été corrigé le 22/08/2026 — il était faux.** Il annonçait que
`ALBERT_API_URL` devait recevoir `/v1`. C'est l'inverse : les clients ajoutent `/v1`
eux-mêmes, la base doit rester sans. Le défaut de `config.py` est correct, et le
« corriger » aurait produit `/v1/v1/`. Voir la correction détaillée dans `HYPOTHESES.md`.

Catalogue Albert servi (10 modèles) : `openai/gpt-oss-120b`,
`qwen3-coder-30b-A3b-instruct`, `ministral-3-8b-instruct-2512`,
`mistral-small-3-2-24b-instruct-2506`, `deepseek-v4-flash`, `bge-m3`,
`bge-reranker-v2-m3`, `qwen3-vl-embedding-8b`, `whisper-large-v3`, `lightonocr-2-1b`.

| question | réponse mesurée |
|---|---|
| Chat | ✅ 200 en 0,20 s sur `openai/gpt-oss-120b` |
| **Tool calling** | ✅ **`tool_calls` présent et bien formé** (0,41 s) |
| Embeddings | ✅ `qwen3-vl-embedding-8b`, **dimension 4096** (0,19 s) |
| Reranker | ✅ `bge-reranker-v2-m3` opérationnel via `POST /rerank` (0,12 s) |

**Conséquence :** la boucle agent native est réalisable, les lots de la phase 4 gardent
leur forme. Le repli « JSON imposé par prompt » est écarté.

**Réserve explicite — H1 n'est PAS levée pour SSPCloud.** Ce qui est mesuré ici, c'est
Albert. D3 désigne SSPCloud comme cible de production : il faut sa propre clé pour
sonder `llm.lab.sspcloud.fr`, et rien ne permet de supposer que son catalogue est le
même. Tant que ce n'est pas fait, `qwen3-6-35b-moe` reste **INCONNU**.

**Ouvert :** clé LLM SSPCloud ; dimension d'embedding 4096 à confronter au coût
mémoire de l'index (H4).

---

## L0.1 — préparation · 22/08/2026 · EN COURS

`scripts/bootstrap.ps1` relu et corrigé avant exécution (trois défauts réels) :
1. le dossier de sauvegarde horodaté était recopié **dans** `_chantier`, y imbriquant une
   copie à chaque relance — contredisait l'idempotence annoncée ;
2. aucune vérification de `$LASTEXITCODE` : PowerShell n'applique pas
   `$ErrorActionPreference` aux commandes natives, un `git checkout` en échec passait
   inaperçu (le `2>&1 | Out-Null` masquait même le message) ;
3. `git remote add v3` échouait à la relance si le remote existait déjà.

Fichier normalisé en UTF-8 BOM + CRLF : en LF sans BOM, PowerShell 5.1 ne parsait pas le
here-string du `.gitignore`. `ParseFile` retourne désormais PARSE OK.

Le dépôt source `colaig-v3` est présent, branche `feat/reflexive-self-config` disponible.

**Ouvert :** exécution du bootstrap, `pytest -q`, premier commit.

---

## Sonde SSPCloud et GitHub — 22/08/2026 · TERMINÉ · **H1b et H6 levées**

Credentials fournis dans le `.env` du chantier (`sspcloud_api_key`, `gh_pat_token`).
Aucune valeur n'apparaît dans les fichiers produits ni dans les échanges.

**H1b levée — `qwen3-6-35b-moe` supporte le tool calling** (200 en 1,19 s, `tool_calls`
bien formé). La boucle agent native tient sur la cible de production ; les lots de la
phase 4 gardent leur forme. Embeddings : `qwen3-embedding-8b`, dimension 4096.

**H2 dégradée en arbitrage :** SSPCloud ne sert **aucun reranker** (7 modèles au
catalogue). Albert en sert un. Trois options chiffrées dans `HYPOTHESES.md` — bi-provider,
MMR seul, ou reranker local. **Aucune n'est retenue par défaut, c'est une décision.**

**H6 levée côté dépôt :** PAT fine-grained valide, `push: true` sur `nic01asFr/Colaig`.
Deux faits à ne pas subir : la branche par défaut est **`Colaig_main`**, pas `main` ; et
le dépôt est **public**. Vérification faite avant tout push : les 16 commits de v3 ne
contiennent aucun secret. Mais pousser dans un dépôt public **est** une publication —
porte humaine du point 8, à franchir avant le premier push.

**Ouvert :** arbitrage reranker ; nom de la branche poussée ; feu vert publication.

---

## L0.1 — Import du tronc et assainissement · 22/08/2026 · **TERMINÉ (local)**

Critère de fin : le dépôt contient le tronc v3 et la suite de tests s'exécute → **atteint**.
Commit : `4edd422` sur la branche `chantier/tronc-unique`.

- 16 commits de v3 (`feat/reflexive-self-config`) importés **avec leur historique**.
- `CLAUDE.md` de v3 archivé en `docs/CLAUDE.v3-original.md`.
- `.gitignore` durci ; `.env` du chantier préservé et ignoré (vérifié).
- **Rien n'a été détruit** : la quarantaine est vide, aucune des cinq scories visées
  n'existait dans le dossier cible (non suivies par git, donc jamais importées).

**Suite de tests : 1574 passent, 42 échouent.**

| échec | nature |
|---|---|
| 41 dans `tests/test_live.py` | attendus — exigent une instance en fonctionnement (erreurs httpx de connexion) |
| 1 dans `tests/test_executor.py` | **test instable** : passe isolé (8/8), échoue sous charge de la suite complète. Avertissement `coroutine ... was never awaited`. À traiter au harnais de test (L0.3), pas à ignorer. |

**Le dépôt public contient déjà une génération antérieure de Colaig** (`app/`,
`browser_use-0.1.41-py3-none-any.whl`, une vingtaine de `.md` en vrac), sous
**Licence Ouverte / Open Licence 2.0** — la même que celle de v3. Branches distantes :
`Colaig_main` (défaut), `dev`, `test-behavior-implementation`, deux `claude/*`.
La question de la licence est donc de fait tranchée, et pousser le tronc n'est pas une
première publication.

**Ouvert :** le push reste à faire (voir « Prochaine action »).

---

## D8 — Stockage du chantier : S3 SSPCloud remplace WebDAV · 22/08/2026 · ACTÉ

Sur instruction. Consigné en D8 dans `DECISIONS.md`, avec D7bis (le chantier passe du
namespace `user-nic01asfr` à **`user-nicolaslaval`**, seul namespace que l'outillage
peut piloter).

Effets :
- **H3 reformulée** — porte sur la latence de MinIO, plus sur celle du WebDAV Bnum.
- **`scripts/probe_s3.py` écrit** : mesure LIST non récursif / LIST récursif / GET,
  aller-retour PUT-GET-DELETE, marqueurs `.albert`/`.colaig`, volumétrie, comptage des
  conversations (H4), nature des credentials. Reprend les variables `AWS_*` d'Onyxia
  quand les `COLAIG_S3_*` sont absentes, donc utilisable tel quel dans un pod.
- **`probe_webdav.py` conservé**, marqué déprécié pour H3 : `webdav.py` reste une
  implémentation du tronc, testée au titre de L1.1.
- **H3bis créée** — durabilité des credentials, bloquante pour l'exploitation.
- Critère de fin de L1.1 mis à jour dans `PLAN.md` : vert sur `local` + `s3`.

Le chantier n'attend plus de credentials WebDAV : un blocage sur cinq est levé.

**Ouvert :** nom du bucket et credentials S3 à utiliser ; réponse du datalab sur
l'existence de credentials non expirantes.

---

## L0.1 — poussé · 22/08/2026 · TERMINÉ

`git push -u origin chantier/tronc-unique` — commits `4edd422` et `e7dbfce`.

**Vérification après push : rien n'a été écrasé.** Six branches distantes, les cinq
d'origine intactes (`Colaig_main` toujours sur `ac35483`, `dev`,
`test-behavior-implementation`, deux `claude/*`) plus `chantier/tronc-unique`. Push en
ajout, sans `--force`.

À noter : D5 prévoyait d'archiver `Colaig_main` et une branche `claude/*` après import.
**Non fait, et pas à faire sans arbitrage** — consigne de ne pas supprimer le travail
existant. Point de vigilance consigné en fin de `DECISIONS.md`.

---

## Recherche et test du stockage S3 — 22/08/2026 · **sonde validée, credentials introuvables**

### Pod de développement créé

`proj-colaig-dev-jupyter-python-0`, namespace `user-nicolaslaval`, cloné sur
`chantier/tronc-unique` (commit `8641439`). Python 3.13.13, git 2.55.0, 9,8 Go libres.

Joignabilité depuis le pod : `minio.lab.sspcloud.fr` **403** (atteignable, refus anonyme
attendu), `llm.lab.sspcloud.fr` 401 (pas de clé dans l'environnement), github 200,
pypi 200.

### Le fait qui bloque — aucune credential S3 n'est récupérable par l'outillage

Quatre pods examinés, aucun ne fournit de credentials utilisables :

| pod | âge | variables `AWS_*` | test |
|---|---|---|---|
| `jupyter-python-271780-0` | 8 h 38 | présentes (STS) | ❌ `InvalidAccessKeyId` |
| `dev-805f11a5-jupyter-python-0` | 24 j | **absentes** | — |
| `jupyter-python-557343-0` | — | — | exec en timeout |
| `proj-colaig-dev-jupyter-python-0` | **48 s** | **absentes** | — |

**Le pod fraîchement lancé n'a aucune variable `AWS_*`.** C'est le résultat décisif : les
pods créés par l'outillage MCP le sont sans passer par Onyxia, donc **sans injection de
credentials S3**. Relancer un pod ne régénère pas de jetons.

Les seuls pods qui en possèdent sont ceux lancés depuis l'interface Onyxia — et ceux
trouvés avaient des jetons déjà expirés (`mc ls s3` → `InvalidAccessKeyId`, alias `mc`
pourtant bien configuré sur `https://minio.lab.sspcloud.fr`).

**Conclusion : les credentials S3 ne peuvent venir que d'Onyxia → Mon compte →
Connexion au stockage.** Aucune valeur n'est supposée d'ici là.

### `probe_s3.py` validée de bout en bout

Faute de S3 réel, la sonde a été exercée contre un S3 simulé (`moto`) semé d'un jeu
représentatif : deux espaces, l'un migré en `.colaig`, l'autre encore en `.albert`,
5 conversations, 8 documents.

```
| LIST non récursif racine | 10 ms | 2 | 200 |
| LIST non récursif espace | 11 ms | 2 | 200 |
| LIST récursif espace     | 18 ms | 8 | 200 |
| GET d'un objet           | 26 ms | 1 | 200 |
PUT 12 ms · GET 14 ms (contenu identique) · DELETE 9 ms

| espace-a/ | —  | ✅ | 3 |
| espace-b/ | ✅ | —  | 2 |
```

Tout se comporte comme prévu : découverte des espaces, distinction `.albert`/`.colaig`,
comptage des conversations (H4), volumétrie (H5), latences et aller-retour en écriture.
**La sonde tournera du premier coup dès que les credentials arriveront** — les chiffres
ci-dessus sont ceux d'un simulateur local et n'ont évidemment aucune valeur de mesure.

**Ouvert :** endpoint, bucket, access key, secret key et éventuel session token, à
récupérer dans Onyxia. Et la réponse du datalab sur H3bis (credentials non expirantes).

---

## Credentials S3 — Vault exploré, réponse trouvée dans la documentation · 22/08/2026 · **H3bis levée**

### Vault ne contient pas de credentials S3

Jeton valide **32 jours**, renouvelable, portée `onyxia-kv/…/nicolaslaval/**`. Dix-sept
secrets, tous des préférences d'interface — et surtout : `s3Profiles` est une **liste
vide**, `s3BookmarksStr` vaut `null`, `restorableServiceConfigs` est vide, aucune
occurrence de `accessKey` / `secretKey` / `sessionToken` nulle part.

Onyxia ne stocke pas de credentials S3 : il les **frappe à la demande** en échangeant le
jeton OIDC contre du STS MinIO. D'où l'absence de cache, et l'impossibilité pour tout
outillage sans OIDC d'en obtenir. La piste automatisée est définitivement close.

### La documentation tranche — deux mécanismes, pas un

| | jeton personnel | **compte de service** |
|---|---|---|
| Durée | **7 jours**, régénéré automatiquement | **permanent** |
| Rattaché à | une personne | **un projet** |
| Obtention | `datalab.sspcloud.fr/account/storage` (scripts R/Python/terminal fournis) | console `minio-console.lab.sspcloud.fr` |
| Usage prévu | travail interactif | « traitements périodiques ou **déploiement d'applications** » |

**Ceci explique la mesure précédente** : `InvalidAccessKeyId` sur un pod de 8 h 38 parce
que le jeton hérité avait été régénéré entre-temps. Ce n'est pas une anomalie, c'est le
fonctionnement nominal — les services créés avant une régénération perdent l'accès et
apparaissent en rouge dans « Mes services ».

**H3bis est levée.** L'inquiétude soulevée en D8 est réelle mais évitable : il suffit de
ne jamais déployer sur un jeton personnel. Contrainte à inscrire dans L3.6 — le chart
Helm consomme un compte de service, jamais un jeton de session.

### Innocuité de la sonde renforcée

Le bucket cible est `nicolaslaval` et il contient `qgis-workspace/`, c'est-à-dire du
travail réel. `probe_s3.py` a donc été durcie : écriture cantonnée à
`<PREFIX>.colaig-probe/` avec suppression de la seule clé écrite, `COLAIG_S3_PREFIX` pour
cantonner toute la sonde, `COLAIG_S3_ALLOW_WRITE=0` pour la désactiver, et listing
récursif borné par `COLAIG_S3_MAX_OBJETS` avec **troncature écrite dans le rapport** —
un plafond silencieux se lirait comme une mesure complète.

**Ouvert :** récupérer le jeton personnel pour lever H3 ; créer le compte de service
avant tout déploiement.

---

## L0.2 — `paths.py` source unique · 22/08/2026 · **TERMINÉ**

Critère de fin → **atteint**, mais **reformulé** : celui de `PLAN.md` était inapplicable.

Branche `lot/L0.2-paths`. Deux commits : le contrat d'abord, le portage ensuite.

### Le critère d'origine ne pouvait pas être satisfait

`grep -rn '\.colaig\|\.albert' colaig/ | grep -v paths.py` → **vide** ne peut jamais
l'être : `from colaig.rag.colaig_index import` contient la sous-chaîne `.colaig`, et les
docstrings mentionnent légitimement le dossier. Sur **206** lignes remontées, **70**
étaient de vraies constructions de chemin.

Critère reformulé, exécutable, dans `tests/test_paths_source_unique.py` : aucun littéral
de chaîne, hors docstring, **sans espace**, contenant `.colaig` ou `.albert`, hors
`paths.py`. L'AST écarte commentaires et noms de modules ; l'exclusion des chaînes à
espace écarte la prose (message d'accueil, description d'outil MCP, gabarit
`docker-compose`) qui mentionne le dossier sans le construire.

### Ce que le lot a corrigé au passage

Deux conventions concurrentes coexistaient : certains appelants faisaient `rstrip('/')`
sur la base, d'autres non. Un espace déclaré `/equipe-rh/` produisait donc tantôt
`/equipe-rh/.colaig/tasks/`, tantôt `/equipe-rh//.colaig/tasks/` — deux objets distincts
selon le backend. `paths.py` normalise une fois pour toutes.

### Un bug introduit puis corrigé, qui vaut d'être retenu

Le portage a **introduit** ce même bug à trois endroits (`behavior_indexer`,
`skill_indexer`, `pre_execution`) : les fonctions `*_dir()` renvoient un slash final,
alors que le code d'origine construisait ces dossiers sans, et le code appelant écrivait
`f"{indexes_dir}/{nom}"` → `.../indexes//behaviors.faiss`.

**Les 1574 tests de la suite n'ont rien vu.** Aucun ne vérifie la forme des chemins de
persistance. Détecté par relecture manuelle des sites où le slash changeait, puis par un
audit statique. Un test de non-régression a été ajouté pour cette classe de bug.

C'est le mode de défaillance le plus coûteux : l'index s'écrit à un endroit et se relit
à un autre, sans erreur visible.

### Sécurité préservée explicitement

`path_validator.py` bloquait par sous-chaîne `"/.colaig" in path`, ce qui attrape aussi
`.colaig-ignore`. Y substituer un test par segment aurait **desserré** un contrôle de
sécurité. D'où deux prédicats distincts et documentés : `is_instance_path()` (égalité
stricte, pour les filtres d'indexation) et `is_reserved_path()` (préfixe, pour la
sécurité).

`protocols.py` n'a **pas** été touché : ses occurrences étaient toutes des docstrings.

### Résultat

- 64 remplacements, 21 fichiers portés, `colaig/paths.py` créé (23 fonctions).
- **1584 tests passent**, zéro échec (1574 d'origine + 10 nouveaux).
- Suite complète hors `test_live` en **20 s** — le critère de L0.4 (« < 60 s hors
  ligne ») est déjà tenu avant même d'avoir commencé ce lot.
- `colaig/CLAUDE.md` créé (contrat de `paths.py`), `rag/CLAUDE.md` mis à jour :
  `ColaigIndex` garde les *clés* et délègue les *chemins*, API publique inchangée.

**Ouvert :** rien. `legacy_albert_path()` est fournie mais sans appelant — c'est
attendu, elle est la brique de L1.7.

---

## L0.3 — Doctrine corrigée · 22/08/2026 · **TERMINÉ — revue humaine requise**

Critère de fin : « zéro contradiction code/doc, **revue humaine** ». La première partie
est atteinte et testée ; **la seconde t'appartient**, elle n'est pas franchie ici.

Branche `lot/L0.3-doctrine`. Le lot devait corriger un texte. Il a trouvé **deux bugs**.

### Bug 1 — un hôte qui ne résout pas, dans le code

`albert-api.etalab.gouv.fr` (avec un **tiret**) ne résout pas — vérifié deux fois. L'hôte
réel est `albert.api.etalab.gouv.fr` (avec un **point**).

Il figurait à **20 endroits**, dont le défaut de `provider_registry.py` — le module même
que la doctrine désigne comme point d'entrée multi-provider — ainsi que `models.py`,
`provisioner.py`, `web/routes.py` et trois endroits de `mcp/server.py`. Un déploiement
sélectionnant `albert` sans surcharger l'URL échouait donc en résolution DNS.

Fait notable : `config.py` portait déjà le **bon** hôte. Les deux coexistaient dans le
même dépôt.

### Bug 2 — la commande de déploiement documentée échoue

`deploy/helm/colaig/README.md` et `docs/EXPLOITATION.md` recommandaient
`--set llm.apiUrl=https://llm.lab.sspcloud.fr/openai`.

Les clients construisent eux-mêmes `{base}/v1/chat/completions`. Mesuré :

```
base=.../openai -> 403  {"detail":"Direct API passthrough is disabled..."}
base=.../api    -> 200  OK
```

**Un déploiement suivant cette documentation démarre puis échoue au premier appel LLM.**
Corrigé, avec la mesure inscrite dans le README pour qu'on n'y revienne pas. `/api` sert
en outre un modèle de plus que `/openai` (7 contre 6, `qwen3-cursor` en moins).

### Une erreur de ma part, corrigée

J'avais inscrit dans `HYPOTHESES.md` et `AVANCEMENT.md` qu'`ALBERT_API_URL` « omet `/v1`,
ce qui renvoie 404 » et qu'il fallait l'ajouter. **C'était faux, et le suivre aurait
cassé la configuration** en produisant `/v1/v1/`. Les clients ajoutent `/v1` eux-mêmes ;
c'était ma sonde qui appelait `/models` sans préfixe. Les deux fichiers sont corrigés.

### Doctrine proprement dite

- `docs/ARCHITECTURE.md` §9.2 disait « **Albert API exclusivement** pour le LLM ».
  Remplacé : le LLM est choisi par l'exploitant, la souveraineté se fait respecter par
  `platform_policy.allowed_llm_endpoints`, pas en câblant un fournisseur dans le code.
- `CLAUDE.md` racine nommait un `LLMClientProtocol` **qui n'existe pas**. Le contrat réel
  s'appelle `AlbertClientProtocol` — alors qu'il est implémenté par `openai_client`,
  `azure_client` et `ollama_client` autant que par `albert.py`. Ce nom est le dernier
  résidu de la doctrine « Albert uniquement ». **Le renommer touche `protocols.py` :
  c'est un arbitrage humain, il n'est pas fait ici.**
- `docs/CLAUDE.v3-original.md` porte désormais une bannière « ARCHIVE — NE PAS SUIVRE »
  qui nomme ses deux points périmés.

### Verrouillage

`tests/test_doctrine_llm.py` — 4 tests **statiques et hors ligne** : aucune occurrence de
l'hôte mort, aucune URL de base suffixée par `/v1`, registre effectivement
multi-provider, cohérence entre le défaut de `config.py` et celui du registre.

**1588 tests passent**, zéro échec.

**Ouvert — porte humaine :** la revue de doctrine. Deux points appellent ton arbitrage :
le renommage d'`AlbertClientProtocol`, et la question de savoir si `platform_policy`
doit restreindre les endpoints par défaut.

---

## L0.3b — Renommage arbitré et durcissement de la policy · 23/08/2026 · **TERMINÉ**

Branche `lot/L0.3b-renommage-protocol`. Fait suite à la revue humaine de L0.3.

### Point 1 — renommage autorisé, effectué

`AlbertClientProtocol` → **`LLMClientProtocol`**. 34 occurrences, 22 fichiers, dont
`protocols.py` — modification couverte par l'arbitrage humain explicite exigé au §5 du
`CLAUDE.md`. Le nom décrit enfin ce que fait le contrat : il est implémenté par
`openai_client`, `azure_client` et `ollama_client` autant que par `albert.py`.
L'archive `CLAUDE.v3-original.md` n'a pas été touchée.

### Point 2 — arbitrage délégué : pas de restriction par défaut (D9)

Décision prise et motivée en **D9**. Une liste blanche par défaut casserait D4
(auto-hébergeable), reviendrait à décider de la souveraineté du déploiement d'autrui, et
donnerait une fausse sécurité puisque qui exécute le code peut l'éditer.

**Mais en examinant le mécanisme, j'ai trouvé une faille.** Le contrôle était
`url.startswith(autorise)` :

```
"https://llm.lab.sspcloud.fr.attaquant.example/v1"
    .startswith("https://llm.lab.sspcloud.fr")   ->  True
```

Un opérateur croyait restreindre son parc au datalab ; un suffixe de domaine suffisait à
envoyer les conversations ailleurs. C'est le **seul** levier de souveraineté du produit,
et il était contournable en une ligne.

Remplacé par `config.endpoint_autorise()` : schéma et autorité comparés à l'identique
(insensibles à la casse), chemin validé sur **frontière de segment** — `/api` autorise
`/api/v1`, pas `/apiv2`. `tests/test_policy_endpoints.py` : **17 tests**, couvrant le
suffixe de domaine, le sous-domaine, le schéma dégradé en HTTP, le port différent et le
débordement de chemin, plus une régression qui refuse le retour de `startswith`.

Contrepoids de D9 : `main.py` journalise désormais `LLM : backend=… endpoint=…` au
démarrage. Si Colaig ne décide pas où partent les conversations, l'exploitant doit le voir.

**1605 tests passent.**

**Ouvert :** rien.

---

## L0.4 — Harnais de test déterministe · 23/08/2026 · **TERMINÉ**

Critère de fin : « suite complète hors ligne < 60 s » → **atteint : 1626 tests en 21 s**,
et deux exécutions consécutives donnent le même résultat.

Branche `lot/L0.4-harnais`.

### Le harnais n'était pas déterministe

`MockStorage` calculait ses etags ainsi :

```python
etag = f'"{hash(content)}"'
```

`hash()` sur des `bytes` est **randomisé par processus**. Mesuré sur trois exécutions :

```
2598434101455927999 · -123023570338129182 · 8217233926374741472
```

Or l'indexation incrémentale de Colaig repose **entièrement** sur la comparaison
d'etags (`.colaig/indexes/etags.json`). Une doublure dont les etags bougent d'un run à
l'autre ne peut pas servir à éprouver ce mécanisme, et fabrique des tests intermittents
dont on finit par accuser la CI. L'etag est désormais le SHA-256 du contenu : stable
entre processus, identique pour un contenu identique — le comportement d'un vrai backend.

`last_modified` lisait aussi l'horloge (`datetime.utcnow()`). Remplacé par un instant
fixe et un compteur : l'ordre des écritures est reproductible.

### Il n'y avait aucune doublure de messagerie

Les tests utilisaient des `AsyncMock()` bruts, qui acceptent n'importe quel appel et ne
vérifient donc **rien** du contrat. `FakeMessaging` enregistre les envois et les
indicateurs de frappe, et `injecter()` déclenche le callback de `on_message` — ce qui
permet de piloter la réception sans réseau. Un `injecter()` sans callback préalable
échoue franchement plutôt que de ne rien faire en silence. `run()` rend la main : une
boucle infinie dans un test fait pendre la suite.

### Livrables

- `tests/fakes.py` — `FakeStorage`, `FakeMessaging`, `FakeLLM`, tous déterministes.
- `tests/conftest.py` — point d'entrée unique, réexporte les doublures sous leurs
  anciens noms (`MockStorage`, `MockAlbertClient`…). **Les 78 fichiers de tests
  existants fonctionnent sans modification** et héritent du déterminisme.
- `tests/test_harnais.py` — 13 tests : stabilité de l'etag **entre processus** (vérifiée
  en relançant un interpréteur, un `assert` local ne dirait rien), absence d'horloge,
  reproductibilité des embeddings, ordre de listing stable, conformité des trois
  doublures à leurs Protocols.
- `tests/CLAUDE.md` — contrat du harnais.

### Un réflexe qui a payé trois fois

Le test de conformité aux Protocols passait sur les trois doublures. Avant de le croire,
vérification qu'il **sait échouer** : `inspect.getmembers()` sur un `Protocol` aurait pu
ne rien retourner, auquel cas le test aurait été vert par vacuité. Il détecte bien les
8 méthodes manquantes d'une classe vide, et cette preuve est elle-même un test.

C'est la troisième fois dans la phase 0 qu'un contrôle est vert pour une mauvaise
raison — après le garde-fou anti-secret (recherche ligne à ligne, aveugle aux clés PEM
multilignes) et le portage de L0.2 (doubles slashes invisibles à 1574 tests).
**À retenir comme méthode : un garde-fou dont on n'a pas vu le rouge ne prouve rien.**

**Ouvert :** rien.

---

## Phase 0 — terminée · 23/08/2026

| lot | état | critère |
|---|---|---|
| L0.1 | ✅ | tronc v3 importé avec son historique, tests au même niveau |
| L0.2 | ✅ | `paths.py` source unique, vérifié par AST |
| L0.3 | ✅ | doctrine multi-provider, zéro contradiction code/doc |
| L0.3b | ✅ | renommage arbitré, `allowed_llm_endpoints` durci |
| L0.4 | ✅ | harnais déterministe, 1626 tests hors ligne en 21 s |

Cinq branches poussées, `Colaig_main` intacte. **Quatre bugs réels trouvés** en chemin :
hôte Albert non résolvant dans sept fichiers, commande de déploiement Helm renvoyant 403,
liste blanche d'endpoints contournable par suffixe de domaine, etags non déterministes.
Aucun n'était l'objet du lot qui l'a découvert.

---

## L1.1 — Contrat `StorageProtocol` · 23/08/2026 · **TERMINÉ**

Critère de fin : « vert sur `local` + `s3` ; autres `skipif` » → **atteint**.
**30 tests verts** sur `fake`, `local` et **`s3` contre le vrai MinIO SSPCloud**.

Branche `lot/L1.1-storage-contrat`. `tests/test_storage_contrat.py` : une seule suite de
10 tests, exécutée contre chaque implémentation par paramétrage.

### La doublure ne se comportait pas comme les vraies

`FakeStorage` levait le `FileNotFoundError` natif sur un fichier absent. **Les sept
implémentations réelles lèvent `StorageFileNotFoundError`** — et l'une n'hérite pas de
l'autre, `issubclass()` vaut `False`.

Conséquence : un `except StorageFileNotFoundError` pouvait passer les tests sans jamais
se déclencher, ou l'inverse. La trace du contournement est encore dans le code —
`agents/tasks.py` attrape **les deux** à deux endroits, « au cas où ». C'est le symptôme
qu'on écrit quand on ne sait plus laquelle arrive.

Doublure alignée sur les sept. Les 1626 tests existants passent sans modification :
rien ne dépendait de l'exception native.

### Le Protocol est sous-spécifié — le contrat l'écrit

`StorageProtocol` ne dit ni ce que lève `download()` sur un chemin absent, ni si
`delete()` d'un inexistant est une erreur, ni si `upload()` crée les parents. Les
docstrings tiennent en une ligne. Le contrat prend pour référence le **comportement
commun aux sept**, pas une préférence — et là où elles divergeraient, c'est un arbitrage
à remonter, pas à trancher dans un test.

Ce qui est désormais écrit et vérifié : aller-retour d'octets, cycle de vie de
`exists()`, exception sur absent, écrasement à l'`upload`, **sémantique de l'etag**
(absent → `None`, stable sans écriture, différent après modification), économie de
transfert de `download_if_changed`, listing récursif *vs* non récursif, dossier vide qui
rend une liste vide sans lever, et préservation du **binaire** — un index FAISS n'est pas
du texte.

### Couverture honnête

| backend | état |
|---|---|
| `fake`, `local` | ✅ vérifié |
| `s3` | ✅ vérifié **contre MinIO SSPCloud** |
| `webdav`, `bigfolder`, `msgraph`, `box`, `gdrive` | ⏭️ `skip` — credentials absents |

Un `skip` est visible dans le rapport pytest. Il dit « non vérifié », jamais
« vérifié » : c'est la différence entre une couverture connue et une couverture supposée.
**Cinq implémentations sur sept restent non éprouvées** faute de credentials.

### Innocuité

Le contrat s'exécute sous un préfixe unique par run (`colaig-contrat/<uuid>`) et chaque
test supprime ce qu'il a créé. Contrôle après exécution : **aucun objet résiduel**, la
racine du bucket ne montre que `qgis-workspace/`.

**Ouvert :** credentials pour les cinq backends non couverts, si on veut les éprouver.

---

## L1.2 — Contrat `MessagingProtocol` · 23/08/2026 · **TERMINÉ**

Critère de fin → **atteint**. 21 tests verts sur `fake`, `noop` et `webchat` ;
`matrix` en `skip` (homeserver et compte bot requis).

Branche `lot/L1.2-messaging-contrat`.

### Le contrat a d'abord condamné ma propre doublure

`FakeMessaging`, écrit deux lots plus tôt, divergeait du Protocol sur **deux points** :

1. `send()` acceptait un `reply_to` **qui n'existe nulle part** — ni dans
   `MessagingProtocol`, ni dans `matrix.py`, ni dans `webchat.py` — et omettait
   `is_status`, qui rend un message en `m.notice` sur Tchap. Je l'avais inventé.
   Une doublure plus permissive que le contrat laisse écrire des appels que la
   production refuse.
2. `run()` retournait immédiatement, et un test affirmait que c'était bien.
   Le Protocol dit « boucle d'écoute **infinie** », et `NoopMessaging` comme
   `WebChatMessaging` bouclent effectivement.

Le second point s'est vengé sur-le-champ : après correction de la doublure, la suite
**a pendu** — le test qui attendait un retour immédiat attendait pour toujours. C'est
précisément ce qui serait arrivé en production à un code écrit contre la doublure
complaisante. Le test dit maintenant l'inverse, et vérifie les deux moitiés du contrat :
la boucle ne retourne pas d'elle-même, et elle reste **annulable** — sans quoi l'arrêt
de Colaig serait un `kill -9`.

### Deux divergences réelles dans le code, latentes

- **`NoopMessaging.send_typing(conversation_id, **kwargs)`** — `**kwargs` n'absorbe que
  les mots-clés. `send_typing(conv, True)`, forme positionnelle que le Protocol
  autorise, levait donc un `TypeError` **sur ce backend seulement**. Vérification faite
  avant de corriger : les huit appels de `handlers.py` passent tous `typing=` en
  mot-clé, le piège n'était pas déclenché. Il attendait. Idem pour `send()`, aligné sur
  les quatre paramètres déclarés.
- **`on_message(handler)`** dans `noop` et `webchat`, contre `on_message(callback)` au
  Protocol. Sans effet tant qu'on appelle positionnellement — et `main.py` le fait aux
  deux endroits. Aligné.

### Ce que le contrat ne peut pas vérifier, et le dit

`NoopMessaging` **jette tout par construction** : c'est son objet. La livraison n'y est
donc pas observable, et le test le `skip` explicitement plutôt que de la simuler par une
assertion creuse. Pour `webchat`, une WebSocket factice permet d'observer une vraie
livraison.

**1667 tests passent, 68 sautent.**

**Ouvert :** credentials Matrix/Tchap de test pour couvrir le quatrième backend.

---

## L1.2 — complément : backend `matrix` vérifié sur le vrai Tchap · 23/08/2026

Sur autorisation explicite, et sous contrainte : **aucun envoi dans un salon existant**,
uniquement un salon créé pour l'occasion.

### Ce qui a été trouvé avant d'exécuter

Le compte bot de production est joignable **depuis le poste** : ses credentials sont dans
`colaig-v3/.env`. Il n'a jamais été nécessaire d'approcher le pod `colaig-0` — et ce
n'était pas possible, son namespace `user-nic01asfr` étant fermé à l'outillage.

Passe en lecture seule : **14 salons**, dont un à **53 membres répartis sur cinq
ministères**. Rien de tout cela n'entre dans le dépôt.

**`matrix.py::_on_invite` fait un auto-join de toute invitation**, et `run()` déclenche
ce callback via `sync_forever`. Démarrer la boucle d'écoute sur ce compte lui ferait
rejoindre des salons : un effet de bord sur la production. `run()` n'a donc **pas** été
exécuté, et le contrat porte ce `skip` avec sa raison.

### Le dispositif

Liste blanche d'envoi : le script ne pouvait émettre que vers les salons créés dans son
exécution. **Vérifié en tentant d'abord un envoi vers le salon à 53 membres** — refusé
avant l'appel réseau. Un garde-fou dont on n'a pas vu le rouge ne prouve rien ; celui-ci
a été mis en rouge exprès.

Salon `colaig-chantier-L1.2` créé, Nicolas Laval invité. Déconnexion et révocation
d'appareil à la fin de chaque passe.

### Résultat — relu depuis le serveur

```
m.text     'message simple'
m.text     'gras'  html='<b>gras</b>'
m.notice   'indexation en cours'
```

`formatted` produit un `formatted_body`, `is_status=True` produit un `m.notice`, le
défaut reste `m.text`. **La sémantique d'envoi du backend matrix est vérifiée contre le
vrai serveur**, pas contre une doublure.

**Ouvert :** un compte bot de **test** reste nécessaire pour lever le `skip` de `run()`
en CI. Sur un compte dédié, l'auto-join est sans conséquence.

---

## L1.3b — OCR des images et fin des documents silencieusement absents · 23/08/2026 · **TERMINÉ**

Branche `lot/L1.3b-ocr-images-et-signalement`. Issu de l'audit : deux défauts constatés
sur un corpus réel, corrigés avec leurs tests.

### 1. Les images n'atteignaient jamais l'OCR

Deux verrous en série. Le repli portait sur `filename.lower().endswith(".pdf")` — donc
jamais une image. Et de toute façon `is_supported()` les écartait **en amont**, dans
`index_workspace`, avant même `index_document`.

Un prédicat distinct plutôt qu'un élargissement : `is_supported()` garde son sens —
extraction **native** — parce que `mcp/server.py` et `rag/document_index.py` s'y fient
pour savoir s'ils obtiendront du texte. Les élargir leur ferait recevoir des chaînes
vides. C'est **`is_indexable()`** qui réunit extraction native et OCR, et lui seul sert
à l'indexeur. Les tests existants sur `is_supported("file.png") is False` restent verts.

### 2. Plus rien n'est silencieux

`logger.debug` puis `return False` : au niveau de log courant, rien. Désormais chaque
document non indexé est journalisé en **`warning`** avec son motif, et exposé par
`Indexer.documents_ignores` et `get_status()["ignored_documents"]`.

Les motifs distinguent les causes, parce qu'elles n'envoient pas chercher au même
endroit : « OCR en échec » désigne le modèle, « OCR sans résultat » le document,
« aucun client OCR configuré » la **configuration**. Un motif générique aurait fait
examiner le document alors que le problème est ailleurs.

Trois finitions au passage : un document produisant du texte mais **zéro chunk** était
encore muet ; un motif précis se faisait **écraser** par le motif générique en aval ; et
un document réparé restait signalé indéfiniment — il sort maintenant de la liste dès
qu'il s'indexe, sans quoi le signalement s'accumule et perd sa valeur.

### Vérification de bout en bout, sur le corpus réel

Le PNG et un PDF scanné, passés dans le **vrai** `Indexer` avec un OCR réel
(`lightonocr-2-1b`) :

```
sans client OCR : 0 indexés, 2 ignorés — motifs explicites, warnings émis
avec client OCR : 2 indexés, 16 chunks, 0 ignoré
```

**Le PNG s'indexe désormais** ; c'était impossible avant, à deux verrous près.

13 tests dédiés. **1680 tests passent**, 68 sautent, aucune régression.

**Ouvert :** rien. Le choix du modèle d'OCR par défaut reste suspendu à L1.5 — voir
l'audit : `lightonocr` est 4× plus rapide et plus propre, mais n'atteint que 82 % du
vocabulaire de `chandra`, et rien ne dit si les 80 mots d'écart sont du texte ou du bruit.

---

## L1.3 — Contrat `LLMClientProtocol` · 23/08/2026 · **TERMINÉ**

Critère de fin : « `docs/llm-capabilities.md` rempli par la sonde » → **atteint**.
47 tests, hors ligne, sur les cinq implémentations (`albert`, `openai`, `azure`,
`ollama`, `fake`). Branche `lot/L1.3-llm-contrat`.

Troisième et dernier contrat de Protocol. **Il a de nouveau condamné ma propre
doublure** — troisième fois sur trois.

### Le Protocol sous-déclare : cinq méthodes, huit appelées

| capacité | appelée par | au Protocol | albert | openai | azure | ollama |
|---|---|---|---|---|---|---|
| socle (5 méthodes) | partout | ✅ | ✅ | ✅ | ✅ | ✅ |
| `ocr` | `rag/indexer.py` | ❌ | ✅ | — | — | — |
| `rerank` | `rag/retriever.py` | ❌ | ✅ | ✅ | — | — |
| `transcribe` | `messaging/handlers.py` | ❌ | ✅ | ✅ | — | — |

`main.py` injecte le même client partout, quel que soit `LLM_BACKEND`. Avec `ollama`,
indexer un PDF scanné levait donc un `AttributeError`, rattrapé par un `except Exception`
générique et rapporté — depuis L1.3b — comme « OCR en échec ». **Un diagnostic faux, qui
envoie chercher du côté du modèle alors que le backend n'a simplement pas la capacité.**

Ces trois méthodes ne peuvent pas devenir obligatoires : exiger un OCR d'Ollama n'a pas
de sens. Elles deviennent **demandables** — `integrations/llm/capabilities.py` —, et
`indexer.py` interroge avant d'appeler. `motif_absence()` distingue « aucun client
configuré » de « ce backend n'a pas la capacité », parce que les deux n'envoient pas
chercher au même endroit.

### La doublure divergeait, encore

`FakeLLM` n'acceptait pas `priority`, déclaré par le Protocol sur `chat`, `chat_stream`
et `chat_with_tools`. Ce n'est pas un détail : `"background"` — OCR, indexation — prend
un **sémaphore réduit** pour toujours laisser un créneau aux requêtes des usagers. Un
travail de fond qui s'annoncerait `"user"` affamerait les conversations **sans erreur ni
trace**.

La doublure l'accepte désormais et **enregistre les priorités**, ce qui rend ce défaut
assertionnable — seule façon de l'attraper, il ne se voit ni dans un log ni dans un test
fonctionnel.

### Un test qui documente sans exiger

`test_la_matrice_des_capacites_est_celle_attendue` fige l'état constaté sans rien
imposer. Un backend qui gagne ou perd une capacité fait échouer le test et doit le
déclarer. Détecter la dérive, sans forcer une uniformité qui n'a pas lieu d'être.

**1727 tests passent**, 68 sautent.

**Ouvert :** rien. Deux arbitrages restent portés par L1.5 — le reranker et le modèle
d'OCR par défaut.

---

## Les trois contrats de Protocol sont posés

| lot | Protocol | implémentations vertes | ce que le contrat a trouvé |
|---|---|---|---|
| L1.1 | `StorageProtocol` | `fake`, `local`, **`s3` réel** | la doublure levait `FileNotFoundError` là où les **sept** implémentations lèvent `StorageFileNotFoundError` |
| L1.2 | `MessagingProtocol` | `fake`, `noop`, `webchat` | `reply_to` inventé, `is_status` oublié, `run()` qui rendait la main ; `send_typing` positionnel cassé sur `noop` |
| L1.3 | `LLMClientProtocol` | les 4 backends + `fake` | trois capacités appelées mais non déclarées ; `priority` absent de la doublure |

**Les trois fois, le contrat a d'abord convaincu la doublure d'être fausse.** C'est
précisément son intérêt : une doublure plus permissive que le contrat laisse écrire du
code que la production refuse, et une doublure plus stricte fait échouer des tests qui
devraient passer. Dans les deux cas on mesure autre chose que la production.

---

## L1.4 — Corpus de référence et amorce du jeu doré · 23/08/2026 · **PARTIEL**

Critère de fin : « `tests/golden/v1.jsonl` ≥ 200 cas, ≥ 3 espaces, revue humaine ».
**Non atteint.** 20 cas, 1 espace, revue non faite. Ce qui est acquis : le corpus, la
méthode, et l'outillage qui empêche un jeu doré faux. Branche
`lot/L1.4-corpus-marches-publics`.

### Le corpus — 184 documents, 1 762 articles

Code de la commande publique, articles **en vigueur uniquement**, depuis
`AgentPublic/legi` (Hugging Face, dérivé de LEGI/DILA), **Licence Ouverte 2.0** — donc
redistribuable dans un dépôt public.

Découpage par unité de travail du rédacteur : la hiérarchie du code, en descendant d'un
niveau tant qu'un groupe dépasse 40 articles. Médiane 6 articles par document. Un article
seul se cite mais ne s'explique pas ; un Titre de 159 articles noie la réponse.

**Source épinglée** sur un instantané daté et une révision, pas sur `legi-latest`. Je
l'avais d'abord tirée de `latest` et me suis corrigé : un corpus de référence qui bouge
n'est pas une référence, et un jeu doré écrit contre lui deviendrait faux **sans
prévenir**. Un manifeste d'empreintes SHA-256 rend la dérive détectable, et un test la
refuse.

Le corpus est **commité** : la référence L1.5 doit être reproductible hors ligne, et
1 Mo de Markdown ne pèse rien.

### Ce que la méthode a évité dès le premier cas

Chaque cas est écrit **contre un article lu dans le corpus**, jamais de mémoire. Le
premier chiffre vérifié a démenti ce que j'aurais écrit : le seuil de dispense de
publicité vaut **60 000 € HT** (fournitures et services) et **100 000 € HT** (travaux),
pas les 40 000 € couramment cités — chiffre qui, dans le code actuel, désigne la
publication des données essentielles (R2196-1).

Un jeu doré écrit de mémoire aurait donc été faux à son premier cas, et aurait fait
**échouer un pipeline correct**. Un test le verrouille désormais : tout montant avancé
par une réponse attendue doit se retrouver dans un article cité.

### Les cas négatifs sont le cœur

4 des 20 cas n'ont **pas** de réponse dans le corpus : le seuil européen de procédure
formalisée (renvoyé à un avis annexé), les CCAG, la jurisprudence, les formulaires DAJ.

Un jeu doré composé de questions répondables ne mesure que la capacité à répondre. Il ne
mesure jamais la capacité à **se taire** — alors que sur un corpus juridique, un seuil
inventé produit une procédure irrégulière. C'est l'échec le plus coûteux, et le seul
qu'un jeu doré naïf laisse passer. Un test impose au moins un cas négatif sur six.

### Composition actuelle

```
20 cas — fait 7 · procédure 6 · rédaction 3 · piège 4
         simple 10 · croisée 6 · négative 4
```

7 tests vérifient le jeu doré lui-même : correspondance au manifeste, existence de
chaque article cité, ancrage des montants, complétude de forme, cohérence des cas
négatifs, part minimale de négatifs.

**1734 tests passent.**

### Ce qui manque pour clore L1.4

1. **180 cas** — la méthode tient, le volume est du travail.
2. **Deux espaces de plus.** Les CCAG sont des arrêtés, donc dans la partition
   `legi_arrete` du même jeu de données : récupérables. Les fiches DAJ non —
   `economie.gouv.fr` renvoie 403 à toute récupération automatique.
3. **La revue humaine**, qui est une porte.

---

## D11 et L1.5 — sources synchronisées, et la première référence · 23/08/2026

### D11 — le mode se déclare par source

Question posée : le corpus interrogé pourrait-il venir d'une source tenue à jour ?
**Oui, mais par source, et jamais pour un espace de mesure.** Le corpus de mesure doit
être figé, le corpus d'exploitation doit être à jour : deux exigences opposées, toutes
deux non négociables. Un espace déclare son mode ; un espace synchronisé ne peut pas
servir de référence.

Pour Légifrance, le web n'est pas le bon tuyau : `AgentPublic/legi` publie **un
instantané tous les 14 jours** — sept intervalles consécutifs de 14 jours, mesurés. Se
synchroniser sur un jeu versionné donne un numéro de version citable et un diff ;
scraper ne donne ni l'un ni l'autre, et les deux sites concernés renvoient 403.

Rattaché à **L5.6**, déjà au plan, désormais dépendant de **L1.5**. Motif écrit dans
D11 : trois des six anti-patrons du projet viennent du sous-système web, dont le repli
hallucinatoire qui faisait **imaginer** au LLM le contenu d'une page inaccessible.

### L1.5 — référence v1

`docs/baseline-20260823.md`, produit par `_chantier/scripts/reference_l15.py`.

**Choix assumé : la référence v1 ne mesure que ce qui est déterministe.** La
récupération se rejoue à l'identique ; un score jugé par LLM varie d'une exécution à
l'autre, et en faire le socle reproduirait le « ça a l'air mieux » que ce chantier
combat. La génération est un palier distinct, à ajouter avec sa variance.

| | |
|---|---|
| Corpus | 185 documents, **2 124 chunks** (`Chunker(800, 100)`) |
| Embeddings | `bge-m3`, 1024 dimensions — défaut D10 |
| Index | `IndexFlatIP`, 8,7 Mo |
| **Récupération complète** | **11/17 — 65 %** |
| Partielle | 3 — 18 % |
| Nulle | 3 — 18 % |
| Rang médian du bon article | **1** sur k=6 |
| Latence de recherche | quelques ms |

### Les trois échecs, diagnostiqués

**Deux sur trois sont des échecs de découpage, pas de sémantique.** Vérifié : pour
mp-004 et mp-009, le **bon document a bien été remonté**, mais pas le passage portant
l'article. À 800 caractères, un document de 31 articles produit une vingtaine de chunks
et celui qui porte l'article se fait devancer par ses voisins.

Piste à **mesurer**, pas à appliquer : un découpage respectant la frontière d'article.
À éprouver contre cette référence — c'est exactement ce à quoi elle sert.

**Le troisième est le plus instructif.** mp-012 demande un seuil en euros : c'est un cas
négatif, la réponse n'est pas dans le corpus. Le moteur remonte alors des documents qui
**contiennent des montants** — précisément les mauvais. La question tire vers le piège et
la récupération suit. Aucun cas positif ne révèle ce comportement : c'est la
démonstration de l'utilité des cas négatifs.

### Ce que cette référence ne vaut pas

**17 cas seulement.** Un cas pèse 6 points. Un écart de moins de deux cas entre deux
exécutions n'est pas un signal. Le jeu doré doit atteindre son volume avant que les
variations fines soient interprétables — c'est écrit dans le rapport.

**Ouvert :** compléter le jeu doré ; ajouter le palier génération avec sa variance.

---

## L1.5b — le découpage par article, mesuré avant d'être appliqué · 23/08/2026

**Première utilisation de la référence pour arbitrer une modification.** C'est le moment
où l'appareil sert à ce pour quoi il a été construit.

| | témoin `Chunker(800,100)` | par article |
|---|---|---|
| chunks | 2 124 | **1 762** |
| index | 8,7 Mo | **7,2 Mo** |
| récupération complète | 11/17 — 65 % | **13/17 — 76 %** |

### L'hypothèse est confirmée par le mécanisme

Les **deux cas diagnostiqués** dans la référence — `mp-004` et `mp-009`, « bon document,
mauvais passage » — sont **précisément ceux qui se corrigent**. Plus `mp-002`, qui passe
de partiel à complet. Les rangs s'améliorent largement : 2→1, 3→1, 4→3.

Un gain agrégé aurait pu venir du hasard. Un gain sur les cas exactement prédits vient du
mécanisme.

### Mais une régression, et elle enseigne

**`mp-015` passe de ✅ à ❌.** L'article attendu, R2151-1, est **court et général** —
« l'acheteur fixe les délais en tenant compte de la complexité ». Au découpage par
document il vivait du contexte de ses voisins ; isolé, il se fait devancer par 1 761
autres articles qui parlent de délais plus spécifiquement.

Le compromis est réel : gain de précision sur les articles identifiables, perte sur les
articles courts qui vivaient de leur contexte. Ce n'est pas un défaut de la mesure, c'est
une propriété du choix.

### Ce que je n'ai pas conclu

Le solde est **+3 / −1** sur 17, soit deux cas nets — **précisément le seuil en dessous
duquel la référence déclare qu'il n'y a pas de signal**. Je l'avais écrit avant de
mesurer ; je m'y tiens. Le découpage reste un **paramètre**, défaut inchangé, décision
reportée au volume.

La régression suggère une troisième voie à éprouver : un article **enrichi de ses voisins
immédiats** plutôt qu'isolé, qui prendrait les deux gains sans la perte.

`docs/comparaison-decoupage-20260823.md`.

---

## Le jeu doré passe à 45 cas, et D12 devient décidable · 23/08/2026

25 cas ajoutés, chacun écrit contre un article lu dans le corpus. Vivier constitué par
extraction automatique des articles à énoncé net et court — 150 candidats — puis lecture
intégrale des quinze retenus avant rédaction.

```
45 cas — fait 16 · procédure 15 · rédaction 6 · piège 8
         simple 21 · croisée 16 · négative 8
```

### Ce que le volume a débloqué

Les deux stratégies de découpage ont été rejouées **à l'identique** sur 39 cas ayant des
articles attendus :

| | `Chunker(800,100)` | **par article** |
|---|---|---|
| complets | 28/39 — 72 % | **32/39 — 82 %** |
| nuls | **7** | **4** |

À 17 cas, la même mesure donnait +3/−1 — en dessous du seuil que la référence s'était
fixé, et j'avais refusé de conclure. À 39, elle donne **+4 complets et −3 nuls**, dans le
même sens sur les deux indicateurs.

**Rien n'a changé sauf le nombre de cas.** Même corpus figé, même manifeste, mêmes
embeddings, même script. La modification n'a pas changé ; la capacité à en juger, si.

**D12 actée** : la stratégie de découpage devient un paramètre d'espace, `article` pour
un corpus structuré en articles. Elle **ne vaut pas** pour le corpus SST — 51 PDF sans
structure déclarée — d'où un paramètre et non un changement de défaut global.

### Un test assoupli, pour la bonne raison

`test_un_cas_negatif_ne_promet_aucun_article` exigeait la locution exacte « ne figure
pas » et refusait « ne figurent pas ». Il vérifie un **sens**, pas une tournure : un test
qui impose une formulation fait réécrire les cas pour lui plaire au lieu de les vérifier.
Remplacé par un jeu de marqueurs.

**1734 tests passent.**

**Ouvert :** 155 cas, la revue humaine, le palier génération.

---

## Palier génération — la boucle complète, et un verdict · 23/08/2026

Analyse, hypothèse, mesure, correction. Trois pistes, dont une écartée sans mesure.

### Écartée avant d'être testée : le filtrage par score

Cinq des huit cas négatifs scorent **au-dessus** du plus faible cas positif — médianes
0,623 contre 0,681. Un seuil écarterait de vrais résultats sans écarter les pièges.
Mesurer ce qu'on sait déjà faux coûte une heure pour rien.

### Mesurée et adoptée : le durcissement du prompt

| | témoin ×2 | **durci** |
|---|---|---|
| refuse aux 3 exécutions | 0/8, 0/8 | **3/8** |
| ne refuse jamais | 3, 2 | **1** |
| cite l'article attendu | 29/37, 29/37 | **33/37** |
| hors contexte | 10, 14 | 14 |

**Je m'étais trompé sur le compromis.** J'annonçais un sur-refus sur les cas positifs
comme prix du durcissement ; c'est l'inverse — la citation de l'article attendu
s'améliore aussi. Rendre explicite la vérification des passages semble ancrer davantage
les réponses légitimes.

**Mais l'interdiction de citer hors passages n'a aucun effet mesurable.** Elle est écrite
dans le prompt, avec sa raison, et les chiffres ne bougent pas. Sur ce point, demander ne
sert à rien — ce qui justifie le contrôle mécanique.

### Corrigée mécaniquement : la provenance

`colaig/rag/verification_citations.py` compare les articles cités aux passages fournis.
Par construction : toutes les défaillances attrapées, aucun faux positif. Annote plutôt
que supprime — retirer la référence rendrait la réponse plus propre et **moins**
vérifiable.

### Deux défauts trouvés dans mes propres outils

**Les clients LLM rendaient une réponse vide en silence.** `qwen3-6-35b-moe` raisonne
3,4× plus qu'il ne répond ; sous mille tokens la réponse est vide, à 2048 — le défaut du
Protocol — elle est tronquée. Les quatre clients retournaient `content` sans regarder
`finish_reason`. Corrigé, factorisé, testé.

**Une exécution entière a mesuré la mauvaise chose.** Le script écrasait `sys.argv` avant
de lire l'argument de variante : le durcissement n'a pas été appliqué, et le rapport est
sorti sous le nom de la variante **sans qu'aucune erreur ne le signale**. J'ai failli
présenter ces chiffres comme l'effet du durcissement.

L'accident a servi : ce réplicat du témoin donne la **variance**, qui manquait. Les
métriques de citation bougent de 10 à 14 et de 1 à 3 à configuration identique — un écart
de trois cas n'y veut rien dire. Le refus, lui, est stable à 0/8 sur six mesures par cas.

### Verdict

**L'assistant n'est pas déployable en l'état.** Non parce qu'il répond mal — il cite
l'article attendu dans 33 cas sur 37 — mais parce qu'il **ne sait pas s'arrêter** :
3/8 de refus fiable, et cinq cas où l'utilisateur ne peut pas savoir à quoi s'attendre.

Ce n'est pas un défaut du corpus ni de la récupération, qui remonte le bon article dans
82 % des cas. C'est un défaut de garde-fou côté génération, et le prompt seul ne le
comble pas.

**Ouvert :** fabriquer le refus mécaniquement quand aucune citation ne provient des
passages ; réplicat du durci ; latence de 18,8 s contre les 10 s de H3.

---

## Prochaine action

1. **Compléter le jeu doré** vers 200 cas — la référence existe, elle attend du volume
   pour être interprétable finement.
2. **Revue humaine** du jeu doré — porte.
3. Éprouver le **découpage par article** contre la référence : deux des trois échecs
   viennent de là.
4. Ajouter le **palier génération**, avec sa variance mesurée sur plusieurs exécutions.
2. **L1.4 / L1.5 restent bloqués** : le jeu doré et la référence de mesure exigent
   l'accès aux conversations de `colaig-0`. **Aucun lot de phase 4 avant L1.5.**
2. Lancer `scripts/probe_s3.py` depuis le pod → lève **H3**, alimente H4 et H5.
   La sonde est déjà validée contre un S3 simulé, le pod est prêt et cloné.
3. Trancher l'arbitrage reranker et l'inscrire dans `DECISIONS.md`.
4. Créer un **compte de service** sur `minio-console.lab.sspcloud.fr` avant tout
   déploiement — credentials permanentes, rattachées au projet (H3bis).

## H3 levée — stockage S3 mesuré sur le vrai bucket · 22/08/2026

Le jeton OIDC fourni portait l'audience `minio-datanode` et la policy `stsonly` : c'est
exactement ce qu'il faut pour frapper des credentials S3 par
`AssumeRoleWithWebIdentity` contre `minio.lab.sspcloud.fr`. Obtenues, valides 7 jours.
**C'est le chemin qu'aucun outillage ne pouvait emprunter sans jeton OIDC** — le point
qui bloquait depuis le début.

| opération | médiane |
|---|---|
| LIST non récursif | **31 ms** |
| GET | **47 ms** |
| PUT (1 Ko) | **86 ms** |
| DELETE | **31 ms** |
| LIST récursif | 641 ms — **sur 14 objets seulement** |

Mesuré depuis le poste Windows via internet : c'est un **majorant** de la latence
intra-datalab, ce qui rend le verdict d'autant plus solide.

**H3 est levée pour les opérations unitaires.** 31 ms, très en deçà du seuil de 300 ms.
Le budget de 10 s n'est pas menacé par le stockage — le poste dominant reste le LLM
(1,19 s par tour outillé).

**Correction en cours de route :** le premier PUT donnait 437 ms, ce qui suggérait une
asymétrie lecture/écriture. Répété six fois, il retombe à 86 ms — les 437 ms étaient
l'établissement TLS. Un chiffre unique ne vaut pas mesure.

**H3ter créée.** Le listing récursif a été mesuré sur 14 objets. C'est l'opération qui
faisait exploser les timeouts de la version déployée, et ce chiffre ne dit **rien** de
son comportement à l'échelle. À remesurer sur un espace représentatif avant tout
arbitrage d'indexation. Extrapoler serait précisément l'erreur que ce chantier veut
éviter.

**Innocuité vérifiée :** après six itérations, aucun objet résiduel sous `.colaig-probe/`.
`qgis-workspace/` a été lu, jamais modifié.

**Ouvert :** un espace de test représentatif pour H3ter ; le compte de service pour le
déploiement (H3bis).

---

---

## L1.5c — l'instrument de mesure réparé, puis les leviers arbitrés · 23/08/2026

**Où reprendre :** branche `lot/L1.5b-decoupage-par-article`, commit `668d99c`.
1791 tests passent, 110 `skip`.

### Ce qui a été trouvé en étendant le jeu doré

Le jeu doré est passé de **45 à 122 cas** (définition du besoin, spécifications
techniques, labels, prix, durée, allotissement, marchés réservés, choix de la
procédure). En l'écrivant, **deux défauts du corpus de référence** sont apparus.

**Le corpus omettait du droit applicable.** Le filtre `status = 'VIGUEUR'` écartait
`VIGUEUR_DIFF` (26 articles entrés en vigueur à effet différé) et `ABROGE_DIFF`
(18 articles dont l'abrogation n'a pas pris effet). Le cas décisif : **`R2152-7`, qui
définit les critères d'attribution**, existait en deux versions — l'ancienne abrogée au
21/08/2026, la nouvelle en vigueur depuis. Le filtre écartait les deux.

Trouvé **par une mesure** : sur 610 articles du CCP cités *à l'intérieur* du corpus,
13 n'y figuraient pas. Un renvoi qui ne résout pas est le signal.

Règle désormais temporelle, `DATE_REFERENCE` épinglée. **1806 articles au lieu de 1762,
44 ajoutés, aucun retiré.**

**Les articles longs étaient tronqués.** `chunk_index = 1` ne gardait que le premier
fragment : 53 articles coupés en pleine phrase, les plus longs donc les plus denses.
Recollés, sans recouvrement.

### Ce que la remesure a donné

| | avant | après |
|---|---|---|
| cas dorés porteurs d'articles | 40 | **103** |
| tous les articles attendus remontés | 34 — 85 % | **88 — 85 %** |

Le taux **tient sur un échantillon presque triple**, ce qui était le seul moyen de
savoir s'il tenait.

### Arbitrage H2 — enfin possible, et il révèle un défaut

FAISS, BM25, RRF et MMR existaient dans `colaig/rag/` **sans avoir jamais été mesurés**.

| variante | cas complets / 103 |
|---|---|
| dense k=6 *(référence)* | 88 |
| **dense k=15** | **95** |
| dense k=20 | 96 |
| BM25 seul k=6 | 66 |
| RRF dense+BM25 k=6 | 84 |
| RRF dense+BM25 k=10 | 91 |

**Le terme de pertinence de MMR était numériquement invisible.** `λ·score − (1−λ)·div`
avec un `score` qui vaut une cosinus [0, 1] derrière FAISS mais `1/(60+rang)` — soit
0,016 — derrière RRF. Normalisation min-max : `RRF+MMR` 50 → 76, `dense+MMR` 76 → **88**.

> **Correction consignée :** j'avais conclu « MMR coûte 12 points de rappel ». Faux.
> MMR n'était pas nuisible, il était privé de son terme de pertinence.

### Ce qui reste ouvert, par ordre

1. **k=6 contre k=15 à la génération** — mesure lancée le 23/08, deux passes du prompt
   durci. k=15 gagne 7 points de rappel mais met 15 passages dans le prompt : plus de
   contexte, plus de latence, et peut-être **moins de refus**. Ne se tranche pas au
   niveau de la recherche.
2. **Brancher le vérificateur de fidélité** (L1.6, écrit et testé, non branché). Il
   ferme `mp-032`, seul cas du jeu doré qu'aucun contrôle mécanique n'attrape.
3. **Le jeu doré à 200 cas** — 122 aujourd'hui, dont 21 négatifs en deux familles
   (`absence` et `premisse`).
4. **Corpus CCAG.** Cinq échecs de recherche sont **hors de portée de tout réglage** :
   « clauses administratives particulières », « règlement de consultation », « acte
   d'engagement » ont **0 occurrence** dans le code. Ce sont des mots des CCAG, qui sont
   des arrêtés absents du corpus. C'est une décision de corpus, pas de moteur.
5. **`run()` de Matrix** — deux motifs distincts de `skip` : un arbitrage (auto-join sur
   un compte de production, une invitation en attente) et un environnement (libolm
   indisponible sous Windows). Le second tombe sous WSL ou en conteneur.
6. **Relecture humaine du jeu doré** — porte explicite. Je garantis la fidélité à
   l'article cité, pas la pertinence pratique en droit.

### Décisions actées ce jour

D13 (aucune organisation nommée) · D14 (un test rouge pour raison d'environnement est un
test à réparer) · D15 (le chiffrement Matrix est un prérequis, pas une option) ·
D16 (référence de recherche sous-estimée) · D17 (le corpus omettait le droit applicable).

---

## L1.5d — l'instrument relu, et ce qu'il cachait · 23/08/2026

**Où reprendre :** branche `lot/L1.5b-decoupage-par-article`, commit `3974a33`.
1805 tests, 110 `skip`. Jeu doré à **124 cas**, dont 21 négatifs.

### Le jeu doré avait 25 % de cas fautifs

Quatre agents ont relu les 122 cas, chacun un quart, article par article contre le
corpus figé : **31 fautifs, 29 douteux, 61 sains**. Tous corrigés (31 + 22).

Le défaut était **systématique et toujours dans le même sens** : l'article porte une
condition restrictive, la réponse attendue retient la règle et laisse tomber la borne.
Un instrument construit ainsi **récompense la réponse incomplète et pénalise la réponse
complète**.

**`mp-032` et `mp-060` déclaraient absente une information présente.** `R2191-7` donne
le taux de l'avance ; `R2143-11` le cadre des renseignements exigibles. Chaque mesure
comptait donc comme un défaut de refus le comportement **correct** du modèle.

> **À retenir sur la méthode.** Le contrôle mécanique écrit pour détecter ce défaut
> (`controle_bornes.py`) en trouve **6 sur 101** ; la relecture en a trouvé **31**.
> Ce défaut n'est pas mécaniquement détectable — il fallait lire.

### La configuration de production est arbitrée

| | avec raisonnement | **sans raisonnement** |
|---|---|---|
| refuse à chaque fois | 15/18 | **21/21** |
| réponses tronquées | 39 | **2** |
| cite l'article attendu | 66/101 | **88/102** |
| latence médiane | ~15–20 s | **2 s** |
| citation hors contexte | 0 | 22 |

Le raisonnement n'achetait que la **discipline de provenance**, et c'est exactement ce
que le garde-fou mécanique rattrape : 137 rendues, 23 annotées, 4 remplacées sur 164.
`reasoning_effort` est **silencieusement ignoré** par l'endpoint. **H3 est tenue** —
10 s visés, 2 s obtenus.

### Le défaut qu'aucun garde-fou ne peut attraper

**108 citations sur 469 — 23 % — portent sur un article hors du régime ordinaire**,
presque toutes du livre défense-sécurité, dont les articles sont des jumeaux textuels
aux seuils différents. Ces articles **étaient dans les passages** : la provenance est
correcte. C'est une réponse **fidèle qui cite le mauvais droit**.

Seul le **périmètre du corpus** peut le corriger. Mesure en cours au 23/08 sur un corpus
restreint à la 2ᵉ partie livre Ier — 38 % du corpus, et les 117 articles attendus s'y
trouvent tous.

### Points ouverts, par ordre

1. **Trancher la restriction du corpus** au vu de la mesure en cours. Elle refigerait le
   corpus et invaliderait la référence une fois de plus — c'est le coût à peser.
2. **Brancher le vérificateur de fidélité** (L1.6, écrit et testé). Attention : il ne
   corrige **pas** le défaut ci-dessus — une réponse fidèle à un passage du mauvais
   livre reste étayée. Il attrape l'inférence qui déborde, ce qui est autre chose.
3. **Corpus CCAG** — décision de périmètre. `FORMAT_CLAUSE` est prêt côté reconnaissance
   des citations ; `_extract_sources` ne rend toujours que des noms de fichiers.
4. **`run()` de Matrix** — l'invitation en attente n'est pas acceptée, conformément à la
   consigne : rien n'est accepté qui ne vienne de l'utilisateur ou de l'agent. Reste
   l'obstacle libolm sous Windows, qui tombe en WSL ou en conteneur.
5. **Editeur** — l'ancrage au rectangle n'est pas repris : `DocumentChunk` sait le
   transporter (`metadata`), `GeneratedResponse.sources` ne sait pas le restituer, et
   cette valeur est celle d'un poste de rédaction, pas d'un assistant conversationnel.
   La **portée** (obligation / faculté) reste à prendre, elle est bon marché.

---

## L2.1 — le balisage des contenus non fiables · 24/08/2026 · **TERMINÉ**

**Critère de fin** : aucun contenu externe n'entre dans un prompt autrement que par un
point de passage unique, et un test de portée dépôt le vérifie.
**Commit** : `b578b9c` sur `lot/L1.5b-decoupage-par-article`. 1837 tests verts.

### Ce qui existait ne balisait pas, il en donnait l'apparence

Trois sites entouraient les passages de `<<<DOCUMENT>>>` … `<<<FIN DOCUMENT>>>` en
insérant le contenu **tel quel**. Un document contenant ce marqueur ferme sa propre
balise ; il suffit de déposer un fichier sur l'espace pour la forger. Le nom de la
source entrait de la même façon — un nom de fichier est choisi par le déposant.

Le motif était écrit **trois fois** : `rag/generator.py`, et deux fois dans
`agents/synthesiser.py`. La duplication contre laquelle le nouveau module met en garde
s'était déjà produite avant qu'il existe.

### Ce qui est fait

`colaig/security/wrap.py` — `baliser()`, `formater_skills()`, `CONSIGNE`. **Onze sites**
portés sur les cinq familles du principe 4. Détail et raisonnement en **D35**.

Deux valent d'être retenus :

- Le champ `instructions` du handshake MCP était concaténé au **message system**. Un
  tiers réseau obtenait l'autorité du système. Il reste transmis — il porte une
  information utile — mais comme donnée, et sous un titre qui ne le présente plus comme
  une instruction.
- `rag/specializer.py` dérive le persona de l'espace depuis son corpus et **l'écrit dans
  la configuration**. Un document déposé pouvait réécrire le `system_prompt` de
  l'instance : une injection qui survit à la conversation au lieu de s'éteindre avec elle.

Un site reste non balisé **délibérément** : `rag/verificateur_fidelite.py`, dont le taux
de détection est un seuil de `reference.json` calibré avec ce prompt exact. Le baliser
invaliderait la calibration. « Rien n'est activé sans mesure » vaut aussi contre soi.

### Le constat annexe, qui vaut plus que le lot

Vérifié en cherchant si ce changement affectait la référence L1.5 : **il ne l'affecte
pas, parce que le harnais de mesure n'utilise pas le prompt de production.**
`reference_generation.py` assemble ses passages avec `"\n\n---\n\n"` et n'appelle jamais
`generator.py`.

La référence mesure donc le **modèle, le corpus et la recherche** — pas l'assemblage de
prompt réellement livré à l'utilisateur. Aucun des sept seuils ne garde ce dernier : on
peut casser le prompt de production sans qu'une seule porte ne s'ouvre.

C'est la même famille de défaut que les cinq copies du motif d'en-tête et que la CI qui
n'avait jamais tourné sur une branche de lot : **l'instrument mesure autre chose que ce
qu'on croit.**

### Points ouverts, par ordre

1. **Faire passer le harnais de mesure par `generator.py`.** Sans cela la porte de
   régression laisse dériver le prompt de production en silence. C'est la suite
   immédiate, avant tout autre lot de la phase 2.
2. **Trois défauts recensés et non traités**, parce qu'ils relèvent d'un arbitrage :
   un `.md` déposé dans `.colaig/prompts/` **remplace intégralement** le prompt système
   et passe **avant** le template Colaig ; `task_scheduler.py` court-circuite
   `sanitize_system_prompt` ; `sanitize_description` est définie et appelée nulle part.
3. **Mesure perdue à reprendre** : l'essai « le raisonnement améliore-t-il le
   vérificateur de fidélité » a rendu un code 0 et **une sortie vide** — la redirection
   n'a rien capté. À relancer en écrivant dans un fichier.
4. Le reste de la phase 2 — L2.2 à L2.6 — inchangé, et toujours marqué **bloquant avant
   tout multi-utilisateurs**.

---

## La référence mesure enfin le prompt livré · 24/08/2026 · **TERMINÉ**

Point 1 des points ouverts de L2.1, traité dans la foulée. **Commit** `94019f9`.

`reference_generation.py` passe par `Generator._build_messages` — le point de couture
qui sépare l'assemblage du prompt du client HTTP, pour que le harnais garde la main sur
`max_tokens` et `enable_thinking`, dont ce chantier a mesuré qu'ils décident de tout.

**Remesure complète** — 135 cas, k=10, durci, sans raisonnement, prompt de production :

| indicateur | seuil | ancien prompt | **prompt livré** |
|---|---|---|---|
| refus systématique | ≥ 0,95 | 22/22 | **22/22** |
| cite l'attendu | ≥ 0,78 | 0,836 (92/110) | **0,805 (91/113)** |
| citation hors contexte | ≤ 34 | 24 | **20** |
| citation fantôme | ≤ 10 | 5 | **8** |
| montant inventé | ≤ 2 | 0 | **0** |
| réponse tronquée | ≤ 12 | 3 | **0** |
| latence médiane | — | 2 s | **2,1 s** |

**Les sept seuils tiennent.** Deux indicateurs s'améliorent : plus aucune réponse
tronquée, et quatre citations hors contexte de moins.

`cite_attendu` baisse de 0,836 à 0,805, mais les dénominateurs diffèrent — 113 cas
jugeables contre 110. Les trois de plus sont ceux qui n'étaient pas jugeables faute
d'avoir été tronqués. En valeur absolue : 91 réponses correctes contre 92, sur trois
tentatives de plus abouties.

`garde_fou_rendues` n'a **pas** été remesuré : il vient d'une analyse distincte.

## Deux défauts trouvés en chemin

**D36 — le corpus n'a jamais compté 1026 articles, mais 1021.** Écrit dans
`reference.json` puis repris cinq fois dans `DECISIONS.md`, dont une sous la forme
« 1026 articles indexés sur 1026 » qui a l'air d'une vérification de complétude sans en
être une. Aucun seuil n'en dépendait ; corrigé, avec la trace.

**Six attentes d'horloge dans `test_executor.py`.** L'une a échoué une fois, en suite
complète, sur une exécution à 93 s concomitante de la mesure LLM. **La cause n'est pas
établie** : quatre tentatives de reproduction, dont deux sous forte charge à 79 s, sont
toutes vertes. Les attentes sont désormais conditionnelles, et la docstring dit ce qui
a été observé sans affirmer ce qui ne l'a pas été.

## Points ouverts

1. **Trois défauts de conception recensés, non traités** — un `.md` dans
   `.colaig/prompts/` remplace intégralement le prompt système et passe **avant** le
   template Colaig ; `task_scheduler.py` court-circuite `sanitize_system_prompt`. Le
   troisième, `sanitize_description` jamais appelée, est **fait** (`414f1c9`).
2. **Mesure à reprendre** : « le raisonnement améliore-t-il le vérificateur de
   fidélité » — sortie vide au premier essai.
3. **L2.2 à L2.6**, toujours **bloquants avant tout multi-utilisateurs**.

---

## L2.1 — Balisage des contenus non fiables · 24/08/2026 · **TERMINÉ**

**Critère du plan** : « un test qui échoue si un chunk arrive non balisé ».
**Atteint** — `tests/test_l21_critere_de_fin.py` : tout module de `colaig/` qui appelle
un LLM passe par `security/wrap.py`, ou figure dans `DISPENSES` **avec sa raison
écrite**. Vérifié aussi dans l'autre sens : un module témoin appelant un LLM sans baliser
fait bien échouer la garde.

Trois tests le tiennent : la garde elle-même, un test qui refuse une dispense portant sur
un module disparu, et un test qui exige que la seule dispense **mesurée** — le
vérificateur de fidélité — garde sa raison inscrite dans le code.

**1886 tests verts.** Dernier commit : voir `git log` sur `lot/L1.5b-decoupage-par-article`.

### Ce que le lot a produit

| | |
|---|---|
| `colaig/security/wrap.py` | point de passage unique, plus `colaig/security/CLAUDE.md` |
| 11 sites portés | les cinq familles du principe 4 |
| D35 | le balisage, et pourquoi le vérificateur en est excepté |
| D44 | **cinq** points d'entrée, pas un — le web était ouvert |
| D45 | une clé, trois rôles, absente par défaut |
| D46 | la carte de réception d'un message et ses quatre trous |

### Les défauts trouvés en chemin, et fermés

- `create_document` et la livraison de tâche faisaient écrire Colaig dans son propre
  `.colaig/` — la chaîne complète de l'injection à la persistance (D37).
- `colaig lier` énumérait tous les espaces de l'instance et rattachait n'importe quel
  salon à n'importe lequel : **deux messages ouvraient n'importe quel corpus** (L2.1d).
- Huit API web d'administration étaient ouvertes sur `0.0.0.0`, dont le rattachement —
  la même chaîne, sans même être invité (L2.1e).
- Le secret de signature des sessions était une constante d'un dépôt public (L2.1f).
- `sanitize_description` était définie et jamais appelée.
- Six attentes d'horloge dans `test_executor.py`, dont une intermittente.
- Mon propre filtre `code_seul` ne retirait que les docstrings de module — la garde du
  marqueur de balisage passait pour une raison partielle.

### Ce qui reste ouvert, routé vers son lot

**Vers L2.2** — *« le lot le plus urgent du chantier »* selon le plan, et le recensement
de D37 le confirme : `mcp_connectors` se lit depuis `config.yaml`, donc **qui écrit dans
l'espace branche un serveur MCP distant** dont Colaig appellera les outils. Le champ
`instructions` de ce serveur est désormais balisé (D35), mais l'outil, lui, s'exécute.

**Vers L2.6** — le câblage, qui est le motif récurrent de ce lot : trois mécanismes
existent et ne sont pas branchés là où ils serviraient.
- `TaskExecutor` a une file par conversation, non branchée sur le chemin Matrix : deux
  messages rapides et le second efface le tour du premier (D46).
- `check_quota` n'existe que dans `albert.py` — zéro dans les trois autres clients, dont
  `openai_client` qui est la cible de production (D46).
- `MegolmEvent` n'a aucun rappel : un message indéchiffrable disparaît sans un mot (D46).

**Vers L3.1** — le plan prévoit de porter le scoring de binding de la version déployée.
**Ne pas le porter tel quel** : sa règle 5, « nom du salon == nom de l'espace », n'est pas
opt-in, et lie automatiquement un salon à un espace au seul choix de son nom (D41). La
retirer, ou l'aligner sur les deux regex qui, elles, sont déclarées par l'espace.

**Vers L6.1** — « Permissions read/write/admin/bot + héritage droits WebDAV ». D42 et D43
l'instruisent : les droits fichiers et les droits Tchap **ne se croisent pas, ils se
composent** — le salon décide qui interroge, le dossier ce qui est interrogeable. Et
l'index déclassifie : **le dossier partagé est l'unité de confidentialité, pas le
fichier**. Deux signaux sont lisibles et inexploités — l'appartenance au salon, et les
niveaux de pouvoir.

### Arbitrages en attente, qui ne bloquent pas la suite

1. `/ask`, `/chat`, `/webhooks/storage` — garder, restreindre, retirer.
2. Refuser de démarrer sans clé avec un port exposé, plutôt qu'ouvrir en silence ; ou
   n'écouter que sur la boucle locale par défaut.
3. Séparer les trois rôles de `COLAIG_PLATFORM_API_KEY`.
4. `storage_readonly` : tenir la promesse, découpler l'état, ou retirer le champ (D37,
   D38 — arbitrage 1 **validé sur le principe**, non engagé).
5. Corriger `docs/SECURITE.md`, qui présente comme protections deux gardes éteintes par
   défaut.

### Dettes de mesure

- `garde_fou_rendues` n'a pas été remesuré avec le prompt de production.
- L'essai « le raisonnement aide-t-il le vérificateur » est à relancer.
- La moitié Box de `sonde_partage_inverse.py` attend de tourner là où vit le secret.
- L1.4 reste incomplet selon son propre critère : ≥ 200 cas sur ≥ 3 espaces ; nous avons
  135 sur un seul.

### La suite

**L2.2**, sans hésitation : le plan le désigne comme le plus urgent, D37 en a mesuré le
mécanisme exact, et il ne dépend d'aucune inconnue.

---

## L2.2a — Sans clé, le serveur web n'écoute que la boucle locale · 24/08/2026 · **TERMINÉ**

Préalable à L2.2, tranché par l'utilisateur (option **b**). **Commit** `0caacdc`.

La liste blanche de L2.2 vit dans `config/clients.yml`, réécrivable par
`POST /api/platform/provision`, gardée par une clé absente par défaut. Livrer L2.2 sans
cela aurait produit un lot dont le critère passe en test et **reste inerte en
déploiement** — le défaut même que ce chantier passe son temps à trouver.

La garde d'authentification n'est pas fermée — cela casserait les auto-hébergés qui s'en
passent délibérément. **C'est le sens de l'échec qui change** : clé absente →
`127.0.0.1` et le journal dit pourquoi ; clé posée → `0.0.0.0` ; `COLAIG_WEB_HOST` →
ce qu'il dit. Une variable oubliée ne donne plus rien, et ouvrir redevient un acte.

`docs/SECURITE.md` corrigé : §9 présentait comme protections des gardes éteintes par
défaut, §8 annonçait un quota qui n'existe que dans `albert.py`.

## L2.2 — Liste blanche MCP au niveau instance · 24/08/2026 · **TERMINÉ**

**Critère du plan** : « un `mcp_servers.json` hors liste ne produit aucun outil ».
**Atteint.** **Commit** `c9d1575`. **1898 tests verts.**

Le lot que le plan désignait comme le plus urgent, avec ce motif : *« quiconque écrit
dans le WebDAV d'un espace injecte un outil arbitraire dans le registre de l'agent »*.
D37 l'avait confirmé sur pièces.

L2.1 avait traité le champ `instructions` de ces serveurs — il entre désormais comme
donnée balisée. **Mais l'outil, lui, s'exécute.** Déclarer n'est pas empêcher.

`colaig/security/mcp_policy.py` est le point de passage unique, et un test de portée
dépôt refuse qu'un module lise `mcp_connectors` sans y passer — un filtre appliqué à
trois sites sur quatre ne filtre rien.

**Le défaut est REFUS**, contrairement aux autres champs de `platform_policy`, et la
divergence est visible dans la valeur : `absent`/`[]` → aucun ; `["*"]` → tous,
explicitement ; une liste → ceux-là. Les autres champs bornent ce que l'**opérateur**
déclare ; celui-ci borne ce que l'**utilisateur final** écrit dans son espace.

La comparaison est ancrée sur l'autorité et le chemin — `startswith` nu laisserait
passer `https://mcp.interieur.gouv.fr.attaquant.fr`. Un serveur écarté est journalisé
avec l'endroit où l'autoriser.

### La suite

**L2.3** — épinglage des schémas d'outils MCP (`mcp_pins.json`). Il dépend de L2.2, qui
est fait. Critère : *changement de schéma → outil désactivé + alerte*.

Restent inchangés : les quatre arbitrages non tranchés (`/ask` et `/chat` ; séparer les
trois rôles de la clé ; `storage_readonly` ; corriger le reste de la doc), et les dettes
de mesure — dont l'essai « le raisonnement aide-t-il le vérificateur », relancé en tâche
de fond le 24/08 après un premier essai à sortie vide.

---

## L2.3 — Épinglage des schémas d'outils MCP · 24/08/2026 · **TERMINÉ**

**Critère** : « changement de schéma → outil désactivé + alerte ». **Atteint.**
Commit `789279b`.

L2.2 décide quels **serveurs** sont montés ; il ne dit rien de ce qu'ils font ensuite.
Un serveur admis peut se faire accepter avec un outil anodin, puis en changer le
contrat — le modèle, lui, voit un outil qu'il connaît.

L'empreinte porte **nom, description et schéma d'entrée** : ce que le modèle lit pour
décider d'appeler. La description en fait partie, et c'est le point — un serveur qui ne
change qu'elle n'a modifié aucun paramètre et a pourtant changé le contrat.

Sérialisation canonique (un remaniement d'ordre n'est pas une mutation), confiance à la
première vue, empreintes dans `config/mcp_pins.json` **sur l'hôte** — hors de portée de
ceux contre qui la garde protège. Un magasin non inscriptible rend l'épinglage inerte,
et le journal le dit.

**Deux** tests pour le branchement : l'un vérifie que le module est *cité*, l'autre
qu'il *agit*. Un import inutilisé passerait le premier.

## L2.4 — Confirmation des actions destructives · 25/08/2026 · **TERMINÉ**

**Critère** : « aucun destructif exécuté sans confirmation ». **Atteint.**
Canal tranché par l'utilisateur : **réponse texte** (option b, D47).

### L2.4a — la classification

`colaig/security/actions.py` classe les 22 outils intégrés, et tranche le cas MCP selon
la spécification : `readOnlyHint` vrai ou `destructiveHint` faux → inoffensif ; **sinon
destructif, annotation absente comprise**. Un serveur qui n'annote rien ne promet rien.

Un test refuse qu'un outil intégré ne soit classé nulle part — sinon il serait traité
comme un externe, donc destructif, **mais par accident**.

### L2.4b — la garde

**La reconnaissance de la réponse est mécanique, et c'est le cœur.** Si un modèle
décidait ce qui vaut confirmation, une consigne déposée dans un document produirait la
sienne. La comparaison porte sur le message **entier**, jamais une sous-chaîne —
« surtout pas oui » contient « oui ».

Trois propriétés tenues : ce qui n'est ni oui ni non **annule** l'attente ; l'attente
**expire** ; l'accord donné est **à usage unique**, borné à un outil, un salon, et dans
le temps.

Les attentes vivent **en mémoire**, pas dans `.colaig/` : une attente rangée dans
l'espace serait modifiable par qui y écrit, et l'utilisateur confirmerait « crée le
document X » pour un appel devenu autre. Un redémarrage les perd — échec dans le bon
sens.

**Ce qui reste imparfait, et c'est dit** : après accord, l'appel n'est pas rejoué
directement — l'utilisateur reformule. Le tour interactif ne sait pas reprendre un appel
d'outil isolé hors de la boucle agentique. Moins élégant, parfaitement honnête : rien ne
s'exécute sans accord explicite, et l'accord ne vaut qu'une fois. `TODO-NORMALE` posé.

### La suite

**L2.5** — suite adversariale, méthodologie AgentDojo. Critère : **zéro appel d'outil
non planifié**, ≥ 20 attaques. Elle dépend de L2.1 à L2.4, tous clos.

C'est elle qui mesurera ce que les quatre lots précédents *déclarent* : le balisage dit
au modèle qu'un contenu est une donnée, il ne garantit pas qu'il l'écoute ; un
`readOnlyHint` vient du serveur et peut mentir ; la confirmation ne garantit pas que le
modèle ne trouvera pas un chemin détourné.
