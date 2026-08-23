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

## D25 — Le vérificateur de fidélité est calibré avant d'être cru · 23/08/2026 · **actée**

Le vérificateur est **lui-même un modèle**. Lui faire juger les réponses produites sans
savoir ce qu'il vaut reviendrait à mesurer une chose inconnue avec un instrument inconnu.

Il existe un jeu de couples **fidèles par construction** : les `reponse_attendue` du jeu
doré, confrontées aux articles qu'elles citent. Elles ont été écrites d'après ces
articles, puis relues une par une contre eux par quatre relectures indépendantes. Si le
vérificateur y répond autre chose qu'« étayé », l'écart lui est imputable.

**Résultat sur 30 couples : 0 % de faux négatifs.** 19 « étayé », 11 « étayé
partiellement », aucun « ne dit pas cela ».

### Deux défauts de banc corrigés en route, tous deux les miens

**1. L'appariement à un seul article.** La première version confrontait la réponse
attendue au **premier** article attendu, et relevait **30 % de faux négatifs**. La
lecture des motifs les a tous innocentés : *« l'extrait mentionne uniquement le délai de
base sans évoquer les exceptions de réduction »*. Le vérificateur avait raison.

L'ironie est exacte : les corrections apportées au jeu doré ce même jour consistaient
précisément à **ajouter les bornes portées par les articles voisins** (D20). Ce sont
elles qui rendaient l'appariement à un seul article intenable.

**2. L'appui discontinu.** Le vérificateur cite volontiers un passage coupé, en marquant
l'élision par « [...] » — légitime quand l'appui s'étend sur deux articles. La
comparaison par sous-chaîne exacte échouait sur la jointure et déclarait **57 % d'appuis
fabriqués**, dont aucun ne l'était. Chaque fragment est désormais vérifié séparément, et
tous doivent se retrouver — avec un seuil de longueur qui ferme la porte de sortie, sans
quoi un appui fabriqué découpé en fragments de trois mots finirait par se retrouver
quelque part. **57 % → 23 %.**

Une hypothèse a été **réfutée** au passage : normaliser les variantes typographiques
(apostrophe courbe, guillemets, espaces insécables) ne change rien au résidu. La
normalisation est conservée parce qu'elle est juste en soi, pas parce qu'elle a servi.

### Ce que le vérificateur vaut, et ce qu'il ne vaut pas

**Sa valeur est dans ses verdicts négatifs.** Sur des couples dont on sait qu'ils sont
fidèles, il n'en produit aucun — donc quand il dit « ne dit pas cela », cela compte.

Il reste **23 % de verdicts positifs non ancrés**, dont `exploitable` refuse de se
contenter. C'est une limite documentée, pas un blocage : le verdict demeure disponible,
signalé comme non vérifié. Le vérificateur sert donc mieux à **signaler** qu'à
**certifier**.

**Ce qui n'est pas mesuré :** le taux de **faux positifs** — dire « étayé » d'une réponse
qui déborde. Il faudrait des couples dont on sait qu'ils sont infidèles ; le jeu doré
n'en contient pas. On peut en fabriquer, et c'est la suite naturelle de ce banc. Tant
qu'il manque, **on ne sait pas ce que vaut un « étayé »**.

## D26 — Le vérificateur s'appelle sur un signal mesuré, pas sur une intuition · 23/08/2026 · **actée**

Le vérificateur coûte ~1 s par couple, sur une réponse qui en prend 2. Le passer sur
tout triplerait la latence gagnée en coupant le raisonnement (D18). D'où la question :
peut-on ne l'appeler que là où il sert ?

**Contrainte qui élimine la plupart des idées :** le signal doit être calculable au
moment de la réponse, à partir de la question, des passages et du texte produit. Tout ce
qui suppose de connaître la bonne réponse est disponible sur un jeu doré et jamais chez
l'utilisateur — ce qui écarte d'emblée la difficulté déclarée du cas.

Mesuré sur 39 réponses, dont 12 portent au moins un verdict négatif :

| signal | réponses saines | réponses suspectes | |
|---|---|---|---|
| couples à vérifier | 2 | **5,5** | **sépare** |
| articles cités | 2 | **4** | **sépare** |
| longueur | 956 car. | **1913 car.** | **sépare** |
| score du 1er passage | 0,663 | 0,671 | ne sépare pas |
| **dispersion des scores** | 0,114 | 0,105 | **ne sépare pas** |

### Ce qui ne marche pas mérite d'être retenu

**La dispersion des scores de recherche ne prédit pas l'infidélité.** Elle prédit
l'échec de *récupération* (D11), et s'en servir ici aurait paru naturel : c'était le
signal déjà mesuré, déjà validé, déjà disponible. Il n'attrape que **2 suspects sur 12**
au même taux d'appel. Deux modes de défaillance, deux signaux.

