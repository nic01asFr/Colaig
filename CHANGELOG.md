# Changelog

Format inspiré de [Keep a Changelog](https://keepachangelog.com/fr/).
Versionnage sémantique.

## [1.0.0] — 2026-06-21

Première version diffusable. Assistant IA souverain, provider-agnostic,
multi-tenant, déployable en un container (ou sur Onyxia/SSP Cloud).

### Ajouté
- **Administration réflexive** : en DM admin, l'agent crée/configure des
  workspaces et lie des salons en conversation (`manage_workspace`,
  `link_conversation`, `set_workspace_prompt`, `list_manageable_workspaces`,
  `manage_workspace_owners`).
- **Droits scopés** : `owners` par workspace ; `can_manage` (injection) +
  `can_manage_workspace` (garde fine) ; anti-escalade (owners hors update générique).
- **Ops / observabilité** : probes `/ready` + `/live`, middleware request_id /
  W3C trace, suivi d'usage LLM par tenant (`/metrics` JSON + `/metrics/prometheus`).
- **Quotas par tenant** : limites journalières requêtes/tokens (`COLAIG_DAILY_*_LIMIT`).
- **Sécurité** : délimitation anti-injection des documents, audit des citations
  (anti-hallucination), masquage des secrets en réponse ; primitives testées.
- **Auto-spécialisation** (opt-in) : dérive persona/vocabulaire d'un workspace
  depuis son corpus (dry-run par défaut).
- **Déploiement** : chart Helm Onyxia/SSP Cloud, CI build/push image (ghcr).
- **Robustesse** : validation de config au boot, fallback embeddings local,
  S3 `session_token` (STS).
- **Docs** : guide utilisateur, réflexif & ops, exploitation (runbook),
  sécurité (modèle de menaces), conformité RGPD.

### Corrigé
- Auth Bigfolder (`Authorization: Bearer`), endpoint Albert par défaut
  (`albert.api.etalab.gouv.fr`), fix anti-open-redirect du paramètre `next`.

### Base
- RAG hybride (dense + BM25 + RRF, reranking, HyDE, contextual chunking, citations).
- Pipeline multi-agent (Analyseur → Orchestrateur agentique → Synthétiseur).
- MCP (serveur ~23 tools + client de connecteurs externes).
- 7 backends storage, 4 familles LLM (fallback), 3 canaux messaging.
- Mémoire conversationnelle + par utilisateur, tâches autonomes, fédération.

[1.0.0]: https://github.com/CEREMA/colaig/releases/tag/v1.0.0
