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
| **Lot en cours** | L0.3b — **TERMINÉ**. Suivant : L0.4 (harnais) |
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

## Prochaine action

1. **L0.4** — harnais de test. Le critère « suite hors ligne < 60 s » est **déjà tenu**
   (17,7 s hors `test_live`) ; reste à écrire les `FakeStorage` / `FakeMessaging` /
   `FakeLLM` déterministes. `tests/conftest.py` existe (334 lignes, 13 fixtures) et est
   déjà unique dans le dépôt.
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