### Le déclencheur retenu

**Le nombre de couples à vérifier** — il est à la fois le coût et le risque, ce qui en
fait le bon candidat. Seuil à 3 : **56 % des réponses vérifiées, 10 suspects sur 12
attrapés.** Un seuil sur la longueur (≥ 1105 caractères) fait légèrement mieux — 51 % des
appels, 11 sur 12 — mais la longueur ne dit rien du coût, alors que le nombre de couples
le donne exactement.

L'interprétation tient debout : une réponse longue citant beaucoup d'articles est une
réponse où le modèle a **synthétisé**, et c'est là qu'il déborde. Une réponse courte qui
cite un article et s'arrête reste fidèle.

**Le seuil est un paramètre, pas une constante.** Échantillon de 39 réponses : le sens de
la séparation est net, le seuil exact ne l'est pas. Le figer donnerait à un chiffre
provisoire l'apparence d'un acquis — un test l'interdit.

## D27 — HyDE ne passe pas la première étape · 23/08/2026 · **actée**

La logique de D26 se généralise en **deux temps qu'il ne faut pas confondre** :

1. **Est-ce que ça aide ?** Sans quoi il n'y a rien à déclencher.
2. **Quand est-ce que ça aide ?** Seulement si la réponse à (1) est oui.

`COLAIG_HYDE_ENABLED` existe dans `config.py` depuis l'origine sans avoir jamais franchi
l'étape 1. C'est l'option dont le coût est **par requête**, donc celle où un déclencheur
aurait eu le plus de valeur.

| | cas complets | gagnés | perdus |
|---|---|---|---|
| témoin | 89/104 — 86 % | | |
| **HyDE w = 0,3** | 90/104 — 87 % | 1 | 0 |
| HyDE w = 0,5 | 88/104 | 1 | 2 |
| HyDE w = 0,7 | 88/104 | 3 | 4 |

**Un cas gagné pour 0,76 s par question** — +38 % de latence sur une réponse qui en prend
deux. Aux poids élevés il gagne et perd à peu près autant : c'est du bruit.

### Pourquoi il échoue là où on l'attendait

HyDE est conçu pour les **écarts de vocabulaire** entre la question et les documents. Or
nos cas d'écart mesurés — `mp-069` et `mp-070`, posés en « CCAG » et « CCAP », dont
l'article attendu est au-delà du rang 60 — **ne figurent dans aucun gain**, à aucun poids.

La raison est logique une fois posée : la réponse hypothétique est produite par le même
modèle, qui emploie lui aussi le vocabulaire du praticien. **HyDE ne franchit pas un
fossé qu'il reproduit.**

`COLAIG_HYDE_ENABLED` reste donc à `false`, avec cette mesure pour motif. Il n'y a rien à
déclencher : on ne construit pas une porte devant une pièce vide.

## D28 — Le préfixe hiérarchique n'aide pas ; le contexte LLM aide un peu · 23/08/2026 · **actée**

Dernière option coûteuse jamais mesurée : `COLAIG_CONTEXTUAL_CHUNKING_ENABLED`, qui fait
générer par un LLM un préfixe d'une à deux phrases par passage. Coût : **un appel par
passage**, soit 9 minutes pour ce corpus, payé à chaque ré-indexation complète.

Le témoin n'est pas vide : le découpage par article **préfixe déjà** chaque passage du
titre du document et de sa position dans le code. La question est donc **« un contexte
écrit par un LLM vaut-il mieux qu'un chemin hiérarchique gratuit ? »**

| variante | cas complets |
|---|---|
| sans préfixe du tout | **90/104 — 87 %** |
| chemin hiérarchique — *ce qui tourne* | 89/104 — 86 % |
| chemin + contexte LLM | **91/104 — 88 %** |

### Le préfixe hiérarchique n'aide pas, et le code affirmait le contraire

`reference_l15.py` porte ce commentaire : *« Le préfixe est essentiel : sans lui, "Les
marchés sont passés en lots séparés" perd le contexte qui permet de le retrouver depuis
une question posée en termes de procédure. »*

**Mesuré en l'isolant : 89 avec, 90 sans.** L'affirmation était fausse.

Elle venait d'une comparaison mal lue. Le rapprochement fait plus tôt le même jour — 85
contre 88 — opposait deux **stratégies de découpage** (`markdown` contre `article`), qui
diffèrent par bien plus que le préfixe. Isoler la variable donne l'inverse.

Le préfixe est conservé : il ne nuit pas, il coûte zéro, et il rend les passages lisibles
pour qui les inspecte. Mais il ne doit plus être présenté comme un levier de rappel.

### Le contexte LLM gagne deux cas, dont un qu'aucun autre levier n'atteignait

