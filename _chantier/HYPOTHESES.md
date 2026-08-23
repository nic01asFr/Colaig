# Hypothèses à lever

Une hypothèse non levée ne se remplace **jamais** par une valeur par défaut plausible.
Si un lot bute dessus : arrêter le lot, inscrire le blocage dans `AVANCEMENT.md`, demander.

| # | Hypothèse | Statut | Bloque | Comment lever |
|---|---|---|---|---|
| **H1a** | **Albert** sert un chat **avec tool calling** + embeddings | ✅ **levée le 22/08/2026** | — | mesurée : `mesures/llm-capabilities-albert.md` |
| **H1b** | **SSPCloud** sert un chat **avec tool calling** (`qwen3-6-35b-moe`) | ✅ **levée le 22/08/2026** | — | mesurée : `mesures/llm-capabilities-sspcloud.md` |
| **H2** | Un reranker est disponible (SSPCloud ou Albert) | ⚠️ **levée pour Albert, PAS pour SSPCloud** | L4.1 — impose un arbitrage | Albert : `bge-reranker-v2-m3` OK (0,12 s). SSPCloud : **aucun reranker au catalogue**. Voir « Arbitrage reranker » ci-dessous. |
| **H3** | La latence du **stockage S3 SSPCloud** est compatible avec une réponse < 10 s | ✅ **levée le 22/08/2026 pour les opérations unitaires** | — | mesurée : `mesures/s3-sspcloud.md`. 31 ms en LIST non récursif. |
| **H3ter** | Le **listing récursif** tient à l'échelle d'un corpus réel | ⚠️ **levée à 59 documents, inconnue au-delà** | L4.1, stratégie d'indexation | 47 ms de médiane sur 63 objets / 43,8 Mo. Loin du seuil de 10 s. Reste à éprouver sur un corpus de plusieurs milliers de documents. |
| **H3bis** | Les credentials S3 SSPCloud peuvent être **non expirantes** | ✅ **levée le 22/08/2026 par la documentation** | — | un **compte de service** MinIO donne un couple access/secret permanent, rattaché à un projet. Console : `minio-console.lab.sspcloud.fr`. Reste à le créer. |
| **H4** | `colaig-0` a assez d'historique pour ≥ 200 cas dorés | ❌ non levée | L1.4 | compter les `.colaig/conversations/*.json` |
| **H5** | Le corpus reste sous le seuil de `IndexFlatIP` (exact, O(n)) | ❌ non levée | L4.1 | compter documents et poids par espace |
| **H6** | L'agent peut pousser sur GitHub et déployer sur SSPCloud | ✅ **levée pour GitHub le 22/08/2026** | — | PAT fine-grained vérifié : `push: true` sur `nic01asFr/Colaig`. Déploiement SSPCloud : voir L3.6. |

---

## Mesures déjà faites — pod `proj-colaig-refonte-jupyter-python-0`, 22/08/2026

```
Python 3.13.13 · git 2.55.0 · node ABSENT
Disque /home/onyxia/work : 9,8 Go libres (vide)
Réseau sortant : github 200 · pypi 200 · llm.lab.sspcloud.fr 200
```

**Résultats bloquants :**

- `GET https://llm.lab.sspcloud.fr/api/models` → **401 `Not authenticated`**
  → la clé LLM est indispensable, elle n'est pas dans l'environnement du pod.
- `kubectl get secrets` → **403 Forbidden** :
  `serviceaccount:user-nic01asfr:proj-colaig-refonte-jupyter-python cannot list secrets`
  → **le pod ne peut pas lire le Secret `*-secretassistant` lui-même.** L'auto-découverte
  de clé implémentée dans `platform/sspcloud.py` (PROD) suppose le rôle `edit` du chart
  Helm ; ce pod-ci ne l'a pas. À reproduire au lot L3.6 avec le bon rôle.
- Aucun token GitHub (`GH_TOKEN` absent), aucune clé SSH dans `~/.ssh`.

---

## Ce qui manque pour démarrer

