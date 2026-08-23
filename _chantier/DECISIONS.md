# Décisions structurantes (ADR)

Chaque décision est datée, justifiée, et porte son coût. Une décision ne se rouvre que
par une nouvelle entrée, jamais par modification de l'ancienne.

---

## D1 — Le tronc est `colaig-v3` · 22/08/2026 · **actée**

**Contexte.** Deux candidats : `Plateforme_colaig` (déployée, branche
`claude/refactor-colaig-tchap-bot-qRqkF`, dernier commit 21/06/2026) et `colaig-v3`
(locale, sans remote, branche `feat/reflexive-self-config`, dernier commit 21/06/2026).

**Décision.** `colaig-v3`.

**Justification.**
- Package `colaig/` sans l'héritage `app/` d'albert-tchap (`iam.py` Grist mort,
  `_grist_legacy.py`, `browser_use` vendoré).
- `protocols.py` : contrat explicite entre modules — **condition sine qua non** pour
  paralléliser des sous-agents sans produire une 17e architecture.
- `colaig_index.py` : source unique des chemins → règle le problème racine du double
  marqueur `.albert`/`.colaig`.
- ~70 fichiers de test couvrant le chemin critique (resolver, retriever hybride, trame,
  pre-execution, security, ACL, serveur MCP).
- RAG une génération au-dessus (BM25+RRF, MMR, rerank cross-encoder, contextual chunking,
  etags incrémentaux).
- **Multi-provider LLM déjà construit** (`provider_registry`, `capability_chain`,
  `openai_client`) → SSPCloud entre nativement (cf. D3).
- **Plateforme adaptée à la portée visée** (cf. D4) : `clients.yml` + `platform_policy` +
  package ZIP self-hosted, contre un SQLite stockant les credentials en clair côté PROD.

**Coût accepté.** Porter 6 briques de PROD : scoring de binding, fils Matrix + réactions,
client MCP, filtrage d'outils, chart Helm Onyxia, classement de PJ. Coût très inférieur au
portage inverse, qui exigerait en plus de déraciner `app/`.

---

## D2 — `Plateforme_colaig` gelée · 22/08/2026 · **actée**

Maintenance de sécurité uniquement jusqu'à parité fonctionnelle, puis archivage en lecture
seule. `colaig-0` continue de tourner dessus sans interruption pendant tout le chantier.

---

## D3 — LLM de production : SSPCloud · 22/08/2026 · **actée**

`https://llm.lab.sspcloud.fr/api`, endpoint OpenAI-compatible, via `provider_registry`.
Albert API devient un provider optionnel (souveraineté), pas une exclusivité.
La doctrine « Albert uniquement » du `CLAUDE.md` de v3 est **supprimée** au lot L0.3.

**Dépendance ouverte :** H1 (le tool calling est-il supporté par le modèle servi ?).
Si non → plusieurs lots de phase 4 changent de nature. **À lever avant tout code.**

---

## D4 — Portée : interministériel **et** auto-hébergeable · 22/08/2026 · **actée**

Conséquences directes :
- pas de SQLite ni de credentials en clair → `clients.yml` + secrets Kubernetes ;
- `platform_policy` (backends, endpoints LLM et serveurs MCP autorisés) validée au
  démarrage ;
- package ZIP self-hosted (docker-compose + .env) généré par `ClientProvisioner` ;
- le dépôt doit être **publiable** : ni wheel vendoré, ni module mort, ni double
  convention de chemins, ni donnée nominative.

---

## D5 — Dépôt unique · 22/08/2026 · **actée**

`github.com/nic01asFr/Colaig`, branche par défaut `main`.
`Colaig_main` et `claude/refactor-colaig-tchap-bot-qRqkF` archivées après import.
Les 15 dépôts antérieurs passent en lecture seule une fois les briques extraites.

---

## D6 — Mode C interne vs extension `Tasks` MCP · **reportée au lot L5.1**

À arbitrer après lecture de la spec `Tasks` de MCP 2026-07-28. Ne pas réimplémenter un
standard si l'extension couvre le besoin des tâches planifiées.