`mp-070` est gagné. C'est l'une des questions posées en vocabulaire de praticien
(« déroger au CCAG dans mon CCAP »), dont l'article attendu était **au-delà du rang 60** —
hors de portée de la profondeur, de BM25, de la fusion et de HyDE.

La raison se comprend une fois posée : le contexte généré **décrit le passage dans les
mots du domaine**, pas dans ceux du code. C'est le seul levier mesuré qui franchisse cet
écart, là où HyDE échouait parce qu'il le reproduisait (D27).

**Gain de 2 cas sur 104 pour 9 minutes d'indexation.** C'est faible, et c'est le premier
levier à franchir l'étape 1. Le coût est **unique** — l'indexation est incrémentale
ensuite — ce qui le distingue de HyDE, payé à chaque question.

Deux cas restent près du bruit : à confirmer sur un corpus plus large avant d'en faire un
défaut.

## D29 — Le CCAG Travaux entre au corpus · 23/08/2026 · **actée**

Le constat qui l'imposait : « clauses administratives particulières », « règlement de
consultation », « acte d'engagement » avaient **zéro occurrence** dans les 1806 articles
du code. Cinq échecs de recherche étaient **hors de portée de tout réglage** — leur
article attendu au-delà du rang 60 — parce que la question et le corpus n'avaient aucun
mot en commun. Ce ne sont pas des mots du code, ce sont des mots des CCAG.

### Un seul CCAG, et lequel

Il en existe six — travaux, prestations intellectuelles, TIC, fournitures courantes et
services, maîtrise d'œuvre, marchés industriels. Ce sont des **régimes parallèles** :
l'article 20 du CCAG Travaux n'est pas celui du CCAG PI. Les verser tous rejouerait
exactement le défaut que D24 vient de supprimer.

Or **un marché relève d'un seul CCAG**, choisi par son objet. Un dossier, une instance,
un périmètre : l'espace porte le cahier de son marché. C'est le CCAG Travaux ici.

### Source et lecture

`legi_arrete` du même instantané épinglé. La partition pèse **4,7 Go en 18 fichiers** et
le CCAG y est dispersé. DuckDB lit le parquet distant par plages et ne rapatrie que ce
que le filtre retient : **8 secondes** au lieu d'un téléchargement de plusieurs
gigaoctets.

**61 articles, 117 fragments, 8 chapitres.** Le corpus passe à **760 articles** dont 55
du CCAG, 57 documents, 0,82 Mo.

### Trois défauts silencieux attrapés en construisant

**1. L'arrêté écrasait son propre cahier.** L'arrêté porte ses articles 1 à 5 — « le CCAG
travaux est approuvé », son application outre-mer — **de même numéro** que ceux du cahier
annexé. Sans filtre, « article 4 » rendait *l'application à Saint-Barthélemy* au lieu des
*Pièces contractuelles* : un article faux sous un numéro juste, que nul contrôle de
provenance n'aurait vu. Seul `subtitles LIKE 'Annexe%'` est retenu.

**2. La mention de source mentait.** Chaque document annonçait « Source : Code de la
commande publique » — faux en tête d'un CCAG, et c'est le genre d'étiquette qu'un lecteur
croit sans vérifier. Elle suit désormais le document.

**3. Le motif d'en-tête tronquait les identifiants.** `([A-Za-z0-9- ]+)` s'arrête sur le
« é » de « Préambule » : le passage entrait dans l'index sous « CCAG Travaux Pr », un nom
qui n'existe nulle part. `mp-069` le cherchait en vain **alors qu'il remontait au rang 1**.
Quatrième copie de ce motif dans le chantier, quatrième divergence.

### Ce que cela donne

| | avant CCAG | après |
|---|---|---|
| articles attendus remontés | 89/104 — 86 % | **95/109 — 87 %** |
| jeu doré | 124 cas | **130 cas**, 22 négatifs |

Cinq des six cas ajoutés sur le CCAG passent, la plupart **au rang 1** : ordre de priorité
des pièces, calcul des pénalités, délai des opérations préalables à la réception, projet
de décompte mensuel, forme des notifications. Ce sont les questions de celui qui rédige,
et le code seul n'y répondait pas.

`mp-070` échoue toujours — mais il échouait déjà avant. Pas de régression, un cas gagné.

**Vocabulaire enfin présent :** CCAP 9 occurrences, CCAG 99, acte d'engagement 4, décompte
général 22, ordre de service 39 — tous à zéro auparavant.

## D30 — Le corpus d'un expert : six CCAG et les annexes du code · 23/08/2026 · **actée**

Décision de périmètre prise par l'utilisateur : couvrir la **rédaction** et non la seule
passation. Elle renverse D29, qui ne retenait qu'un cahier.

### Pourquoi les six CCAG, alors que D24 écarte le livre défense

Les deux cas paraissent identiques — des régimes parallèles, des articles jumeaux aux
contenus différents. Ils diffèrent sur **un seul point, et il est décisif** :