| Besoin | Pour quoi | Bloque |
|---|---|---|
| **Clé API LLM SSPCloud** | lever H1 et H2 | tout le code |
| **Token GitHub** (scope `repo`, push sur `nic01asFr/Colaig`) | D5, persistance du travail | tout |
| **Compte bot Matrix/Tchap de test** + salon dédié (≠ production) | L1.2, tests bout en bout | phase 1 |
| **Bucket S3 SSPCloud** (endpoint, bucket, access/secret, éventuel session token) + quelques documents | L1.1, lever H3 | phase 1 |
| **Accès aux conversations de `colaig-0`** + feu vert anonymisation | L1.4, jeu doré | phase 1, donc phase 4 |
| Droit de créer/détruire des services Onyxia | L3.6, chart Helm | phase 3 |
| Licence retenue + autorisation de publication Cerema | D4 | publication |

---

## Mesures LLM — Albert, 22/08/2026

Sonde exécutée depuis le poste local avec les credentials de `colaig-v3/.env`.
Rapport intégral : `mesures/llm-capabilities-albert.md`.

| | |
|---|---|
| Endpoint | `https://albert.api.etalab.gouv.fr/v1` |
| Chat | `openai/gpt-oss-120b` — 200 en 0,20 s |
| **Tool calling** | **présent**, `tool_calls` bien formé — 0,41 s |
| Embeddings | `qwen3-vl-embedding-8b` — **dimension 4096** |
| Reranker | `bge-reranker-v2-m3` — 0,12 s |

**Correction du 22/08/2026 — ce paragraphe disait le contraire de la vérité.**
Il affirmait qu'`ALBERT_API_URL` « omet `/v1`, ce qui renvoie 404 sur tous les appels ».
C'était un diagnostic erroné, tiré du comportement de la sonde et non de celui du code.

Vérification faite : `integrations/albert.py` et `llm/openai_client.py` construisent
eux-mêmes `f"{base_url}/v1/chat/completions"`. La base **doit donc être sans `/v1`**, et
le défaut de `config.py` — `https://albert.api.etalab.gouv.fr` — est **correct**.
Mesuré : cette base suivie de `/v1/models`, exactement ce que construit le client,
retourne 200 et 10 modèles. **Ajouter `/v1` à la configuration produirait `/v1/v1/` et
casserait tout.** C'est la sonde qui appelait `/models` sans préfixe, pas le code.

**Ce qui n'est PAS mesuré :** SSPCloud. Le catalogue ci-dessus est celui d'Albert et rien
ne permet de le transposer. `qwen3-6-35b-moe` reste **INCONNU**.

**Conséquence sur H4/H5 :** une dimension d'embedding de 4096 est élevée — 16 Ko par
vecteur en float32. Le seuil de `IndexFlatIP` de H5 doit être recalculé sur cette base,
pas sur les 1024 d'un `bge-m3`.

---

## Mesures LLM — SSPCloud, 22/08/2026 · **cible de production (D3)**

Sonde exécutée avec `sspcloud_api_key` (fourni dans le `.env` du chantier).
Rapport intégral : `mesures/llm-capabilities-sspcloud.md`.

| | |
|---|---|
| Endpoint | `https://llm.lab.sspcloud.fr/api/v1` (le suffixe `/v1` est optionnel ici, les deux répondent) |
| Catalogue | 7 modèles : `gemma4-26b-moe`, `qwen3-6-35b-moe`, `qwen3-vl`, `qwen3-embedding-8b`, `chandra-ocr-2`, `qwen3-8-27b`, `qwen3-cursor` |
| Chat | `qwen3-6-35b-moe` — 200 en 0,39 s |
| **Tool calling** | **présent**, `tool_calls` bien formé — 1,19 s |
| Embeddings | `qwen3-embedding-8b` — **dimension 4096** |
| Reranker | **aucun au catalogue** |

**La boucle agent native est réalisable sur la cible de production.** Les lots de la
phase 4 gardent leur forme. Le repli « JSON imposé par prompt » est écarté.

Le modèle expose son raisonnement dans `reasoning_content` et
`provider_specific_fields.reasoning`, en plus de `tool_calls`. À prévoir au parsing :
`content` vaut `null` quand un outil est appelé — un client qui suppose une chaîne
plantera.

**Latence à surveiller :** 1,19 s pour un tour avec outils, contre 0,41 s chez Albert.
Sur une boucle agent à plusieurs itérations, c'est le poste dominant du budget des 10 s
de H3. À mesurer pour de vrai au lot L1.5, pas à extrapoler d'un appel unique.

