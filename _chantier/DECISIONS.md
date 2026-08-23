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

---

## D13 — Colaig n'est rattaché à aucune organisation dans le dépôt · 23/08/2026 · **actée**

Le dossier de chantier mentionnait une « autorisation de publication Cerema », et
`docs/ARCHITECTURE.md` intitulait une partie « INTÉGRATIONS AVANCÉES (CEREMA) ».
**C'est faux, et retiré.**

Ce n'est pas un détail de forme. D4 pose une portée **interministérielle et
auto-hébergeable** : le dépôt est public, et un lecteur d'un autre ministère qui y lit le
nom d'un organisme en déduit que le projet est le sien. Cela décourage la reprise, qui
est précisément l'objet.

La porte de publication demeure — licence retenue, autorisation obtenue — sans nommer
d'organisation.

Les mentions restantes dans `docs/CLAUDE.v3-original.md` ne sont pas touchées : ce
fichier est une **archive** datée, conservée telle quelle, et porte déjà une bannière
« NE PAS SUIVRE ».

---

## D14 — Un test rouge pour une raison d'environnement est un test à réparer · 23/08/2026 · **actée**

Un `pytest` nu sur un dépôt sain sortait **41 échecs** : `tests/test_live.py` interroge un
service HTTP en écoute, absent d'une machine de développement. Ces tests portent le
marqueur `live`, mais rien ne les désélectionnait par défaut.

Ils **skippent** désormais quand rien n'écoute, avec le motif et l'action. `pytest -m live`
exprime toujours l'intention de les exécuter.

Ce n'est pas de la cosmétique. Une suite dont on sait qu'elle est rouge « pour de
mauvaises raisons » cesse d'être lue, et le jour où un vrai défaut s'y ajoute personne ne
le voit. C'est la cinquième fois dans ce chantier qu'un contrôle est vert — ou rouge —
pour la mauvaise raison. **Effet de bord mesurable :** la suite passe de 195 s à 26 s,
parce que 41 tests n'attendent plus un délai réseau.

## D15 — Le chiffrement du backend Matrix est un prérequis, pas une option · 23/08/2026 · **actée**

`requirements.txt` déclare `matrix-nio[e2e]` depuis toujours. L'extra apporte
`python-olm`, qui se compile contre **libolm** ; sous Windows aucune roue n'est publiée,
l'installation de l'extra échoue **sans empêcher `matrix-nio` de s'installer**. On
obtient un environnement où la dépendance paraît satisfaite et où le chiffrement est
absent — jusqu'à la première connexion, qui lève un `ImportWarning` remonté de
`nio/client/base_client.py`. Il ne nomme ni le paquet, ni la bibliothèque système, ni la
plateforme.

`matrix.py::_exiger_e2e()` lève désormais une `MessagingError` qui nomme les trois, et
dit pourquoi couper `encryption_enabled` n'est pas une échappatoire : **sur Tchap, tous
les salons sont chiffrés**, un client sans olm démarrerait et ne lirait aucun message.
C'est une panne bien plus coûteuse à diagnostiquer que l'erreur d'origine.

Découvert en tentant de lever le `skip` de `run()` : la vérification s'est arrêtée avant
d'atteindre le réseau, sur une erreur qui ne disait pas quoi faire.

## D16 — La référence de recherche était sous-estimée · 23/08/2026 · **actée**

Remesure après unification du reconnaisseur de références (voir le commit du même jour) :

| | avant | après |
|---|---|---|
| tous les articles attendus remontés | 32/39 — 82 % | **34/40 — 85 %** |
| aucun remonté | 4 | **3** |

`mp-022` comptait comme un échec de recherche : `R2182-1` **était** dans les passages,
sous sa forme pointée, que l'ancien motif ne reconnaissait pas. Le cas était bon, la
mesure était fausse.

Le dénominateur passe de 39 à 40, et les négatifs de 8 à 7, pour une autre raison :
`mp-044` avait été requalifié en cas mixte — il exigeait un refus global alors que la
moitié de la question est répondable, et pénalisait donc le comportement correct.

**Ce que cette correction change pour la suite :** rien dans les conclusions de fond —
le découpage par article reste supérieur au découpage glissant, et la dispersion reste
prédictive de l'échec. Ce qu'elle change, c'est le statut du chiffre : 85 % n'est pas
« mieux que 82 % », c'est **la même mesure enfin juste**. Les deux ne se comparent pas.

