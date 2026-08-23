# Plan de construction — 41 lots

Un lot = une branche = une PR. La PR référence l'ID du lot et son critère de fin.
Les fiches détaillées (contrat complet, justification) sont dans
`colaig-plan-construction-agentique.md`. Ce fichier est la version de travail.

## Graphe

```
P0 (1 agent, séquentiel)
 └─> P1 ──┬─ 3 agents ─┐
          └─> P2 ──────┼─ 2 agents ─┐   [PORTE 1 : sécurité]
                       └─> P3 ──────┼─ 4 agents ─┐
                                    └─> P4 ──────┼─ 3 agents ─┐  [PORTE 2 : mesure]
                                                 └─> P5 ──────┼─ 3 agents ─┐
                                                              └─> P6 ──────┴─> P7
```

Chevauchements autorisés : **L3.6** (Helm) dès P0 · **L1.4** (jeu doré) en parallèle de P0.

**Interdits.** Aucun lot P4 avant L1.5. Aucune ouverture multi-utilisateurs avant P2
complète. Aucune modification de `protocols.py` sans arbitrage.

---

## Phase 0 — Socle · 1 agent, séquentiel

| ID | Lot | Dépend | Critère de fin |
|---|---|---|---|
| L0.1 | Assainissement dépôt : import v3, `main`, suppression `;C`, `secrets/`, `.gitignore` durci | — | `git status` propre, `pytest` au même niveau qu'avant |
| L0.2 | `paths.py` source unique + `legacy_albert_path()` | L0.1 | `pytest tests/test_paths_source_unique.py` vert — critère vérifié par AST, le grep d'origine étant inapplicable (voir docstring du test) |
| L0.3 | Doctrine corrigée dans `CLAUDE.md` (multi-provider, SSPCloud) | L0.1 | zéro contradiction code/doc, revue humaine |
| L0.4 | Harnais de test : `FakeStorage`, `FakeMessaging`, `FakeLLM` déterministes, `conftest` unifié | L0.1 | suite complète hors ligne < 60 s |

## Phase 1 — Filet · 3 agents

| ID | Lot | Dépend | Critère de fin |
|---|---|---|---|
| L1.1 | Contrat `StorageProtocol` sur 7 implémentations | L0.4 | vert sur local + **s3 (SSPCloud, D8)** ; autres `skipif`, webdav inclus |
| L1.2 | Contrat `MessagingProtocol` (matrix/webchat/noop) | L0.4 | idem |
| L1.3 | Contrat `LLMClientProtocol` + `capability_chain` SSPCloud | L0.4, **H1** | `docs/llm-capabilities.md` rempli par la sonde |
| L1.4 | Jeu doré : extraction + anonymisation depuis `colaig-0` | L0.4, **H4** | `tests/golden/v1.jsonl` ≥ 200 cas, ≥ 3 espaces, revue humaine |
| L1.5 | Harnais RAGAS + DeepEval, **rapport de référence** | L1.4 | `docs/baseline-AAAAMMJJ.md` reproductible |
| L1.6 | CI GitHub Actions, portes bloquantes sur régression relative | L1.1-L1.5 | une PR fautive est bloquée |
| L1.7 | `debug_contexte` / `reparer_contexte` + migration `.albert`→`.colaig` | L0.2 | espace `.albert` de test migré sans perte d'index, idempotent et réversible |

## Phase 2 — Sécurité · 2 agents · **bloquante avant tout multi-utilisateurs**

| ID | Lot | Dépend | Critère de fin |
|---|---|---|---|
| L2.1 | Balisage `<untrusted source="…">` — point de passage unique `security/wrap.py` | L0.4 | test qui échoue si un chunk arrive non balisé |
| L2.2 | **Allow-list MCP au niveau instance** (`platform_policy.allowed_mcp_servers`) | L2.1 | un `mcp_servers.json` hors liste ne produit aucun outil |
| L2.3 | Épinglage des schémas d'outils MCP (`mcp_pins.json`) | L2.2 | changement de schéma → outil désactivé + alerte |
| L2.4 | Action-selector pour outils destructifs (confirmation par réaction ✅) | L2.1 | aucun destructif exécuté sans confirmation |
| L2.5 | Suite adversariale (méthodologie AgentDojo) | L2.1-L2.4 | **zéro appel d'outil non planifié**, ≥ 20 attaques |
| L2.6 | Câblage `security/` aux points de passage réels + `citation_checker` | L2.1 | couverture branche > 90 % sur `security/` |

> **L2.2 est le lot le plus urgent du chantier.** Aujourd'hui, quiconque écrit dans le
> WebDAV d'un espace injecte un outil arbitraire dans le registre de l'agent.

## Phase 3 — Portage des briques PROD · 4 agents

| ID | Lot | Dépend | Critère de fin |
|---|---|---|---|
| L3.1 | Scoring de binding 6 niveaux + auto-bind à l'invitation ; vérité dans `config.yaml` | L0.2 | `test_workspace_binding.py` de PROD passe sur le tronc |
| L3.2 | Fils Matrix + **mention native `m.mentions`** + registre des `thread_root` suivis | L1.2 | un fil ouvert sur une réponse du bot est suivi sans nouvelle mention |
| L3.3 | Réactions 👍👎🔄➕ ; ➕ réécrit sur `StorageProtocol` ; feedback persisté ; pose auto de 👍👎 | L3.2 | ➕ écrit dans `.colaig/notes.md` ; feedback survit au redémarrage |
| L3.4 | Client MCP (registre, transport, cache, compaction, timeout 20 s) ; `cache_scope`→`cacheScope`, honorer `ttlMs` | L1.3, L2.2, L2.3 | `test_mcp_datagouv.py` passe + test `cacheScope` |
| L3.5 | Filtrage d'outils 2 niveaux **fusionné dans PreExecution** | L3.4 | **1 seul `embed()` par tour**, vérifié par compteur |
| L3.6 | Chart Helm Onyxia + `sspcloud.py` (auto-découverte clé, rôle `edit`) | L0.1 | `helm install` → pod qui répond `/ready` |
| L3.7 | Pièces jointes + commandes réduites (`!aide !space !index !classer !skills`) | L1.2 | une PJ est classée dans le bon dossier |