### Arbitrage reranker — à trancher avant L4.1

SSPCloud ne sert pas de reranker ; Albert sert `bge-reranker-v2-m3`. Trois options :

| option | ce que ça coûte |
|---|---|
| **a. Bi-provider** — chat SSPCloud + rerank Albert | deux clés, deux dépendances externes, mais garde le gain le mieux documenté du pipeline RAG |
| **b. MMR seul** | une seule dépendance, mais perte de qualité de reranking — **à mesurer contre la référence L1.5, pas à supposer** |
| **c. Reranker local** | pas de dépendance externe ; coût RAM/CPU dans le pod, à chiffrer |

Aucune de ces options n'est retenue par défaut. **C'est une décision, elle va dans
`DECISIONS.md`.**

---

## Mesures GitHub — 22/08/2026 · H6 levée côté dépôt

| | |
|---|---|
| Token | PAT **fine-grained** (pas de `x-oauth-scopes`), compte `nic01asFr` |
| `nic01asFr/Colaig` | accessible, **droit de push confirmé** |
| Branche par défaut | **`Colaig_main`** — et non `main`. Le bootstrap crée un `main` local : le nom de la branche poussée doit être choisi explicitement, pas subi. |
| Visibilité | ⚠️ **dépôt PUBLIC** |

**Conséquence du caractère public :** tout ce qui est poussé est immédiatement lisible
par tous, et un historique git ne se rattrape pas par une suppression. Vérification faite
avant de pousser quoi que ce soit — les 16 commits de v3 ne contiennent **aucun** `.env`,
`.pem`, `.key` ni fichier de credentials. Seuls apparaissent du code (`auth/tokens.py`,
`security/secrets_filter.py`), un template Helm (`deploy/helm/colaig/templates/secret.yaml`)
et `config/.env.example`. **L'historique est propre, le push est sûr de ce point de vue.**

Reste que la publication en open source est une **porte humaine** (point 8 du cadrage :
licence retenue, autorisation Cerema). Pousser dans un dépôt déjà public revient de fait
à publier : à confirmer avant le premier push, pas après.

---

## Inventaire des providers de v3 — 22/08/2026

**Correction d'une affirmation erronée faite plus tôt dans la session.** Il avait été écrit
que « v3 n'a aucune variable WebDAV, son stockage est MSGraph/Box/local », et qu'il fallait
peut-être reformuler H3. **C'est faux.** L'absence de variables `WEBDAV_*` dans un `.env`
donné ne renseigne que sur *l'instance configurée par ce fichier*, pas sur les
implémentations disponibles. v3 est bien générique sur les trois axes :

| axe | sélecteur | valeurs acceptées | défaut |
|---|---|---|---|
| Stockage | `STORAGE_BACKEND` | `local`, **`webdav`**, `bigfolder`, `s3`, `msgraph`, `box`, `gdrive` | **`webdav`** |
| Messagerie | `MESSAGING_BACKEND` | `matrix`, `webchat`, `telegram`, `slack`, `none`/`noop` | `matrix` |
| LLM | `LLM_BACKEND` | `albert`, `openai`, `azure`, `ollama` | `albert` |

Fichiers correspondants : `colaig/integrations/storage/{local,webdav,bigfolder,s3,msgraph,box,gdrive}.py`,
`colaig/messaging/{matrix,webchat,noop}.py` + `colaig/integrations/messaging/telegram.py`,
`colaig/integrations/llm/{openai_client,azure_client,ollama_client,provider_registry,capability_chain}.py`.
La validation de configuration de `config.py` vérifie les champs requis backend par backend
et rejette un backend inconnu.

**Conséquences :**

1. **H3 tient telle qu'elle est formulée.** `probe_webdav.py` vise un backend réellement
   implémenté — et qui est même le défaut du code. Il ne manque que des credentials de test.
2. La bascule vers SSPCloud relève de `LLM_BACKEND=openai` + `LLM_API_URL`/`LLM_API_KEY`
   (endpoint OpenAI-compatible), **pas** d'un nouveau provider à écrire. Ce qui confirme D3
   et l'argument de D1 : le multi-provider est déjà construit, il n'est pas à refaire.
3. Le `.env` de v3 configurait `msgraph` — c'est un **choix d'instance**, pas une limite du
   code. Reste à établir quel backend l'instance de production utilise réellement, ce qui
   détermine où porter l'effort de mesure de latence.