---

## D7 — Pod de développement séparé · 22/08/2026 · **actée**

Le développement se fait sur un pod dédié du namespace `user-nic01asfr`.
**`colaig-0` n'est jamais touché.** Les tests Tchap se font sur un compte bot et un salon
distincts de la production.

---

## D7bis — Amendement de D7 : namespace de développement · 22/08/2026 · **actée**

D7 désignait le namespace `user-nic01asfr`. **Le chantier se fait sur
`user-nicolaslaval`.** Motif : l'outillage de pilotage (MCP SSPCloud) s'authentifie comme
`system:serviceaccount:user-nicolaslaval:...` et ne peut pas atteindre `user-nic01asfr` —
`pods ... is forbidden`. Travailler dans un namespace non pilotable revient à travailler
à l'aveugle.

Le reste de D7 est inchangé : **`colaig-0` n'est jamais touché**, et les tests de
messagerie se font sur un compte et un salon distincts de la production.

---

## D8 — Le stockage du chantier est le stockage S3 SSPCloud · 22/08/2026 · **actée**

**Décision.** Le backend de stockage utilisé pour le développement, les tests de contrat
et les mesures est le **stockage utilisateur SSPCloud (MinIO)**, `STORAGE_BACKEND=s3`.
Il remplace l'espace WebDAV de test qui était attendu.

**Ce que ça change.**

- **H3 est reformulée** : elle porte sur la latence de MinIO, plus sur celle du WebDAV
  Bnum. Sonde : `scripts/probe_s3.py`, qui remplace `probe_webdav.py`.
- Le chantier n'est **plus bloqué** par l'attente de credentials WebDAV — c'était l'un
  des cinq blocages ouverts.
- `probe_webdav.py` est **conservé** et marqué déprécié : `webdav.py` reste une
  implémentation du tronc, testée au titre de L1.1.

**Ce que ça ne change pas.**

- `StorageProtocol` reste la seule interface. Le choix d'un backend pour le chantier
  n'est pas un choix d'architecture : les sept implémentations restent au contrat de
  L1.1. C'est précisément ce que le Protocol protège.
- Un bucket du datalab **n'est pas** l'espace où les agents déposent leurs documents.
  Le principe fondateur vise leur espace de travail réel. Le choix de S3 est un choix
  d'outillage de chantier, il ne présume pas du backend d'un déploiement chez un tiers.

**Ce que ça coûte — à ne pas perdre de vue.**

Les credentials S3 injectées par Onyxia sont des **jetons STS temporaires**, mesurés
refusés en moins de neuf heures (`InvalidAccessKeyId` sur un pod âgé de 8 h 38). Une
instance au long cours branchée dessus tombera en panne d'authentification sans qu'une
ligne de code ait bougé, et la panne est silencieuse côté code.

C'est suivi comme **H3bis**, bloquante pour l'exploitation. Question ouverte au datalab :
délivre-t-il des credentials S3 non expirantes (compte de service) ? **Aucune valeur par
défaut n'est supposée tant que la réponse manque.**

---

## Point de vigilance sur D5 — branches distantes

D5 prévoit d'archiver `Colaig_main` et `claude/refactor-colaig-tchap-bot-qRqkF` après
import. **Cela n'a pas été fait, et ne le sera pas sans arbitrage explicite** : consigne
a été donnée de ne pas supprimer le travail existant. Le dépôt public contient une
génération antérieure de Colaig, sous Licence Ouverte 2.0, et ses cinq branches sont
intactes. Le tronc a été poussé sur `chantier/tronc-unique`, en ajout.

D5 dit aussi « branche par défaut `main` » : la branche par défaut réelle est
**`Colaig_main`**. Changer la branche par défaut est une opération visible et
difficilement réversible — elle relève d'un arbitrage, pas d'un effet de bord de lot.

---

## D9 — Aucune restriction d'endpoint LLM par défaut · 23/08/2026 · **actée**