| | livre défense-sécurité | les six CCAG |
|---|---|---|
| identifiant | `R2322-14` | `CCAG Travaux 20` |
| se distingue de son jumeau ? | **non** — seul un expert voit que ce n'est pas `R2122-8` | **oui** — le cahier est dans le nom |
| une citation fautive se voit ? | jamais | immédiatement |

**On écarte ce qu'on ne peut pas voir, on garde ce qu'on peut lire.**

### Ce que le corpus porte désormais

**1026 articles, 107 documents, 1,52 Mo.**

| source | articles |
|---|---|
| Code, 2ᵉ partie livre Ier + titre préliminaire | 699 |
| CCAG Travaux · MOE · FCS · PI · TIC · Industriels | 295 |
| Annexe 2 — seuils de procédure | 1 |
| Annexe 7 — profils d'acheteurs | 8 |
| Annexe 12 — signature électronique | 12 |
| Annexe 13 — modèles de garantie | 6 |

### Quatre défauts silencieux, dont un qui servait du droit périmé

**1. LEGI ne ferme pas les versions antérieures.** L'avis sur les seuils compte **cinq
versions**, toutes `status = VIGUEUR` avec `end_date = 2999-01-01`, de 2018 à 2026. La
règle temporelle ne les départage pas. **Le corpus servait donc les seuils de 2018 —
144 000 € — comme s'ils étaient en vigueur.** Règle ajoutée : par numéro, la version
applicable la plus récente. Le corpus porte maintenant les seuils du 14/01/2026 —
**140 000 €, 216 000 €, 5 404 000 €**.

**2. L'arrêté écrasait son propre cahier** — mêmes numéros 1 à 5 pour l'arrêté et pour le
cahier annexé. « Article 4 » rendait l'application à Saint-Barthélemy au lieu des Pièces
contractuelles.

**3. Les articles sans numéro s'écrasaient entre eux.** Cinq versions sous le même
en-tête « Annexe 2 — texte » : quatre disparaissaient. Un ordinal les distingue, et un
suffixe traite les numéros en double.

**4. Le motif d'en-tête perdait 183 articles.** `[A-Za-z0-9-. ]` n'accepte ni le tiret
cadratin ni les accents : « Annexe 2 — Seuils » et « CCAG Maîtrise d'œuvre » échappaient
entièrement à l'index. **1026 articles indexés sur 1026** désormais.

### Le jeu doré suit son corpus, ou il mesure autre chose

**Cinq cas négatifs sont devenus faux** : seuils européens (deux cas), modèles de
garantie, profils d'acheteurs, CCAG. Tous requalifiés en positifs — même défaut que
`mp-032`, et il **revient à chaque enrichissement**. Cinq négatifs réels les remplacent,
vérifiés contre le corpus élargi : CCTG absents, annexe 1 absente, absence de CCAG
supplétif, cumul de cahiers, taux de la retenue de garantie.

`mp-124` mérite d'être noté : écrit comme négatif le matin, rendu faux le soir par
l'ajout de l'annexe 7. C'est la démonstration la plus nette que les cas négatifs se
revérifient à chaque évolution du corpus.

**Et le contrôle des montants a arrêté une invention.** La réponse de `mp-012` annonçait
un seuil travaux de 5 538 000 € — écrit de mémoire, en violation directe de la règle qui
l'interdit. Le corpus dit **5 404 000**. Le garde-fou existe exactement pour cela.

### Mesure

| | avant | après |
|---|---|---|
| articles attendus remontés | 89/104 — 86 % | **98/113 — 87 %** |
| jeu doré | 124 cas | **135 cas**, 22 négatifs |
| corpus | 699 articles | **1026** |

Le taux tient à corpus multiplié par 1,5 et à jeu doré élargi — ce qui est le résultat
qui comptait : enrichir sans dégrader.

## D31 — Le vocabulaire du corpus se lit, il ne se devine pas · 23/08/2026 · **actée**

### La reconnaissance littérale

Les CCAG et les annexes ne numérotent pas selon un motif : « CCAG Travaux 4 »,
« Annexe 2 — Seuils de procédure — texte 1 ». **Aucune expression régulière ne les
décrit**, et en écrire une assez large pour les couvrir attraperait la moitié de la
prose.

Mais ces identifiants sont **connus** — le corpus les porte en en-tête. `articles_cites`
accepte donc un vocabulaire, cherché **littéralement**. C'est exact par construction :
la méthode ne peut trouver que ce qui existe, donc elle n'a pas de faux positif.

Sans elle, une réponse citant correctement le CCAG était vue comme ne citant **rien**, et
`garde_fou_reponse` l'aurait remplacée par un refus. C'est le mode de défaillance déjà
rencontré deux fois — sur les articles préliminaires `L1` à `L6`, puis sur les CCAG.