---

## Stockage S3 SSPCloud (MinIO) — mesuré le 22/08/2026

**Question posée :** peut-on utiliser le stockage utilisateur SSPCloud comme backend ?

**Réponse courte : oui pour le développement et les tests, non en l'état pour la
production — et cela ne remplace pas H3.**

### Ce qui est acquis

| | |
|---|---|
| Backend | v3 implémente déjà `integrations/storage/s3.py` (boto3, compatible MinIO) |
| Sélection | `STORAGE_BACKEND=s3` |
| Variables | `S3_ENDPOINT_URL`, `S3_ACCESS_KEY`, `S3_SECRET_KEY`, `S3_BUCKET_NAME`, `S3_PREFIX`, `S3_REGION`, **`S3_SESSION_TOKEN`** |
| Endpoint Onyxia | `https://minio.lab.sspcloud.fr`, région `us-east-1` |

La présence de `S3_SESSION_TOKEN` dans la configuration de v3 montre que le cas des
credentials temporaires avait été prévu.

### Le fait qui décide — credentials non durables

Test d'aller-retour sur un pod Onyxia âgé de **8 h 38** :

```
ClientError: An error occurred (InvalidAccessKeyId) when calling ListBuckets:
The Access Key Id you provided does not exist in our records.
```

Les credentials sont pourtant bien chargés (`method=env`, session token présent) : ce
sont des jetons **STS temporaires**, et ils sont **déjà refusés en moins de neuf heures**.

**Conséquence directe :** une instance Colaig au long cours configurée sur les
credentials S3 injectés par Onyxia **tombera en panne d'authentification** sans qu'aucune
ligne de code n'ait changé. Le mode de défaillance est silencieux du point de vue du code
et déroutant à diagnostiquer — exactement le genre de panne qui a produit des versions
successives. À ne pas découvrir en production.

### Ce à quoi S3 SSPCloud sert, et ce à quoi il ne sert pas

**Il sert** — et cela débloque immédiatement le point 4 de la liste des blocages, sans
attendre les credentials WebDAV :
- tests de contrat `StorageProtocol` sur un backend distant réel plutôt que sur `LocalStorage` ;
- persistance de l'index et des artefacts entre deux pods ;
- terrain de mesure pour la référence L1.5.

**Mise à jour du 22/08/2026 — arbitrage rendu.** Le stockage SSPCloud **remplace** le
WebDAV comme stockage du chantier (voir D8 dans `DECISIONS.md`). H3 est donc reformulée :
elle porte désormais sur la latence de MinIO, mesurée par `scripts/probe_s3.py`.

Deux réserves subsistent et ne sont pas levées par cet arbitrage :

- **La durabilité des credentials** devient une hypothèse à part entière (**H3bis**), et
  elle est bloquante pour l'exploitation : jetons STS refusés en moins de neuf heures.
- **Un bucket du datalab n'est pas l'espace où les agents déposent leurs documents.**
  Le principe fondateur — « un espace de stockage + un dossier `.colaig` = une
  instance » — vise l'espace de travail réel des utilisateurs. Que le chantier se
  développe et se mesure sur S3 est un choix d'outillage ; il ne présume pas du backend
  d'un déploiement chez un tiers, et c'est précisément ce que `StorageProtocol` protège.
  `webdav.py` reste dans le tronc, testé au titre de L1.1.

**Pour un usage de production il faudrait** une clé non expirante (compte de service
SSPCloud, si le datalab en délivre) ou un mécanisme de renouvellement des jetons STS.
**Question ouverte : SSPCloud délivre-t-il des credentials S3 non expirantes ?** Sans
réponse, aucune valeur par défaut n'est supposée.

### Accès kubectl — limite constatée

Le MCP SSPCloud de cette session s'authentifie comme
`system:serviceaccount:user-nicolaslaval:jupyter-python-271780` et **ne peut pas atteindre
le namespace `user-nic01asfr`** où tourne `proj-colaig-refonte-jupyter-python-0` :

```
pods "proj-colaig-refonte-jupyter-python-0" is forbidden
```

Deux comptes Onyxia coexistent (`nicolaslaval`, `nic01asfr`). **À trancher :** sur lequel
le chantier travaille, car cela conditionne quel pod est pilotable depuis l'outillage.

