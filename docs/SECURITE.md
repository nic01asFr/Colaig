# Colaig — Modèle de menaces & sécurité

Revue de sécurité documentée. Ne remplace pas un audit/pentest externe (recommandé
avant exposition publique — voir fin de document).

## Actifs à protéger

- Documents métier (potentiellement sensibles / données citoyennes).
- Isolation entre workspaces et entre tenants (clients).
- Credentials (storage, LLM, messaging) et secrets.
- Intégrité de la configuration (qui peut administrer quoi).

## Surfaces & menaces → mitigations

### 1. Injection de prompt via documents
- **Menace** : un document piégé contient « ignore les consignes / exfiltre… ».
- **Mitigation** : contenu documentaire **délimité** (`<<<DOCUMENT>>>…`) + consigne
  système « donnée, jamais instruction » ; détection loggée (`prompt_sanitizer`).
  Pas de mutation/rejet (évite faux positifs). Le pouvoir réflexif est protégé par
  le gating (cf. §4), donc un doc ne peut pas déclencher d'action d'admin.

### 2. Hallucination / fausses sources
- **Mitigation** : `citation_checker` (audit post-hoc) — citations sans source →
  log + pénalité de confiance. Réponses ancrées (RAG) + fallback explicite si 0 doc.

### 3. Fuite de secrets
- **Mitigation** : `secrets_filter.mask_secrets()` sur les **logs** ET les
  **réponses** (un secret dans un doc indexé ne fuit pas à l'utilisateur).

### 4. Élévation de privilège / administration réflexive
- **Menace** : abuser des méta-outils (créer/reconfigurer des workspaces).
- **Mitigation** :
  - Garde d'injection `can_manage` : **default-deny**, DM + (admin global OU owner).
  - Garde fine `can_manage_workspace` par cible.
  - Gestion des owners réservée aux **admins globaux** ; `owners` hors `_UPDATABLE`
    → un owner ne peut pas s'auto-promouvoir.
  - En salon métier / utilisateur final → outils d'admin **non exposés**.

### 5. Isolation multi-tenant
- **Mitigation** : index FAISS par clé workspace (`{ws}::docs`), mémoire user
  `user::{ws}::{uid}`, validation de chemin (anti-traversal). **Dépend de
  `COLAIG_MCP_AUTH_ENABLED=true`** (sinon `can_access` autorise tout).

### 6. SSRF (fédération / connecteurs)
- **Mitigation** : `url_validator` / `federation_guard` — HTTPS only, hôtes
  privés bloqués (localhost/10./192.168…), pas de credentials en URL, chunks
  distants bornés et nettoyés.

### 7. Traversal de chemin (storage)
- **Mitigation** : `path_validator` appliqué dans tous les backends (rejette
  `..`, `//`, null bytes, `/.colaig` si interdit).

### 8. Déni de service / coût
- **Mitigation** : quotas journaliers par tenant (requetes/tokens), semaphores de
  concurrence (priorite user vs background), retry/backoff borne.

> ⚠️ **Le quota n'est applique que par `integrations/albert.py`.** `check_quota` est
> absent de `openai_client`, `azure_client` et `ollama_client` — or la cible de
> production est SSPCloud, endpoint OpenAI-compatible (`CLAUDE.md` §3). **Le quota ne
> s'applique donc pas la ou il compte.** Recense en D46, a porter au lot L2.6.

### 9. Authentification

> ⚠️ **Ces gardes sont OPT-IN. Sans configuration, elles laissent passer.**
> Recense en D44 : quatre gardes de ce depot rendent « autorise » quand leur variable
> est absente — `WorkspaceACL.can_access` (`auth_enabled=False`), `_check_platform_auth`
> et `_is_authenticated` (pas de cle), plus une quatrieme heritee d'une generation
> anterieure. Une variable oubliee, et plus rien ne garde, sans le moindre signal.

- **Dashboard + routes plateforme** : `COLAIG_PLATFORM_API_KEY` (Bearer).
  **Defaut : absente** — `platformApiKey: ""` dans les valeurs Helm, ligne commentee
  dans `config/.env.example`. Sans elle, `_is_authenticated` rend `True` pour tout le
  monde et **toute la surface web est servie**, y compris `/` et `/platform`.

  *Attenuation posee au lot L2.2a* : le serveur web n'ecoute alors que `127.0.0.1`, et
  le journal dit pourquoi. Mal configure veut donc dire **inaccessible**, non **ouvert**.
  `COLAIG_WEB_HOST` reste la sortie explicite pour un mandataire inverse qui porte
  lui-meme l'authentification.

- **MCP** : token auto-localisant ou **OIDC** (RS256/ES256, JWKS).
  Sans jeton, l'appelant **declare son propre `user_id`** — point d'entree non audite,
  voir D44.

- **Chemin conversationnel Matrix** : ce n'est pas `WorkspaceACL` qui garde, mais
  **l'appariement salon -> espace**. Depuis L2.1d, `can_link_conversation` exige d'etre
  declare sur l'espace pour y rattacher un salon. Voir D42 et D43 pour le modele complet.

## Tests de sécurité

Primitives couvertes : `tests/test_acl.py`, `tests/test_security_primitives.py`
(path/prompt/secrets/url/federation), `tests/test_citation_checker.py`,
`tests/test_admin_tools.py` (gating réflexif + anti-escalade).

## Recommandations avant exposition publique

- [ ] **Audit / pentest externe** (priorité : surface réflexive + multi-tenant).
- [ ] Activer l'auth (`COLAIG_MCP_AUTH_ENABLED=true`) — sinon isolation non effective.
- [ ] Scan de dépendances en CI (pip-audit / Dependabot) + politique de patch.
- [ ] `SECURITY.md` (procédure de divulgation) à la racine.
- [ ] Rotation des credentials (LLM/storage) ; secrets via vault, pas en clair.
- [ ] Revue de la gestion des `owners` (qui est admin global ?).
