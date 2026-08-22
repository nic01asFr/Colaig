# CLAUDE.md — Chantier Colaig, tronc unique

> **Lis ce fichier en entier avant toute action.**
> Ordre de lecture : ce fichier → `_chantier/DECISIONS.md` → `_chantier/PLAN.md` →
> `_chantier/AVANCEMENT.md` → `colaig/protocols.py` → le `CLAUDE.md` du module que tu touches.

## 1. Ce qu'est ce dépôt

Ce dépôt est le **tronc unique** de Colaig, issu de la consolidation de 16 générations
antérieures du projet (février 2025 → juin 2026). Il part de `colaig-v3` et absorbe
brique par brique ce qui a été validé ailleurs, en particulier dans `Plateforme_colaig`
(la version déployée jusqu'à présent).

**Colaig** est un assistant IA conversationnel décentralisé pour l'administration
française : il écoute sur un canal de messagerie (Tchap/Matrix), lit les documents d'un
espace de stockage (WebDAV/Bnum), et répond en s'appuyant sur ce corpus.

**Principe fondateur, posé en juillet 2025 et jamais démenti :**

> Un espace de stockage + un dossier `.colaig` = une instance Colaig complète.

Chaque espace a sa configuration, ses conversations, son index, ses skills, ses outils.
Colaig est un système multi-tenant dont la frontière est le dossier.

## 2. Principes inviolables

1. **Zero database.** Toute persistance passe par `StorageProtocol`. Jamais de PostgreSQL,
   SQLite, Redis, Qdrant ou ChromaDB comme dépendance de Colaig — y compris pour la
   couche plateforme. (Un service externe qui utilise une base en interne, c'est son
   affaire, pas la nôtre.)
2. **Provider-agnostic.** Toute I/O passe par un Protocol : `StorageProtocol`,
   `MessagingProtocol`, `LLMClientProtocol`. L'implémentation concrète est injectée dans
   `main.py`, nulle part ailleurs.
3. **Un seul module produit les chemins.** `colaig/paths.py` est la source unique des
   chemins `.colaig/` et des clés d'index. Aucun autre fichier ne construit un chemin
   `.colaig/...` en dur.
4. **Le contenu externe est non fiable par construction.** Documents WebDAV, résultats
   d'outils MCP, contenu web, skills, `workspace.yaml` : tout entre dans un prompt
   **balisé**, jamais brut.
5. **L'identité de l'instance est portée par ses identifiants de connexion** (une adresse
   mail dédiée qui ancre Matrix, WebDAV, LLM, GitHub). Pas de couche IAM interne.
6. **Rien n'est activé sans mesure.** Toute option coûteuse (HyDE, contextual chunking,
   rerank, résumé LLM) est un flag, mesuré par espace contre la référence.

## 3. LLM

**Cible de production : SSPCloud**, endpoint OpenAI-compatible
(`https://llm.lab.sspcloud.fr/api`), via `colaig/integrations/llm/provider_registry.py`.
Albert API reste **un provider parmi d'autres**, pas une exclusivité.

⚠️ Si tu lis quelque part dans ce dépôt « LLM : Albert API uniquement », c'est un texte
périmé hérité de v3 : le code a toujours été multi-provider (`openai_client`,
`azure_client`, `ollama_client`, `capability_chain`). Le lot **L0.3** corrige ce texte.

## 4. Conventions de travail — non négociables

1. **Protocols only.** Tu implémentes les Protocols qui te sont assignés et tu consommes
   les autres modules *uniquement* par leur Protocol. Aucun import d'implémentation
   concrète en dehors de `main.py`.
2. **Test avant code.** Le test de contrat ou de composant est écrit et commité **avant**
   le portage. Si le test ne peut pas s'écrire, la brique est mal délimitée : remonte à
   l'orchestrateur au lieu de coder.
3. **Marqueurs de statut.** Chaque fichier porte en en-tête :
   `STATUT: NON_IMPLEMENTE|PARTIEL|COMPLET|TESTE`, une version datée, et des
   `# TODO-CRITIQUE|HAUTE|NORMALE|BASSE`.
4. **`CLAUDE.md` par module**, à jour, décrivant le **contrat** et non l'implémentation.
5. **Un lot = une branche = une PR**, référençant l'ID du lot et son critère de fin.
   Jamais de gros merge.
6. **Tout portage passe par un flag** `COLAIG_<BRIQUE>_ENABLED`, avec une **date de
   péremption écrite dans la PR**. Le vieux code part au même sprint que le nouveau.
7. **Rien de nominatif** dans le dépôt : ni tests, ni fixtures, ni exemples.
8. **Interdiction d'inventer une donnée.** Catalogue de modèles, latence, volume de
   corpus : si la valeur manque, tu **arrêtes le lot et tu demandes**. Tu ne mets pas de
   valeur par défaut plausible.

## 5. Interdits stricts

- Ne **jamais** modifier `colaig/protocols.py` sans arbitrage humain explicite.
- Ne **jamais** franchir une porte humaine (voir `_chantier/PLAN.md` §7) sans validation.
- Ne **jamais** démarrer un lot de la phase 4 avant que la référence de mesure (L1.5)
  existe. *Sans référence, « ça a l'air mieux » remplace la mesure — c'est exactement ce
  qui a produit seize versions du projet.*
- Ne **jamais** toucher à l'instance de production `colaig-0` (pod SSPCloud, namespace
  `user-nic01asfr`, Tchap `agent.dev-durable.tchap.gouv.fr`).
- Ne **jamais** commiter un secret, ni un extrait de conversation non anonymisé.

## 6. Continuité entre sessions

`_chantier/AVANCEMENT.md` est le **mécanisme de reprise**. Chaque lot terminé y inscrit :
l'ID, la date, le critère de fin atteint, le commit qui l'atteint, et les points ouverts.
Une session qui démarre lit ce fichier et sait où reprendre sans relire le dépôt.

**Tu le mets à jour à chaque lot terminé. Sans exception.**

## 7. État actuel

Le dépôt n'est pas encore initialisé — voir `_chantier/AVANCEMENT.md` pour l'étape en
cours et `_chantier/scripts/bootstrap.ps1` pour l'import du tronc.