## Phase 4 — Qualité perçue · 3 agents · **mesurée contre L1.5**

| ID | Lot | Dépend | Critère de fin |
|---|---|---|---|
| L4.1 | Retriever réglé : **HyDE off par défaut**, pool ~20→rerank→3-5 mesuré, seuil adaptatif μ−2σ en option | L1.5, H2 | rapport comparatif vs référence, config justifiée par les chiffres |
| L4.2 | PreExecution bout en bout (1 embed, multi-source) | L3.5 | trace : 1 embed, 1 aller-retour storage par source |
| L4.3 | ProgressReporter câblé Matrix | L3.2 | 5 messages d'étape sur une requête à 3 outils |
| L4.4 | Synthèse conditionnelle (seulement si ≥ 1 outil exécuté) | L4.3 | 1 appel LLM sur « bonjour », N+1 sur requête outillée |
| L4.5 | TaskExecutor câblé — **supprime le timeout global de 75 s** | L0.4 | 2 conversations simultanées ne se bloquent pas ; ordre respecté dans une conversation |
| L4.6 | Mémoire conversationnelle + utilisateur activées | L1.5 | gain mesuré sur les cas multi-tours |

## Phase 5 — Capacités · 3 agents

| ID | Lot | Dépend | Critère de fin |
|---|---|---|---|
| L5.1 | Serveur MCP migré **spec 2026-07-28** : MRTR, `Mcp-Method`/`Mcp-Name`, `ttlMs`/`cacheScope`. **Arbitrer D6** | L3.4, L2.2 | un client 2026-07-28 appelle `colaig_ask`, une élicitation aboutit |
| L5.2 | Outil `ask_user` (clarification) | L4.4 | sur un cas doré ambigu, le bot pose une question |
| L5.3 | Bus d'événements asyncio + taxonomie 25 types | L0.4 | `DOCUMENT_UPDATED` émis et reçu |
| L5.4 | Webhooks refondés : sortant sur événements + HMAC ; entrant → tool MCP `colaig_notify` | L5.3, L5.1 | `webhook_service.py` et `webhook_handler.py` supprimés |
| L5.5 | Retrait des behaviors JSON, migration vers skills (script, pas suppression sèche) | L4.2 | zéro référence `behavior` dans `colaig/` |
| L5.6 | Sources synchronisées (**D11**) + web externalisé sur `webtools` MCP ; conserver la logique de fraîcheur | L3.4, **L1.5** | `!explorer_lien` sans Chromium dans l'image |

## Phase 6 — Écosystème · 3 agents

| ID | Lot | Dépend | Critère de fin |
|---|---|---|---|
| L6.1 | Permissions `read/write/admin/bot` + héritage droits WebDAV | L2.6 | un `read` ne peut pas déclencher `create_document` |
| L6.2 | Templates d'espaces métier (juridique, RH, technique, projet, ingénierie routière) | L6.1 | créer depuis un template produit arborescence + config |
| L6.3 | Fédération : `workspace_directory` + peers MCP + `ask_workspace` | L6.1 | réponse sourcée d'un autre espace, ssi le droit existe |
| L6.4 | Plateforme sans SQLite : `clients.yml` + secrets K8s + `platform_policy` + ZIP + UI DSFR | L3.6 | un tiers installe depuis le ZIP sans accès à l'infra |
| L6.5 | Observabilité : `/ready` réel, `/metrics`, métriques métier, `request_id` | L5.3 | tableau de bord latence par étape |

## Phase 7 — Finition

L7.1 streaming token-par-token · L7.2 annulation · L7.3 late chunking évalué ·
L7.4 catalogue d'outils versionné.

---

## Portes humaines

| Porte | Quand | Validé |
|---|---|---|
| **P1** | fin P2 | suite adversariale à zéro échec — sans ça, pas d'ouverture au-delà de Nicolas |
| **P2** | fin P4 | rapport comparatif vs référence L1.5 ; si pas de progrès mesurable, on corrige, on ne passe pas |
| **P3** | fin P6 | un second espace métier réel en production, avec un utilisateur qui n'est pas Nicolas |

À chaque porte : dogfooding une semaine sur un pod de test, relevé des 👍👎 et incidents,
puis généralisation. Le flag rend le retour arrière gratuit.

---

## Ce qui disparaît définitivement

`app/` et l'héritage albert-tchap · `iam.py`, `_grist_legacy.py` · `behavior_manager`,
`behavior_index`, les 4 types JSON · `browser_use`, langchain, le wheel vendoré,
l'installation Chromium au runtime · `web_add.py`, `web_explorer.py` · le vector store web
JSON · `webhook_service.py`, `webhook_handler.py` · `registry.py` SQLite ·
`extract_with_llm_summary` (le repli qui demande au LLM d'inventer le contenu d'une page) ·
`.albert/` · les répertoires `;C` · les branches `Colaig_main` et
`claude/wonderful-villani` · le pod `proj-colaig-refonte` vide.