**Le piège du préfixe est fermé** : sans frontière de fin, « CCAG Travaux 4 » se
retrouverait dans « CCAG Travaux 41 », et une réponse citant correctement l'article 41 se
verrait attribuer un article 4 qu'elle ne cite pas. La frontière interdit ce qui
**prolonge** le numéro — un chiffre, un « .4 », un « -4 » — et rien d'autre : une
première version excluait tout point, et « CCAG Travaux 41. » en fin de phrase n'était
plus reconnu.

### Cinq copies du même motif, cinq divergences

Le motif d'en-tête `[A-Za-z0-9\- ]+` existait en **cinq exemplaires** dans le chantier.
Chacun a produit une mesure fausse avant d'être trouvé :

| copie | conséquence mesurée |
|---|---|
| `test_jeu_dore.py` | refusait « CCAG Travaux Préambule » comme inexistant |
| `index_corpus.py` | **183 articles absents de l'index** sur 1026 |
| `reference_l15.py` — découpage | passages indexés sous « CCAG Travaux Pr », tronqué |
| `reference_l15.py` — reconnaissance | six cas dorés comptés en échec, le bon passage remonté |
| `reference_generation.py` | **843 articles reconnus sur 1026** — une citation juste comptée comme fantôme |

La dernière a été trouvée **pendant** la mesure qu'elle faussait, et l'exécution a été
arrêtée.

Toutes convergent désormais : le vocabulaire est constitué une fois depuis les passages,
et passé à la reconnaissance. **Une chose qui doit être vraie partout ne doit être écrite
qu'une fois.**

## Référence L1.5 — corpus d'expert, 23/08/2026

1026 articles (code livre Ier, six CCAG, quatre annexes), 135 cas dorés, prompt durci,
`k=6`, raisonnement coupé.

| | code seul — 124 cas | **expert — 135 cas** |
|---|---|---|
| refuse **à chaque fois** sur cas négatif | 21/21 | **22/22** |
| tous les articles attendus remontés | 89/104 — 86 % | **98/113 — 87 %** |
| cite l'article attendu | 84/100 — 84 % | 86/112 — 77 % |
| citation hors contexte | 18 | **33** |
| citation fantôme · montant inventé | 6 · 2 | 7 · **0** |
| réponses tronquées | 3 | 3 |
| garde-fou : rendues / annotées / remplacées | 140 / 16 / 7 | 138 / 27 / **11** |
| réponses propres | 140/163 — 86 % | 138/176 — **78 %** |

### Ce que l'enrichissement gagne et ce qu'il coûte

**Il gagne de la couverture.** Dix questions de plus trouvent leur article, et surtout des
questions que le corpus du code ne pouvait pas traiter du tout : ordre de priorité des
pièces contractuelles, calcul des pénalités, délai des opérations préalables à la
réception, seuils européens chiffrés. La recherche tient à **87 %** sur un corpus qui a
grossi de moitié — c'était le résultat qui comptait.

**Il coûte de la précision.** Les citations hors contexte passent de 18 à 33. Un corpus
plus riche donne au modèle davantage d'articles réels à convoquer de mémoire, et
davantage de passages voisins qui se ressemblent. La proportion de réponses rendues sans
réserve tombe de 86 % à 78 %.

**Le garde-fou absorbe l'écart** : 27 réponses annotées et 11 remplacées, contre 16 et 7.
L'utilisateur reçoit la réponse **et** l'avertissement ; rien ne passe en silence. C'est
précisément ce pour quoi ce garde-fou existe, et c'est ce qui rend l'enrichissement
soutenable.

**Le refus reste parfait — 22/22.** C'était 0/8 au premier jour de mesure. Sur un corpus
deux fois plus large et avec cinq cas négatifs entièrement nouveaux, il ne bouge pas.

## D32 — Le vérificateur voit ce qu'on ajoute, pas ce qu'on retire · 23/08/2026 · **actée**

La calibration en faux négatifs (D25) ne disait que la moitié : sur des couples fidèles,
le vérificateur ne produit aucun verdict négatif. Restait à savoir ce que vaut un
**« étayé »** de sa part.

Le jeu doré ne contient pas de couples infidèles — il est écrit pour être juste. On les
fabrique donc, en partant de réponses justes et en y introduisant des dérives contrôlées,
chacune reproduisant une faute réellement observée dans ce chantier.

**104 dérives sur 45 cas :**

| dérive | détectée | |
|---|---|---|
| **ajout** d'une affirmation non étayée | **45/45 — 100 %** | l'inférence qui déborde |
| **seuil** déplacé | **10/10 — 100 %** | la faute invisible à la lecture |
| **négation** inversée — « ne peut » devient « peut » | 11/14 — 79 % | l'interdit devient permis |
| **portée** — « peut » devient « doit » | 14/23 — 61 % | une faculté présentée comme obligatoire |
| **suppression** de la borne | **6/12 — 50 %** | *le défaut mesuré sur un quart du jeu doré* |
| **ensemble** | **86/104 — 83 %** | |