## D17 — Le corpus de référence omettait le droit applicable · 23/08/2026 · **actée**

Deux défauts dans `construire_corpus_mp.py`, trouvés en étendant le jeu doré.

**1. Le filtre par statut écartait des articles en vigueur.** La requête retenait
`status = 'VIGUEUR'`. Or LEGI distingue les effets différés :

- `VIGUEUR_DIFF` — version **entrée en vigueur à effet différé**. 26 articles au 23/08/2026 ;
- `ABROGE_DIFF` — abrogation **à effet différé**. 18 articles restaient applicables.

Le cas décisif est **`R2152-7`, qui définit les critères d'attribution** — la question la
plus centrale pour quelqu'un qui rédige. Il existe en deux versions : l'ancienne abrogée
au 21/08/2026, la nouvelle en vigueur depuis cette même date. Le filtre écartait les
deux. Le corpus ne pouvait donc pas répondre sur les critères d'attribution, alors que
d'autres articles du corpus y renvoient explicitement.

Trouvé par une mesure, pas par relecture : sur 610 articles du CCP cités **à l'intérieur**
du corpus, 13 n'y figuraient pas. Un renvoi qui ne résout pas est le signal.

La règle est désormais temporelle — `start_date <= DATE_REFERENCE < end_date`, hors
`MODIFIE_MORT_NE` — avec une `DATE_REFERENCE` **épinglée** au même titre que l'instantané.
Un corpus dont le périmètre dépend du jour de son exécution n'est pas une référence.

**Résultat : 1806 articles au lieu de 1762. 44 ajoutés, aucun retiré.**

**2. Les articles longs étaient tronqués.** La requête gardait `chunk_index = 1`, soit le
**premier fragment seulement**. 53 articles étaient coupés en pleine phrase — et ce sont
les plus longs, donc les plus substantiels. Mesuré sur `L2511-7` : le fragment 1
s'arrêtait sur « au moins 80 % de son chiffre », le fragment 2 reprenait sur
« d'affaires ». Les fragments sont contigus, sans recouvrement ; ils sont désormais
recollés par une espace simple.

Aucune réponse attendue du jeu doré n'était fausse de ce fait — un seul cas s'appuyait sur
un article tronqué, et sur sa partie visible. Mais l'instrument était amputé de 4,1 %,
précisément là où le texte est le plus dense.

**Ce que cela invalide.** Les références de mesure publiées portaient sur l'ancien corpus.
La recherche a été remesurée : **88/103 cas complets, 85 %** — le taux tient sur un
échantillon presque triple (103 cas contre 40), ce qui était le seul moyen de savoir s'il
tenait. La référence de génération reste à refaire.

**Ce que la remesure révèle.** Les 11 échecs complets ne sont pas répartis au hasard :
plusieurs opposent le **vocabulaire du praticien** à celui du code. « Rendre le CCAG
applicable » ne remonte pas `R2112-2`, qui parle de « documents généraux » et écrit
« cahiers des clauses administratives générales » en toutes lettres — l'acronyme ne figure
nulle part. Idem pour « déroger dans mon CCAP » et `R2112-3`. C'est un angle mort que
l'ancien jeu doré, trop petit, ne pouvait pas montrer.

## D18 — Couper le raisonnement du modèle, et laisser le garde-fou rattraper · 23/08/2026 · **actée**

27 à 33 % des réponses étaient coupées à `max_tokens=4000`. `qwen3-6-35b-moe` est un
modèle à raisonnement : raisonnement et réponse puisent au **même** budget.

Sonde sur cinq cas réellement tronqués :

| régime | coupées | réponse moyenne | latence médiane |
|---|---|---|---|
| témoin 4000 | 2/5 | 1281 car. | 20,3 s |
| `max_tokens` 8000 | 0/5 | 2339 car. | 20,8 s |
| **`enable_thinking: false`** | **0/5** | 1202 car. | **2,2 s** |
| `reasoning_effort: low` | 3/5 | 1196 car. | 20,8 s |

**`reasoning_effort` est silencieusement ignoré** — 16 373 caractères de raisonnement
malgré lui. Un réglage accepté sans effet est pire qu'un réglage refusé : on croit
l'avoir appliqué.