---

## Vault SSPCloud — exploré le 22/08/2026 · H3bis reste ouverte, mais une voie apparaît

Accès fourni : `https://vault.lab.sspcloud.fr`, jeton de policy `onyxia-kv`.

| | |
|---|---|
| Validité du jeton | **2 764 642 s ≈ 32 jours**, `renewable: true` |
| Portée | `onyxia-kv/…/nicolaslaval/**` uniquement — la racine et `nic01asfr` renvoient 403 |
| Contenu | 17 secrets sous `nicolaslaval/.onyxia/` : préférences d'interface, `gitName`, `gitEmail`, `githubPersonalAccessToken`, `servicePassword`, `s3Profiles`, `s3BookmarksStr`, `restorableServiceConfigs` |

**Ce qui n'y est pas : les credentials S3.**

- `s3Profiles` → **liste vide**
- `s3BookmarksStr` → `null`
- `restorableServiceConfigs` → vide ; aucune occurrence de `accessKey`, `secretKey`,
  `sessionToken` ni `aws_access` dans l'ensemble du contenu.

**Explication et conséquence.** Onyxia ne stocke pas de credentials S3 : il les **frappe à
la demande** en échangeant le jeton OIDC de la session contre des credentials STS MinIO
(`AssumeRoleWithWebIdentity`). C'est pour cela qu'elles sont temporaires, et pour cela que
rien n'est mis en cache nulle part. **Sans jeton OIDC, aucun outillage ne peut en obtenir**
— ce qui referme définitivement la piste automatisée.

