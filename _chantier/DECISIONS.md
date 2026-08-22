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