### L'arbitrage réel, mesuré sur les 122 cas

| | avec raisonnement | sans raisonnement |
|---|---|---|
| citation hors contexte | **0/122** | 26/122 |
| refuse à chaque fois | 15/18 | **21/21** |
| réponses tronquées | 39 | **1** |
| cite l'article attendu | 66/101 | **88/101** |
| latence médiane | ~15–20 s | **2,0 s** |

Le raisonnement achetait **la discipline de provenance**, et rien d'autre. Sans lui, le
modèle est plus rapide, plus complet, refuse parfaitement — et puise dans sa mémoire.

### Ce qui rend l'arbitrage tranchable

Le garde-fou mécanique attrape exactement ces 26 dérives :

| | avec raisonnement | sans raisonnement |
|---|---|---|
| réponses complètes et propres | 121/164 — 74 % | **134/164 — 82 %** |
| annotées | 4 | 24 |
| remplacées par un refus | 0 | 5 |

**Sans raisonnement gagne même sur la lecture la plus stricte**, pour un neuvième de la
latence. Le garde-fou n'est pas un raffinement : il est ce qui rend ce régime possible.
C'est aussi ce qui ramène H3 dans son budget — 10 s visés, 15 s mesurés, 2 s obtenus.

## D19 — Le garde-fou est une politique de corpus, pas un réglage global · 23/08/2026 · **actée**

Branché avec un défaut **actif**, le garde-fou a fait échouer un test existant dont la
réponse cite `[guide.txt]` — une source de fichier, pas un article.

Le test avait raison. Ce garde-fou juge une réponse à l'aune des **numéros d'article**
qu'elle cite. Colaig est multi-tenant par construction : un espace de procédures RH, une
FAQ technique, un fonds de notes internes n'en contiennent aucun. Actif par défaut, il y
remplacerait **toute** réponse par un refus — le service serait muet, et le journal
dirait qu'il protège.

`COLAIG_GARDE_FOU_ENABLED` est donc **inactif par défaut**, et s'active sur les espaces
dont les sources portent des références normalisées. Sa vraie place est
`workspace.yaml` : une variable d'environnement est globale, or la décision ne l'est pas.

Huit tests fixent les deux moitiés — inactif il ne touche à rien, actif il fait ce pour
quoi il existe. Un garde-fou dont on n'a vérifié qu'une des deux ne prouve rien.

## D20 — Le jeu doré avait 25 % de cas fautifs, tous dans le même sens · 23/08/2026 · **actée**

Quatre agents ont relu les 122 cas, chacun un quart, article par article contre le
corpus figé.

| lot | fautifs | douteux | sains |
|---|---|---|---|
| mp-001 → 030 | **14** | 9 | 7 |
| mp-031 → 060 | **8** | 7 | 15 |
| mp-061 → 091 | **3** | 7 | 21 |
| mp-092 → 122 | **6** | 6 | 18 |
| **total** | **31 / 122 — 25 %** | 29 | 61 |

### Le défaut est systématique, et il va toujours dans le même sens

**L'article porte une condition restrictive ; la réponse attendue retient la règle et
laisse tomber la borne.** Un instrument construit ainsi **récompense la réponse
incomplète et pénalise la réponse complète**. Il pousse dans la mauvaise direction, en
silence — ce qui est pire qu'un instrument bruyant.

Trois cas suffisent à le montrer :

- **`mp-026`** — la question demande si l'on peut faire régulariser *un* soumissionnaire,
  la réponse disait « oui ». R2152-2 dit « **tous les soumissionnaires concernés** ». Le
  jeu doré validait la pratique irrégulière que la question invitait à commettre.
- **`mp-106`** — la réponse énumérait les trois raisons d'un opérateur unique sans la
  borne : « n'est justifié que lorsqu'il n'existe **aucune solution de remplacement
  raisonnable** et que l'absence de concurrence ne résulte pas d'une **restriction
  artificielle** ». Elle validait l'acheteur qui fabrique son fournisseur unique.
- **`mp-081`** — la réponse omettait les trois dérogations d'allotissement. Un modèle
  répondant **complètement** était compté en écart.

Dans trois de ces cas, la `justification` énonçait correctement la borne manquante : la
connaissance était là, elle n'avait pas été transférée dans le champ mesuré.