**La voie qui s'ouvre, pour H3bis.** `s3Profiles` est précisément l'emplacement prévu par
Onyxia pour un **profil S3 personnalisé** — c'est-à-dire des credentials MinIO qui ne
viennent pas du flux STS. Il est vide, mais il existe, et il est porté par un magasin
durable (jeton de 32 jours, renouvelable, portée limitée à l'utilisateur).

Le chemin vers un stockage exploitable est donc : obtenir des credentials MinIO durables
auprès du datalab → les enregistrer comme profil personnalisé → y pointer Colaig.
**La première étape reste une question au datalab, elle n'est pas contournable.**

**À noter pour L3.6, sans en faire une décision ici :** Vault est un magasin de secrets
durable et déjà disponible dans l'environnement cible. Il pourrait porter les credentials
d'une instance sans contredire le principe « zero database » — Vault est un service de
l'environnement de déploiement, au même titre que l'API LLM, pas une dépendance interne
de Colaig. À arbitrer au lot du chart Helm, pas avant.

---

## Credentials S3 SSPCloud — réponse documentée, 22/08/2026 · **H3bis levée**

Recherche menée après l'échec de toutes les pistes automatisées. La documentation
SSPCloud tranche la question, et elle décrit **deux mécanismes distincts** qu'il ne faut
pas confondre.

### 1. Jeton personnel — le mécanisme par défaut, celui qui expirait

- **Valide 7 jours**, régénéré automatiquement à échéances régulières.
- Pré-injecté en variables d'environnement dans les services lancés **depuis le Datalab**.
- À l'expiration, les services créés avant la date de péremption **perdent l'accès au
  stockage** et apparaissent **marqués en rouge** dans « Mes services ».
- Renouvellement : page **`datalab.sspcloud.fr/account/storage`**, qui fournit des
  scripts prêts à l'emploi pour R, Python et le terminal.

**Ceci explique exactement ce qui a été mesuré** : `InvalidAccessKeyId` sur un pod de
8 h 38, parce que le jeton dont il avait hérité avait été régénéré entre-temps. Ce n'est
pas une anomalie, c'est le fonctionnement nominal.

**Et cela confirme le second constat :** les pods lancés par l'outillage MCP ne passent
pas par le Datalab, donc ne reçoivent **aucune** injection. Vérifié sur un pod de 48 s.

### 2. Compte de service — le mécanisme pérenne

- Couple **(access key, secret access key) permanent**, sans expiration.
- Rattaché à un **projet**, pas à une personne — donc il survit au départ d'un agent.
- Créé depuis la console **`minio-console.lab.sspcloud.fr`**.
- La documentation le désigne explicitement pour « les traitements périodiques ou **le
  déploiement d'applications** » — c'est-à-dire précisément le cas de Colaig.

### Conséquences pour le chantier

| besoin | mécanisme | où |
|---|---|---|
| Mesurer H3 maintenant | jeton personnel (7 j) | `datalab.sspcloud.fr/account/storage` |
| Faire tourner une instance | **compte de service** | `minio-console.lab.sspcloud.fr` |

**H3bis est levée : une option non expirante existe et est documentée.** L'inquiétude
soulevée en D8 — « une instance tombera en panne d'authentification » — est réelle mais
**évitable**, à condition de ne pas déployer sur un jeton personnel. À inscrire comme
contrainte du lot L3.6 : le chart Helm doit consommer un compte de service, jamais un
jeton hérité d'une session.

Sources : [Stockage de données — docs.sspcloud.fr](https://docs.sspcloud.fr/content/storage.html),
[S3 Configuration — docs.onyxia.sh](https://docs.onyxia.sh/admin-doc/s3-configuration).


---

## Mesures sur corpus réaliste — 23/08/2026

Corpus **privé** monté dans `nicolaslaval/colaig-mesure-sst/` : 59 documents, 43,8 Mo,
51 PDF, arborescence à 14 sous-dossiers. Bucket vérifié privé avant dépôt (aucune
politique de bucket, `GET` anonyme → 403). **Ce corpus n'est jamais commité — seuls les
chiffres le sont.**

| opération | médiane | échantillon |
|---|---|---|
| LIST non récursif | **31 ms** | 3 mesures |
| **LIST récursif, espace entier** | **47 ms** | 6 mesures, 63 objets |
| GET | 31–47 ms | |
| PUT / DELETE | 47 ms / 31 ms | |
| Dépôt initial | 43,8 Mo en 17,1 s | 2,6 Mo/s depuis le poste |

### H3ter — levée à cette échelle, pas au-delà

Le listing récursif est l'opération qui faisait exploser les timeouts de la version
déployée. À 59 documents il coûte **47 ms** : le seuil de 10 s au-delà duquel il faudrait
l'interdire dans le code n'est pas approché. **Mais 59 documents restent peu.** Ce qui
est établi, c'est que le mécanisme n'est pas intrinsèquement lent ; ce qui reste inconnu,
c'est son comportement sur un espace de plusieurs milliers de documents. À ne pas
extrapoler.

### H5 — corrigé : l'index pèse ~3 fois le corpus, pas dix

> **Correction du 23/08/2026.** Ce qui suit dimensionnait sur `qwen3-vl-embedding-8b`
> (4096), parce que la sonde prenait le premier modèle d'embedding du catalogue. Or
> `colaig-v3/.env` configure `BAAI/bge-m3`, mesuré à **1024**. À 1024 l'index estimé
> tombe à **~120 Mo** pour 44 Mo de corpus, et non 479 Mo. Le raisonnement ci-dessous
> reste valable en tant que borne haute, et la décision est en **D10**.

59 documents, 43,8 Mo de source. En estimant ~1500 octets de texte utile par chunk :
**~29 000 chunks**, soit **~479 Mo d'index** en float32.

Le facteur est la dimension. Les deux endpoints réels servent du **4096** — 16 Ko par
vecteur, contre 4 Ko pour un `bge-m3` à 1024. **Un corpus de 44 Mo produit donc un index
d'un demi-gigaoctet.** À dix espaces de cette taille, on parle de 5 Go en mémoire pour
un `IndexFlatIP`, ce qui n'est plus une décision d'implémentation mais de dimensionnement
du pod.

C'est une **estimation**, pas une mesure : le nombre réel de chunks dépend du découpage,
qui dépend du format — un PDF scanné passe par l'OCR, un Markdown se coupe aux titres.
À confirmer par une indexation réelle. Mais l'ordre de grandeur suffit à poser la
question maintenant plutôt qu'au lot L4.1.

### Deux défauts de la sonde, corrigés en produisant ces chiffres

1. **Elle mesurait le mauvais dossier.** Le listing récursif portait sur le *premier*
   sous-préfixe rencontré — qui, sur un espace Colaig, est `.colaig/` : quatre fichiers
   de configuration. Elle annonçait donc un « LIST récursif » de 47 ms qui n'avait
   parcouru ni les documents ni l'arborescence. Un chiffre faux ayant l'apparence d'une
   mesure. Elle liste désormais l'espace entier.
2. **Elle ne mesurait qu'une fois.** Le premier appel porte l'établissement TLS : la
   première lecture donnait **1094 ms**, ce qui franchissait le seuil « > 1 s → index
   local persistant requis » et aurait fait conclure l'inverse de la vérité. Six mesures
   consécutives donnent 360 ms puis 62, puis 47 quatre fois. Médiane de trois désormais,
   comme les autres lignes.

Même erreur que le PUT à 437 ms deux jours plus tôt. **Un chiffre unique ne vaut pas
mesure** — c'est maintenant appliqué à toutes les lignes de la sonde.

---

## H5 — **mesurée**, 23/08/2026 · l'estimation était fausse d'un facteur 28

Indexation réelle du corpus SST avec le **vrai** `extract_text()` et le **vrai**
`Chunker` de Colaig (`chunk_size=800`, `chunk_overlap=100`).

| | estimé | **mesuré** |
|---|---|---|
| chunks | ~29 000 | **1 059** |
| index à 4096 | 479 Mo | **17 Mo** |
| index à 1024 | 120 Mo | **4 Mo** |

### Pourquoi l'estimation était fausse

Elle divisait le poids du corpus par ~1500 octets de texte par chunk. **Elle supposait
donc qu'un PDF est du texte.** Il ne l'est pas : 42,6 Mo de PDF ont produit **0,61 Mo de
texte extrait**, soit **1,4 %**. Le reste est de l'image, des polices, de la structure.

**On ne peut pas estimer un nombre de chunks à partir d'une taille de fichier.** C'est
une erreur de méthode, pas un écart de calibrage — et elle allait dans le sens du
catastrophisme, ce qui la rendait d'autant plus crédible.

| format | docs | Mo source | Mo texte extrait | chunks |
|---|---|---|---|---|
| pdf | 51 | 42,6 | 0,61 | 1011 |
| odt | 7 | 0,2 | 0,03 | 48 |
| png | 1 | 1,0 | 0,00 | 0 |

Chunks : médiane 789 caractères, moyenne 656, max 1996.
Extraction des 59 documents : **1,0 s**. Découpage : négligeable.

### H5 est levée, et largement

1 059 chunks contre un seuil de ~100 000 pour `IndexFlatIP`. Il faudrait **cent espaces
de cette taille** pour l'approcher. La recherche exacte en O(n) n'est pas un problème à
cette échelle, et le débat sur l'index approché ne se pose pas.

### Ce que cela fait à D10

**L'argument mémoire de D10 s'effondre.** J'y écrivais « à 4096, dix espaces de 44 Mo
font 5 Go d'index ». Mesuré, c'est **170 Mo**. La différence entre 4096 et 1024 sur ce
corpus est de 17 Mo contre 4 Mo — dérisoire des deux côtés.

Le choix de la dimension **ne se décide donc plus sur la mémoire, mais sur la seule
qualité de restitution**. La décision reste suspendue à L1.5, mais pour une autre raison
que celle que j'avais donnée. D10 est amendée en conséquence.

### Le vrai problème est ailleurs : 29 % du corpus est invisible

**8 documents sur 59 ne produisent aucun texte** — 7 PDF et 1 PNG, soit **17,2 Mo,
29 % du poids du corpus**. Ce sont des documents scannés.

Sans OCR, ils ne génèrent aucun chunk : ils sont **absents de la recherche**, et rien
dans l'interface ne le signale. Sur un corpus de santé et sécurité au travail, cela veut
dire que la fiche réflexe dont quelqu'un a besoin peut être précisément celle qui est un
scan — et l'assistant répondra qu'il n'a rien trouvé, ou pire, répondra à partir d'un
document voisin.

**L'OCR n'est donc pas une option de confort sur ce type de corpus.** Le catalogue Albert
sert `lightonocr-2-1b`, SSPCloud sert `chandra-ocr-2`, et `albert.py` implémente déjà un
chemin OCR. À inscrire comme exigence, pas comme amélioration.

**Et il manque un signalement.** Qu'un document soit indexé à zéro chunk devrait être
visible — dans un rapport d'indexation, et à l'utilisateur qui demande ce que contient
l'espace. Silencieusement absent est le pire état possible.