### Le résultat, en une phrase

**Le vérificateur voit ce qu'on ajoute, pas ce qu'on retire.**

Cent pour cent sur l'ajout et le déplacement de chiffre ; cinquante pour cent sur la
suppression d'une condition. Et ce n'est pas un caprice du modèle : une affirmation
tronquée reste **vraie**, et l'extrait la soutient effectivement. Répondre « étayé » est
défendable — c'est le barème qui n'a pas de case pour « exact mais incomplet au point
d'induire en erreur ».

`etaye_partiellement` était censé la porter. Le modèle le lit comme « soutenu en partie »,
pas comme « soutenu mais amputé ». **La consigne le dit pourtant** — règle 4 : « un écart
de portée compte ». Comme toujours, une consigne se respecte la plupart du temps, ce qui
ne suffit pas.

### Ce que cela commande

Le vérificateur **peut être branché** pour ce qu'il fait bien : détecter l'inférence qui
déborde et le chiffre déplacé, aux deux endroits où il est parfait. Il ne peut **pas**
servir à garantir la complétude d'une réponse — c'est précisément ce que quatre
relectures humaines ont dû faire sur le jeu doré.

Une piste, à mesurer et non à supposer : un **cinquième verdict** — « exact mais
incomplet » — assorti d'une consigne qui demande d'énumérer ce que l'extrait dit et que
l'affirmation tait. Le passer par la consigne seule a déjà échoué une fois.

### Correction d'une lecture hâtive

Sur un premier échantillon de 12 cas, la négation était détectée **1 fois sur 3**, et
j'en avais conclu que le vérificateur manquait la dérive la plus dangereuse. Sur 45 cas,
c'est **11 sur 14**. Trois observations ne font pas une mesure — et le réflexe qui a
sauvé ce chantier toute la journée vient de servir contre moi-même.

## D33 — `k` suit la taille du corpus · 23/08/2026 · **actée**

L'enrichissement du corpus a fait monter les citations hors contexte de 18 à 33. Le
diagnostic, avant toute correction : **aucune n'est un article de CCAG.** Les 25
références distinctes sont **toutes des articles du code**.

Le modèle ne confond donc pas les six cahiers — l'inquiétude qui avait présidé à D24 ne
se vérifie pas ici. Il puise dans sa mémoire **du code**, et davantage qu'avant. Le
mécanisme se déduit : le corpus a grossi de 50 % et `k` est resté à 6, donc **moins de
passages du code remontent par question**.

Testable, et testé :

| | k=6 | **k=10** |
|---|---|---|
| cite l'article attendu | 86/112 — 77 % | **92/110 — 84 %** |
| citations hors contexte | 33 | **24** |
| citations fantômes | 7 | **5** |
| garde-fou : rendues | 138/176 — 78 % | **149/176 — 85 %** |
| annotées / remplacées | 27 / 11 | 24 / **3** |
| refuse à chaque fois · tronquées | 22/22 · 3 | 22/22 · 3 |

**k=10 restaure le taux de réponses propres** — 85 %, contre 86 % sur un corpus deux
fois plus petit — tout en couvrant 50 % de corpus en plus.

### Ce que cela corrige de D18

La profondeur avait été arbitrée à `k=6` le matin même, sur un corpus deux fois plus
petit **et** dans une comparaison confondue par la troncature : `k=15` gagnait en
fidélité et perdait en réponses complètes, parce que le raisonnement épuisait le budget.
Le raisonnement coupé, cette confusion disparaît — **3 troncatures dans les deux cas** —
et la profondeur peut être jugée pour elle-même.

**`k` n'est pas une constante, c'est une fonction de la taille du corpus.** Le garder fixe
en enrichissant revient à réduire silencieusement la couverture par question.

## D34 — `run()` de Matrix vérifié contre le vrai serveur · 23/08/2026 · **actée**

Dernier point du `MessagingProtocol` resté non vérifié. Les deux obstacles étaient de
nature différente et ont été traités séparément.

**L'arbitrage.** Une invitation adressée au compte bot est en attente depuis un autre
ministère, et `_on_invite` l'aurait acceptée. Le callback est **débranché avant tout
appel réseau**, et le script le vérifie par assertion — la consigne est que rien ne soit
accepté qui ne vienne de l'utilisateur ou de l'agent. Aucun callback de message n'est
enregistré non plus.

**L'environnement.** La vérification tourne en conteneur Linux. Résultat contre
`agent.dev-durable.tchap.gouv.fr` :

    auto-join débranché (4 → 3 callbacks), aucun callback message
    boucle vivante après 45 s : True
    salons chargés   : 15
    jeton de synchro : obtenu
    déconnecté, appareil révoqué