**Question posée :** `platform_policy.allowed_llm_endpoints` doit-il restreindre les
endpoints LLM par défaut ? Arbitrage délégué.

**Décision : non.** La liste reste vide par défaut. Colaig n'impose aucun endpoint.

**Pourquoi.**

1. **Cela casserait D4.** Le dépôt vise une portée interministérielle **et
   auto-hébergeable**. Une liste blanche par défaut interdirait d'emblée un Ollama sur
   `localhost`, un Azure privé, un endpoint ministériel interne — c'est-à-dire
   exactement les cas que D4 protège.
2. **`platform_policy` est par construction une contrainte d'*opérateur*.** Vide = pas
   d'opérateur de plateforme = pas de contrainte. Y mettre un défaut reviendrait à ce
   que Colaig décide de la souveraineté du déploiement de quelqu'un d'autre.
3. **Un défaut donnerait une fausse sécurité.** Qui exécute le code peut l'éditer. La
   souveraineté est une propriété de déploiement, pas une propriété de code. Une liste
   blanche embarquée rassurerait sans rien garantir.

**Le contrepoids — deux obligations en échange.**

Ne pas restreindre n'autorise pas à être négligent. Deux choses ont donc été faites au
même lot :

- **Le contrôle, quand il est posé, doit tenir.** Il était implémenté par
  `url.startswith(autorise)`, et laissait passer
  `https://llm.lab.sspcloud.fr.attaquant.example/v1` — un opérateur croyait restreindre
  son parc au datalab alors qu'un suffixe de domaine suffisait à envoyer les
  conversations ailleurs. Remplacé par `config.endpoint_autorise()` : comparaison exacte
  du schéma et de l'autorité, chemin sur frontière de segment. 17 tests, dont tous les
  contournements ci-dessus.
- **L'endpoint effectif est tracé au démarrage.** `main.py` journalise
  `LLM : backend=… endpoint=…`. Si Colaig ne décide pas où partent les conversations,
  l'exploitant doit au minimum le voir.

**Ce que cela n'exclut pas.** Un opérateur de plateforme qui veut contraindre son parc
renseigne `allowed_llm_endpoints` dans `clients.yml` — le mécanisme existe, il est
testé, et il est désormais solide. C'est là que la souveraineté se décide.

---

## D10 — Dimension d'embedding : 1024 par défaut, en flag · 23/08/2026 · **proposée**

**Question posée :** faut-il des embeddings en 1024 plutôt qu'en 4096 ?

**Réponse : oui, très probablement — mais en flag, et tranché par la mesure à L1.5.**

### Correction préalable

L'estimation de H5 à 479 Mo portait sur `qwen3-vl-embedding-8b` (4096), parce que la
sonde prenait le **premier** modèle d'embedding du catalogue. Or `colaig-v3/.env`
configure `ALBERT_MODEL_EMBED=BAAI/bge-m3`, mesuré à **1024**. Le chiffre annoncé ne
correspondait donc pas à la configuration réelle.

### Ce qui est mesuré

| dimensions | top-1 correct | accord avec 4096 | Spearman | index estimé (corpus 44 Mo) |
|---|---|---|---|---|
| 4096 | 6/6 | — | 1,0000 | 479 Mo |
| 2048 | 6/6 | 6/6 | 0,9819 | 239 Mo |
| **1024** | 6/6 | 6/6 | 0,9546 | **120 Mo** |
| 512 | 6/6 | 6/6 | 0,9376 | 60 Mo |

**Le top-1 est parfait à toutes les dimensions, y compris 512 : l'échantillon est trop
facile pour discriminer.** Six questions et dix documents ne prouvent rien. Le signal
utile est le Spearman, qui se dégrade régulièrement — c'est le classement **fin** qui
souffre, et c'est lui qui compte quand des dizaines de chunks se ressemblent.

### Trois façons d'obtenir 1024, à ne pas confondre

