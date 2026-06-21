# Colaig — Conformité & RGPD

Document d'aide à la conformité pour un déploiement en administration publique.
Ce n'est pas un avis juridique : à compléter avec votre DPO / juriste.

## Principe de souveraineté

- **LLM** : Albert API (Etalab/DINUM) ou tout endpoint souverain OpenAI-compatible
  (SSP Cloud). Aucune dépendance à un cloud non-souverain par défaut.
- **Embeddings** : Albert ou modèle local (`COLAIG_LOCAL_EMBEDDINGS`).
- **Aucune télémétrie sortante** : Colaig n'émet pas de données vers un tiers.

## Données traitées et localisation

| Donnée | Contenu | Où | Base |
|---|---|---|---|
| Documents métier | Fichiers du workspace | **Backend de stockage** (Nextcloud/Bnum, S3, OneDrive…) | Sous contrôle de l'organisation |
| Index vectoriels | Embeddings des chunks | `{ws}/.colaig/indexes/` (storage) | Dérivé des documents |
| Historiques de conversation | Messages user/assistant | `{ws}/.colaig/conversations/*.json` | Contient des données personnelles potentielles |
| Mémoire utilisateur | Faits/préférences extraits | `{ws}/.colaig/users/{uid}/` | Données personnelles |
| Config / owners | Paramètres workspace | `{ws}/.colaig/config.yaml` | `user_ids`, `owners` = identifiants |
| Logs | Événements applicatifs | stderr/fichiers | Secrets masqués (`secrets_filter`) |

**Zéro base de données propre** : tout vit dans le backend de stockage que vous
maîtrisez → la localisation des données = celle de votre stockage.

## Minimisation & rétention

- Historique de conversation borné : `COLAIG_CONVERSATION_MEMORY_MAX_STORED` (défaut 100).
- Pas de stockage centralisé hors du workspace concerné (isolation par chemin/clé d'index).
- **À configurer selon votre politique** : purge périodique des
  `.colaig/conversations/` et `.colaig/users/` (rétention). *Une commande/cron de
  purge n'est pas fournie en standard — à mettre en place côté exploitation.*

## Droits des personnes

- **Accès / portabilité** : les données d'un utilisateur sont localisées
  (`conversations/{conv}.json`, `users/{safe_uid}/`) → extractibles depuis le storage.
- **Effacement** : supprimer les fichiers correspondants dans le storage (+ ré-indexer).
- **Information** : prévoir une mention dans le canal (Tchap/webchat) indiquant
  l'usage de l'IA et le traitement.

## Sécurité des traitements

- Masquage des secrets dans les logs **et** les réponses (`secrets_filter`).
- Isolation multi-tenant par clés d'index + validation de chemin (anti-traversal).
- Auth MCP : token ou OIDC ; dashboard protégé par `COLAIG_PLATFORM_API_KEY`.
- Voir [SECURITE.md](SECURITE.md) pour le modèle de menaces.

## À faire avant mise en production (checklist conformité)

- [ ] Activer l'auth (`COLAIG_MCP_AUTH_ENABLED=true`) — sinon accès workspace ouvert.
- [ ] Définir `COLAIG_PLATFORM_API_KEY`.
- [ ] Politique de rétention + purge des conversations/mémoire.
- [ ] Mention d'information utilisateurs (IA + traitement).
- [ ] Registre des traitements + analyse d'impact (AIPD) si requis.
- [ ] DPA avec l'hébergeur du stockage et du LLM.