**Ce qui reste non vérifié :** l'auto-join lui-même, précisément parce qu'on le
débranche. Il demeure couvert par les seuls tests à doublure, et le vérifier demanderait
un compte bot distinct de la production.

### Deux corrections issues de l'exécution réelle

**`import olm` était le mauvais critère.** `matrix-nio` 0.26 remplace libolm par
`vodozemac`, sa réimplémentation en Rust. Mesuré en conteneur : `olm` **absent**,
`vodozemac` présent, `AsyncClientConfig(encryption_enabled=True)` accepté. Le contrôle
écrit ce matin aurait donc **refusé de démarrer sur une installation parfaitement
capable**, en réclamant un paquet dont elle n'a pas besoin — et son message d'erreur
aurait rendu le blocage incompréhensible.

`_exiger_e2e()` teste désormais la **capacité** et non son implémentation : on demande à
nio s'il sait chiffrer, et on le laisse répondre. Un garde-fou qui bloque ce qui
fonctionne est pire qu'absent : on le contourne, et tout ce qu'il protégeait avec lui.

**Un appareil neuf ne lit pas l'historique chiffré.** Le journal remonte des
`undecryptable Megolm event from a unknown device` portant l'identité du bot lui-même.
Tout redéploiement créant un nouvel appareil perd l'accès aux messages antérieurs — à
connaître avant d'en déployer un.

---

## D35 — Le balisage des contenus non fiables passe par un point unique · 24/08/2026 · **actée**

Lot L2.1. Le principe 4 de `CLAUDE.md` pose que tout contenu externe entre dans un
prompt **balisé, jamais brut**, et il nomme cinq familles : documents d'un espace,
résultats d'outils MCP, contenu web, skills, configuration lue depuis l'espace.

**Ce qui existait ne balisait pas, il en donnait l'apparence.** Trois sites entouraient
les passages de `<<<DOCUMENT>>>` … `<<<FIN DOCUMENT>>>` en insérant le contenu **tel
quel** :

    f"<<<DOCUMENT>>>\n{chunk.text}\n<<<FIN DOCUMENT>>>"

Un document contenant lui-même ce marqueur **ferme sa propre balise**, et tout ce qui
suit se lit comme du prompt. Ce n'était pas une clôture, c'était une convention que le
contenu pouvait forger — et il suffit de déposer un fichier sur l'espace pour la forger.
Le nom de la source était injecté de la même façon : **un nom de fichier est un contenu
externe**, celui qui dépose le document en choisit le nom.

Le motif était écrit **trois fois** — une dans `rag/generator.py`, deux dans
`agents/synthesiser.py`. La duplication que la règle 3 du nouveau module annonce comme
le danger s'était donc déjà produite, avant même que le module existe.

**Ce qui est fait.** `colaig/security/wrap.py` devient le point de passage unique :
`baliser(contenu, source, nature)` neutralise toute balise de la famille présente dans
le contenu, échappe la source dans son attribut, et **signale** la neutralisation au
lieu de supprimer en silence — même arbitrage que le garde-fou de provenance : annoter
plutôt que retirer, sous peine de modifier un document que l'utilisateur croit lire
intact.

Onze sites ont été portés :

| famille | site | ce qui entrait brut |
|---|---|---|
| documents | `rag/generator.py` | passages RAG + nom de fichier |
| documents | `agents/synthesiser.py` ×2 | passages RAG, chemin classique et chemin agentique |
| outils | `agents/orchestrator.py` | **tous** les `role: "tool"` — MCP, stockage, RAG, délégation |
| outils | `agents/synthesiser.py` | résultats d'outils dans le prompt système |
| MCP | `agents/orchestrator.py` | le champ `instructions` du handshake |
| skills | `agents/synthesiser.py`, `agents/orchestrator.py` | contenu intégral des `.md` de l'espace |
| documents | `rag/specializer.py` | échantillons du corpus |
| documents | `rag/contextualizer.py` | extrait + titre, à l'indexation |
| documents | `rag/document_index.py`, `mcp/server.py` | texte + nom de fichier, à l'analyse |
| documents | `agents/tools/summarize_tools.py` | texte à résumer |

**Deux sites méritent d'être nommés.**

`orchestrator.py` concaténait le champ `instructions` du handshake MCP au message
**system**, sous le titre « Instructions des serveurs MCP connectés ». Un tiers réseau
obtenait ainsi l'autorité du système. Le texte reste transmis — il porte une information
utile, ce que le serveur sait faire — mais comme **donnée** : balisé, et sous un titre
qui ne lui confère plus le statut d'instruction.