| voie | comment | ce que ça coûte |
|---|---|---|
| **a. Troncature Matryoshka** | tronquer `qwen3-embedding-8b` à 1024 puis renormaliser L2 | reste sur SSPCloud (D3), aucun provider ajouté. On paie le calcul du 4096, on n'économise que l'index. **Le paramètre `dimensions` est refusé côté serveur** (`litellm.UnsupportedParamsError`) : la troncature est donc **côté client**. |
| **b. `bge-m3` chez Albert** | modèle **nativement** entraîné en 1024 | vraisemblablement meilleur qu'un 4096 tronqué à taille égale — mais Albert n'est pas la cible de production, donc bi-provider, comme pour le reranker |
| **c. Embeddings locaux** | `llm.localEmbeddings=true`, déjà prévu dans le chart Helm | aucune dépendance externe pour l'indexation ; coût RAM/CPU dans le pod, à chiffrer |

Une troncature d'un modèle 4096 et un modèle nativement 1024 **ne sont pas équivalents**,
et rien dans ce qui est mesuré ici ne permet de les départager.

### Décision

1. La dimension devient un **paramètre de configuration**, défaut **1024**.
2. Le choix entre (a), (b) et (c) est **suspendu à la référence L1.5**. Trancher
   maintenant, c'est décider au jugé — précisément ce que le principe 6 interdit.
3. L'estimation d'index est recalculée à **~120 Mo pour 44 Mo de corpus**, et reste une
   **estimation** : le nombre de chunks dépend du découpage, donc du format. À confirmer
   par une indexation réelle.

**Ce qui rendait la question importante — et qui s'est révélé faux le jour même.**

J'écrivais ici : « à 4096, dix espaces de 44 Mo font 5 Go d'index en mémoire ». C'était
fondé sur une **estimation** du nombre de chunks, elle-même fondée sur le poids des
fichiers. L'indexation réelle donne **1 059 chunks**, pas 29 000 : un PDF n'est pas du
texte, et 42,6 Mo de PDF n'ont produit que 0,61 Mo de texte extrait.

Mesuré, dix espaces de cette taille font **170 Mo**, pas 5 Go. Sur ce corpus, l'écart
entre 4096 et 1024 est de **17 Mo contre 4 Mo**.

**L'argument mémoire tombe donc.** Le choix de la dimension ne se décide plus sur
l'empreinte, mais sur la **seule qualité de restitution**. La décision — 1024 par défaut,
en flag, tranchée à L1.5 — reste inchangée ; sa justification, non. Voir `HYPOTHESES.md`,
section « H5 — mesurée ».

---

## D11 — Sources synchronisées : le mode se déclare par source · 23/08/2026 · **actée**

**Question posée :** tout ou partie du corpus interrogeable pourrait-il venir d'une
source tenue à jour, plutôt que de fichiers déposés une fois ?

**Réponse : oui, mais en déclarant un mode par source — et jamais pour un espace de
mesure.**

### Deux besoins opposés, tous deux non négociables

| | corpus de **mesure** | corpus d'**exploitation** |
|---|---|---|
| exigence | **figé** | **à jour** |
| pourquoi | un article modifié rend une réponse attendue fausse **sans qu'aucun test n'échoue** : la référence dérive en silence | un assistant qui cite un article abrogé est nuisible |
| mécanisme | instantané épinglé + manifeste d'empreintes | synchronisation périodique |

Ce n'est donc pas un choix global. Un espace déclare `source_mode: fige` ou
`synchronise`. **Règle dure : un espace en mode synchronisé ne peut pas servir d'espace
de mesure.** À faire respecter par un test, pas par la discipline.

### Pour une source versionnée, le web n'est pas le bon tuyau

Mesuré sur `AgentPublic/legi` : **24 instantanés publiés, un tous les 14 jours** — sept
intervalles consécutifs de 14 jours exactement.

Se synchroniser sur un jeu versionné donne ce qu'un scraping ne donnera jamais : un
**numéro de version citable**, un diff entre deux états, une licence, et pas de page à
parser. Accessoirement, `legifrance.gouv.fr` et `economie.gouv.fr` renvoient tous deux
**403** à toute récupération automatique — vérifié.