### Deux refus indus

**`mp-032` déclarait absente une information présente.** `R2191-7` donne le taux de
l'avance (5 % à 30 %) et son assiette. Chaque mesure comptait donc comme un défaut de
refus le comportement **correct** du modèle. Ce cas avait en outre été cité toute la
journée comme « celui qu'aucun contrôle mécanique n'attrape, où l'inférence déborde ».
**C'était faux : le modèle avait raison, le cas avait tort.**

**`mp-060`** de même — `R2143-11`, `R2143-3` et `L2142-1` portent tout le cadre des
renseignements demandables ; seule la liste de l'annexe manque.

Les deux sont requalifiés en cas positifs. Deux négatifs réels les remplacent
(`mp-123` données essentielles, `mp-124` profil d'acheteur), chacun vérifié par
recherche avant écriture — et non fabriqués pour satisfaire le seuil du contrat, ce qui
serait précisément le travers contre lequel ce test met en garde.

### Ce que la vérification mécanique dit de sa propre limite

Un contrôle a été écrit — `_chantier/scripts/controle_bornes.py` — qui cherche dans les
articles cités les marqueurs de restriction absents de la réponse. **Il trouve 6 cas sur
101 ; la relecture en a trouvé 31.**

Ce n'est pas une contradiction, c'est le résultat : **ce défaut n'est pas mécaniquement
détectable.** Il fallait lire. Le contrôle reste utile comme révélateur, jamais comme
test — le transformer en test échouerait sur des cas sains et finirait ignoré.

### Effet sur les chiffres publiés

Ni dramatiser, ni minimiser :

- **recherche (88/103)** — métrique portant sur *quel article remonte*. Touchée seulement
  là où `articles_attendus` était faux : `mp-041` citait un article des concessions,
  `mp-004` et `mp-024` généralisaient une règle spéciale. À remesurer.
- **refus (21/21)** — deux cas faussement négatifs : le dénominateur était faux.
- **citation attendue (88/101)** — fondée sur les numéros d'article, peu touchée.

### Ce qui reste ouvert

29 cas douteux, non traités ici. Deux méritent un arbitrage de fond : le corpus contient
le livre défense/sécurité, dont les articles sont des **jumeaux textuels aux seuils
différents** (R2122-8 : 60 000 € ; R2322-14 : 100 000 €). Toute question posée sans
ancrage de livre est ambiguë, et un système remontant le bon article du mauvais livre
serait compté faux sans avoir rien inventé.

## D21 — Une réponse fidèle qui cite le mauvais droit · 23/08/2026 · **actée**

Le corpus contient **1806 articles, dont 38 % seulement** relèvent du régime des marchés
publics ordinaires (2ᵉ partie, livre Ier). Le reste : livre **défense-sécurité** (23 %),
concessions, marchés de partenariat, outre-mer.

Or **les 117 articles attendus par le jeu doré sont tous dans le livre Ier.** Aucun cas
ne mobilise les 62 % restants.

### Ce que la restriction change, et ce qu'elle ne change pas

| | recherche |
|---|---|
| corpus entier | 88/104 |
| restreint au livre Ier | **89/104** |

**Un seul cas.** Le bruit ne coûte presque rien à la recherche — l'hypothèse du bruit
est donc largement infirmée.

**La mesure décisive est ailleurs.** Sur les réponses produites, **108 citations sur 469
— 23 % — portent sur un article hors du régime ordinaire**, presque toutes du livre
défense-sécurité. `R2312-11` est le jumeau de `R2112-14` ; `R2322-12` celui de
`R2122-x` : même règle, seuils différents.

### Pourquoi aucun garde-fou ne peut l'attraper

Ces articles **étaient dans les passages fournis**. La provenance est donc correcte, et
`verification_citations` les valide à juste titre. C'est une réponse **fidèle qui cite le
mauvais droit** — un mode de défaillance qu'aucun contrôle de provenance ne verra jamais,
par construction.

La correction n'est pas dans le moteur, elle est dans le **périmètre du corpus**. Un
espace dédié aux marchés publics ordinaires ne doit pas contenir le livre défense. C'est
exactement le principe fondateur : un dossier, une instance, un périmètre.

## D22 — Un nombre à quatre chiffres sans tiret n'est pas un article · 23/08/2026 · **actée**

Le corpus ne compte que six articles en forme courte — `L1` à `L6`, plus `L3-1`. Tout
autre porte **quatre chiffres et un tiret**.

« Les articles R2161 et suivants » désigne donc une **section**, et c'est une façon
correcte d'écrire. Le motif élargi la comptait comme une citation d'article, qui
ressortait ensuite en fantôme.

Mesuré : sur 124 cas, **quatre des dix fantômes annoncés** étaient de cette nature. Après
resserrement : **8 au lieu de 10**.

Une métrique qui signale comme invention une manière correcte d'écrire gonfle son propre
compte et perd la confiance qu'on lui accorde. Les formes courtes restent reconnues, et
c'est nécessaire dans les deux sens : `L2` est un vrai article, `L30` — cité par une
réponse mesurée — est une vraie invention. Le motif ne peut pas les distinguer ; c'est la
comparaison au corpus qui tranche.

## Référence L1.5 sur le jeu doré corrigé — 23/08/2026

Configuration retenue : prompt durci, k=6, **raisonnement coupé** (D18), 124 cas.

| | |
|---|---|
| refuse **à chaque fois** sur cas négatif | **21/21** |
| cite l'article attendu | **88/102** — 86 % |
| tous les articles attendus remontés | **88/104** — 85 % |
| citation fantôme | 8/124 |
| citation hors contexte | 22/124 |
| montant inventé | 2/124 |
| réponses tronquées | **2** |
| latence médiane | **~2 s** |
| garde-fou : rendues / annotées / remplacées | **137 / 23 / 4** sur 164 |

Le refus est désormais **systématique** — c'était 0/8 au premier jour de mesure, 6/8 avec
le prompt durci, 15/18 avec raisonnement. H3 est tenue : 10 s visés, 2 s obtenus.

## D23 — La qualification de portée d'Editeur ne se transpose pas au code · 23/08/2026 · **actée**

`ingestion2.py`, dans le poste de rédaction `Editeur`, qualifie chaque passage avant
extraction : `compatibilite` si le texte porte « devra », « doit », « s'impose », « est
interdit » ; `indicatif` s'il porte « pourra », « veillera », « recommandé ». C'était le
seul emprunt que je recommandais — bon marché, et répondant à une faiblesse mesurée :
plusieurs cas dorés tournent sur la distinction obligation / faculté.

**Mesuré sur les 1805 articles du corpus :**

| | articles | part |
|---|---|---|
| obligation **explicite** — doit, devra, est tenu, est interdit | 111 | **6 %** |
| faculté — peut, pourra — sans obligation explicite | 634 | 35 % |
| **ni l'un ni l'autre** | 1060 | **59 %** |

Les 59 % ne sont pas des articles sans portée : ce sont des obligations énoncées **à
l'indicatif présent**, style législatif français classique. « Les marchés *sont passés*
en lots séparés » est une obligation ; « l'acheteur *écarte* les offres irrégulières »
aussi. Aucun marqueur lexical ne les distingue d'une définition.

L'emprunt échoue donc pour une raison de **genre documentaire**, pas de code. Les
documents d'urbanisme d'Editeur — SCoT, SAR — sont des documents de planification
adressés à des actions futures : ils écrivent naturellement « devra ». Un code écrit au
présent de l'indicatif.

**Conséquence :** ne pas reprendre `portee` tel quel. Un contrôle de dérive de portée sur
le code demanderait une analyse syntaxique, pas une liste de marqueurs — et son coût
n'est pas justifié tant que le défaut n'est pas mesuré autrement.

Un premier essai de mesure a d'ailleurs signalé sept dérives apparentes, dont au moins
deux étaient des **erreurs du classifieur** : `L2113-10` porte « peut limiter » plus loin
dans l'article, ce qui le faisait classer « faculté » alors que sa règle principale est
une obligation. Le classifieur était plus faux que ce qu'il mesurait.

## D24 — Le corpus est restreint au régime des marchés publics ordinaires · 23/08/2026 · **actée**

Suite de D21. La mesure tranche, et pas dans le sens attendu.

| generation sur 124 cas | corpus entier | **restreint** |
|---|---|---|
| **citations du mauvais régime** | **115 — 22 %** | **1 — 0 %** |
| citations hors contexte | 22 | 55 |
| garde-fou : rendues / annotées / remplacées | 137 / 23 / 4 | 103 / 50 / 9 |
| cite l'article attendu | 88/102 | 87/100 |

**La restriction dégrade la provenance et supprime les citations du mauvais droit.**
Le choix est donc entre **115 erreurs silencieuses** et **33 avertissements visibles de
plus**.

Il se tranche en regardant ce que l'utilisateur reçoit. Une citation du mauvais régime
délivre du droit faux **comme s'il était juste** — le livre défense pose 100 000 euros là
où l'ordinaire pose 60 000 — et **aucun garde-fou ne peut la voir**, puisque l'article
était bien dans les passages. Une citation hors contexte, elle, est annotée sous les yeux
de l'utilisateur, qui garde la réponse et la mention.

**On préfère le mode de défaillance que le garde-fou sait voir.**

### Le test d'ancrage a rattrapé une faute grossière

La première restriction faisait tomber **`L3`**, qui énonce les principes de la commande
publique. Les articles préliminaires `L1` à `L6` et `L3-1` relèvent du *Titre
Préliminaire* et d'aucun livre : un filtre par livre les emportait. Ils définissent
« marché public » et « acheteur » — ce sont les plus cités.

`HORS_PERIMETRE_RETENUS` les conserve quoi qu'il arrive. Sans
`test_tous_les_articles_cites_existent`, le corpus aurait perdu ses définitions sans que
rien ne le signale.

### Ce que cela donne

**692 articles + le titre préliminaire, 47 documents, 0,38 Mo** — contre 1806 articles et
188 documents. L'index passe de 1806 à 699 passages.

Recherche : **89/104 — 86 %**, contre 88/104 sur le corpus entier.

`PERIMETRE = None` reconstruit le code entier : l'ancien corpus reste reproductible, il
est en quarantaine hors dépôt, et dans l'historique git.

### D24 — rectificatif du 23/08/2026 : la comparaison était faussée

**Les chiffres publiés ci-dessus pour la colonne « restreint » sont faux, et le
raisonnement qui s'appuyait dessus l'est aussi.**

`reanalyse_generation.py` recalculait les passages avec un périmètre **codé en dur**
(`decouper("article")`), sans savoir lequel la mesure avait employé. Une mesure lancée
sur le corpus restreint était donc recomptée contre le corpus entier : `fournis`
n'était pas ce que le modèle avait reçu, et les citations hors contexte grimpaient à 55
sans raison.

**J'ai comparé une réanalyse cohérente à une réanalyse incohérente.** Recomptées toutes
deux contre leur propre corpus :

| generation sur 124 cas | corpus entier | **restreint** |
|---|---|---|
| **citations du mauvais régime** | **115 — 22 %** | **1 — 0 %** |
| citations hors contexte | 22 | **22** |
| garde-fou : rendues / annotées / remplacées | 137 / 23 / 4 | 135 / 21 / 6 |
| cite l'article attendu | 88/102 | 87/100 |

**La restriction supprime les citations du mauvais droit sans rien coûter à la
provenance.** Il n'y a donc pas d'arbitrage entre « erreurs silencieuses » et
« avertissements visibles » : c'était un arbitrage imaginaire, produit par un défaut de
mon outil de recomptage.

La décision de restreindre est inchangée ; elle est simplement mieux fondée qu'écrit.

Le périmètre se déduit désormais du nom du fichier de réponses, qui le porte déjà.

---

## Référence L1.5 — définitive au 23/08/2026

Corpus restreint (692 articles + titre préliminaire), jeu doré à 124 cas après les deux
lots de corrections, prompt durci, `k=6`, **raisonnement coupé**.

| | |
|---|---|
| refuse **à chaque fois** sur cas négatif | **21/21** |
| tous les articles attendus remontés | **89/104 — 86 %** |
| cite l'article attendu | **84/100** |
| citation hors contexte | **18** |
| citation fantôme · montant inventé | 6 · 2 |
| réponses tronquées | 3 |
| latence médiane | **~2 s** |
| garde-fou : rendues / annotées / remplacées | **140 / 16 / 7** sur 163 |

C'est le meilleur état mesuré à ce jour sur chacun des indicateurs de fidélité — et il
est obtenu à **un neuvième de la latence** du premier point de mesure.