`specializer.py` dérive le persona de l'espace depuis son corpus et l'écrit dans la
configuration. Un document déposé pouvait donc **réécrire le `system_prompt` de
l'instance** : une injection qui survit à la conversation au lieu de s'éteindre avec
elle. Son séparateur `---` était forgeable au passage — une ligne de tirets dans un
document se faisait passer pour deux échantillons.

**Un site est délibérément laissé de côté, et c'est mesurable.**
`rag/verificateur_fidelite.py` interpole l'extrait dans un prompt `AFFIRMATION : … /
EXTRAIT : …`. Son taux de détection — 82,7 %, D32 — est un **seuil de
`_chantier/reference.json`**, calibré avec ce prompt exact. Le baliser modifierait le
prompt et invaliderait la calibration. Le principe « rien n'est activé sans mesure »
vaut aussi contre soi-même : le porter suppose de remesurer, ce qui est un lot en soi.

**Ce que le balisage ne fait pas.** Il ne rend pas le modèle immunisé. Il **déclare** ce
qui est donnée et ce qui est instruction ; il ne garantit pas que le modèle respecte la
déclaration. C'est la condition nécessaire, jamais suffisante — la suffisance se mesure,
et c'est l'objet de la suite adversariale du lot L2.5.

**Constat annexe, qui vaut plus que le lot.** Vérifié en cherchant si ce changement
affectait la référence L1.5 : **il ne l'affecte pas, parce que le harnais de mesure
n'utilise pas le prompt de production.** `reference_generation.py` assemble ses passages
avec `"\n\n---\n\n"` et n'appelle jamais `generator.py`. La référence mesure donc le
modèle, le corpus et la recherche — pas l'assemblage de prompt réellement livré à
l'utilisateur. Aucun seuil ne garde ce dernier.
TODO-HAUTE : faire passer le harnais par `generator.py`, faute de quoi la porte de
régression laisse le prompt de production dériver sans rien dire.

**Trois défauts recensés et NON traités**, parce qu'ils relèvent d'un arbitrage et non
d'un balisage :

1. `agents/context_builder.py` — un `.md` déposé dans `.colaig/prompts/` **remplace
   intégralement** le prompt système de l'agent, et `orchestrator.py` le place **avant**
   le template Colaig, en priorité maximale. C'est peut-être l'intention (un espace
   configure son agent) ; c'est aussi le fait que quiconque écrit sur l'espace possède
   l'agent. À trancher, pas à corriger en passant.
2. `agents/task_scheduler.py` construit son `WorkspaceContext` à la main et
   court-circuite `sanitize_system_prompt`. Les tâches de fond n'ont donc aucun filtre
   là où le chemin conversationnel en a un.
3. `security/prompt_sanitizer.py::sanitize_description` est définie et **appelée nulle
   part**. Un garde-fou qu'on n'a jamais vu se déclencher ne vaut rien.

---

## D36 — Le corpus n'a jamais compté 1026 articles · 24/08/2026 · **actée**

**1021.** Le sommaire du corpus, généré par `construire_corpus_mp.py`, l'écrit depuis le
commit `8e8a86d`. Le harnais de mesure le confirme à chaque exécution : « 1021 chunks,
1021 articles ». Un `grep` des titres sur le dépôt donne le même chiffre.

**1026 n'a jamais été vrai.** La séquence réelle est 699 (D24) → 755 (D29) → 1021 (D30).
Le chiffre a été écrit dans `_chantier/reference.json`, puis repris **cinq fois** dans
`DECISIONS.md` — dont une sous la forme « 1026 articles indexés sur 1026 », qui a l'air
d'une vérification de complétude et n'en est pas une, puisqu'aucune source ne portait le
dénominateur.

**Ce qui n'est pas touché.** Aucun seuil n'en dépend : `_configuration.articles` est un
champ descriptif, `verifier_reference.py` ne fait que l'afficher. Les conclusions
adossées à ce chiffre tiennent, leur libellé seul était faux — « 843 articles reconnus
sur 1026 » se lit 843 sur 1021, et le constat qu'il portait (un cinquième du corpus
invisible au compteur de fantômes) est inchangé.

**Pourquoi le corriger quand même.** Une référence dont la description est fausse est
exactement ce que ce chantier traque. Le jour où quelqu'un vérifie la reproductibilité
du corpus, il cherche 1026 articles, en trouve 1021, et conclut à une dérive qui n'existe
pas. Un instrument qui ment sur lui-même coûte plus qu'un instrument absent.

`reference.json` est corrigé et porte la trace de l'erreur. Les entrées antérieures de
`DECISIONS.md` ne sont **pas** réécrites : elles consignent ce qui était cru au moment
où il l'était, et c'est leur fonction.

**Comment il a été trouvé.** En vérifiant si le passage au balisage `<untrusted>` (D35)
invalidait la référence. Deux défauts pour une vérification : celui-ci, et le fait que
le harnais ne mesurait pas le prompt de production.