Le web reste utile pour ce qui n'est pas versionné. Il ne doit pas être le mécanisme par
défaut d'une source qui l'est.

### Ce que cela impose, au vu de l'histoire du projet

**Trois des six anti-patrons consignés viennent du sous-système web.** Ce n'est pas une
coïncidence, et toute reprise doit y répondre nommément :

1. **Aucun repli génératif.** `extract_with_llm_summary` demandait au LLM d'**imaginer**
   le contenu d'une page inaccessible et le présentait comme un extrait. Une source
   inaccessible est une **erreur**, jamais une invite. Sur du droit, une page inventée
   présentée comme du Légifrance produit une procédure irrégulière.
2. **Un cache est lu, ou n'existe pas.** `web_search_cache.set()` sans `.get()`.
3. **Les unités de découpage sont cohérentes.** `chunk_size` en caractères et `overlap`
   en mots : 60 % de recouvrement, embeddings payés 2,5×.

Et une quatrième, propre à cette décision :

4. **La version doit être citable.** Si l'assistant cite `L2113-10`, il doit pouvoir dire
   **depuis quel instantané**. Sinon « à jour » est une affirmation invérifiable — et
   invérifiable, sur du droit, vaut faux.

### Architecture

Le synchroniseur **écrit dans l'espace via `StorageProtocol`**. Aucun nouveau Protocol :
l'indexation incrémentale par etags — mesurée à 47 ms sur 63 objets — détecte les
changements et ne réindexe que le nécessaire. La brique existe et elle est éprouvée.

### Calendrier

Rattaché au lot **L5.6** déjà prévu (« Web externalisé sur `webtools` MCP ; conserver la
logique de fraîcheur »). **Pas avant L1.5.** Une source qui bouge avant que la référence
existe est exactement la faute que le plan interdit : on ne saurait plus si une variation
de qualité vient du pipeline ou du corpus.

---

## D12 — Stratégie de découpage déclarée par espace · 23/08/2026 · **actée**

**Décision.** La stratégie de découpage devient un paramètre d'espace. Pour un corpus
**structuré en articles**, la stratégie `article` — un chunk par article, préfixé du
titre du document et de sa position dans le code — est retenue. Le découpage par
fenêtre glissante reste le défaut pour tout le reste.

### Sur quoi elle se fonde

Mesuré contre la référence L1.5, sur 39 cas dorés :

| | `Chunker(800,100)` | `article` |
|---|---|---|
| récupération complète | 28/39 — 72 % | **32/39 — 82 %** |
| échecs totaux | 7 | **4** |
| chunks | 2 124 | 1 762 |
| index | 8,7 Mo | 7,2 Mo |

Meilleur sur les deux indicateurs, avec un index 17 % plus petit.

### Ce que le chemin de cette décision enseigne

**La même mesure, faite à 17 cas, ne permettait pas de conclure** : +3/−1, en dessous du
seuil de signification que la référence s'était fixé. J'ai refusé de trancher, porté le
jeu doré de 20 à 45 cas, et rejoué à l'identique.

La modification n'avait pas changé. La capacité à en juger, si.

C'est le premier arbitrage du chantier rendu par la mesure plutôt que par l'intuition —
et le fait qu'il ait fallu s'abstenir une première fois en fait la démonstration, pas
l'exception.

### Ce que la décision ne dit pas

**Elle ne vaut que pour un corpus à structure explicite.** La stratégie `article`
s'appuie sur des marqueurs `## Article` ; sur le corpus SST — 51 PDF sans structure
déclarée — elle n'a aucun sens. D'où un paramètre d'espace, et non un changement de
défaut global.

**Elle ne clôt pas la question du découpage.** La régression observée à 17 cas sur un
article court a mis au jour une cause plus profonde — l'écrasement des scores denses,
voir `docs/diagnostic-dispersion-20260823.md`. Un découpage par article **enrichi de ses
voisins immédiats** reste à éprouver, et se mesure de la même façon.
