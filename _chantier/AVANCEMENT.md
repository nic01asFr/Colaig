# Avancement

**Ce fichier est le mécanisme de reprise entre sessions.** Une session qui démarre le lit
et sait où reprendre sans relire le dépôt. Chaque lot terminé y inscrit : ID, date,
critère de fin atteint, commit, points ouverts.

**Format d'une entrée :**

```
## Lxx.y — Titre · JJ/MM/AAAA · TERMINÉ|EN COURS|BLOQUÉ
Critère de fin : <énoncé> → <atteint / pourquoi pas>
Commit : <sha court>
Ouvert : <ce qui reste, ou "rien">
```

---

## État global

| | |
|---|---|
| **Phase en cours** | 0 — Socle |
| **Lot en cours** | L0.1 — non démarré |
| **Bloqué par** | H3 (credentials WebDAV de test), H4/H5 (accès `colaig-0`) |
| **Arbitrages en attente** | reranker absent de SSPCloud (voir HYPOTHESES) ; nom de branche poussée ; feu vert publication (dépôt public) |
| **Dernière mise à jour** | 22/08/2026 |

---

## Journal

## Cadrage — 22/08/2026 · TERMINÉ

Analyse complète des 16 générations antérieures de Colaig (POC fév. 2025 → v3 juin 2026),
comparaison fonction par fonction, choix du tronc, plan en 41 lots.

Livrables :
- `colaig-etat-des-lieux-fonctionnel.md` — 17 domaines comparés, optimum désigné par fonction
- `colaig-recherche-cible-methode.md` — SOTA vérifié, structure cible, méthode
- `colaig-plan-construction-agentique.md` — fiches détaillées des 41 lots
- `CLAUDE.md`, `_chantier/{DECISIONS,PLAN,HYPOTHESES,CONVENTIONS}.md` — ce dépôt

Décisions actées : D1 (tronc = v3), D2 (PROD gelée), D3 (LLM = SSPCloud),
D4 (portée interministérielle + auto-hébergeable), D5 (dépôt unique), D7 (pod séparé).
D6 reportée à L5.1.

**Ouvert :** H1, H2, H3, H4, H5 non levées. H6 partielle.

---

## Sonde environnement — 22/08/2026 · TERMINÉ

Pod `proj-colaig-refonte-jupyter-python-0` (namespace `user-nic01asfr`) :
Python 3.13.13, git 2.55.0, node absent, 9,8 Go libres, sortie réseau OK
(github 200, pypi 200, llm.lab.sspcloud.fr 200).

**Trois blocages identifiés :**
1. `GET /api/models` → **401** : la clé LLM n'est pas dans l'environnement du pod.
2. `kubectl get secrets` → **403** : le service account ne peut pas lire
   `*-secretassistant`. L'auto-découverte de `platform/sspcloud.py` suppose le rôle `edit`
   du chart Helm — à reproduire au lot L3.6.
3. Aucun token GitHub, aucune clé SSH.

**Ouvert :** fournir clé LLM, token GitHub, credentials WebDAV et Matrix de test.

---

## Sonde LLM — 22/08/2026 · TERMINÉ · **H1 et H2 levées (sur Albert)**

Exécutée depuis le poste local, avec les credentials trouvés dans `colaig-v3/.env`
(sur instruction explicite). Aucune valeur de clé n'apparaît ni dans le transcript ni
dans les fichiers produits. Résultat complet : `_chantier/mesures/llm-capabilities-albert.md`.

**Point à corriger dans la config héritée :** `ALBERT_API_URL` de v3 vaut
`https://albert.api.etalab.gouv.fr` — **sans le préfixe `/v1`**, ce qui renvoie 404 sur
tous les appels. L'URL correcte est `https://albert.api.etalab.gouv.fr/v1`.

Catalogue Albert servi (10 modèles) : `openai/gpt-oss-120b`,
`qwen3-coder-30b-A3b-instruct`, `ministral-3-8b-instruct-2512`,
`mistral-small-3-2-24b-instruct-2506`, `deepseek-v4-flash`, `bge-m3`,
`bge-reranker-v2-m3`, `qwen3-vl-embedding-8b`, `whisper-large-v3`, `lightonocr-2-1b`.

| question | réponse mesurée |
|---|---|
| Chat | ✅ 200 en 0,20 s sur `openai/gpt-oss-120b` |
| **Tool calling** | ✅ **`tool_calls` présent et bien formé** (0,41 s) |
| Embeddings | ✅ `qwen3-vl-embedding-8b`, **dimension 4096** (0,19 s) |
| Reranker | ✅ `bge-reranker-v2-m3` opérationnel via `POST /rerank` (0,12 s) |

**Conséquence :** la boucle agent native est réalisable, les lots de la phase 4 gardent
leur forme. Le repli « JSON imposé par prompt » est écarté.

**Réserve explicite — H1 n'est PAS levée pour SSPCloud.** Ce qui est mesuré ici, c'est
Albert. D3 désigne SSPCloud comme cible de production : il faut sa propre clé pour
sonder `llm.lab.sspcloud.fr`, et rien ne permet de supposer que son catalogue est le
même. Tant que ce n'est pas fait, `qwen3-6-35b-moe` reste **INCONNU**.

**Ouvert :** clé LLM SSPCloud ; dimension d'embedding 4096 à confronter au coût
mémoire de l'index (H4).

---

## L0.1 — préparation · 22/08/2026 · EN COURS

`scripts/bootstrap.ps1` relu et corrigé avant exécution (trois défauts réels) :
1. le dossier de sauvegarde horodaté était recopié **dans** `_chantier`, y imbriquant une
   copie à chaque relance — contredisait l'idempotence annoncée ;
2. aucune vérification de `$LASTEXITCODE` : PowerShell n'applique pas
   `$ErrorActionPreference` aux commandes natives, un `git checkout` en échec passait
   inaperçu (le `2>&1 | Out-Null` masquait même le message) ;
3. `git remote add v3` échouait à la relance si le remote existait déjà.

Fichier normalisé en UTF-8 BOM + CRLF : en LF sans BOM, PowerShell 5.1 ne parsait pas le
here-string du `.gitignore`. `ParseFile` retourne désormais PARSE OK.

Le dépôt source `colaig-v3` est présent, branche `feat/reflexive-self-config` disponible.

**Ouvert :** exécution du bootstrap, `pytest -q`, premier commit.

---

## Sonde SSPCloud et GitHub — 22/08/2026 · TERMINÉ · **H1b et H6 levées**

Credentials fournis dans le `.env` du chantier (`sspcloud_api_key`, `gh_pat_token`).
Aucune valeur n'apparaît dans les fichiers produits ni dans les échanges.

**H1b levée — `qwen3-6-35b-moe` supporte le tool calling** (200 en 1,19 s, `tool_calls`
bien formé). La boucle agent native tient sur la cible de production ; les lots de la
phase 4 gardent leur forme. Embeddings : `qwen3-embedding-8b`, dimension 4096.

**H2 dégradée en arbitrage :** SSPCloud ne sert **aucun reranker** (7 modèles au
catalogue). Albert en sert un. Trois options chiffrées dans `HYPOTHESES.md` — bi-provider,
MMR seul, ou reranker local. **Aucune n'est retenue par défaut, c'est une décision.**

**H6 levée côté dépôt :** PAT fine-grained valide, `push: true` sur `nic01asFr/Colaig`.
Deux faits à ne pas subir : la branche par défaut est **`Colaig_main`**, pas `main` ; et
le dépôt est **public**. Vérification faite avant tout push : les 16 commits de v3 ne
contiennent aucun secret. Mais pousser dans un dépôt public **est** une publication —
porte humaine du point 8, à franchir avant le premier push.

**Ouvert :** arbitrage reranker ; nom de la branche poussée ; feu vert publication.

---

## Prochaine action

1. **Arbitrer** : nom de branche à pousser, et feu vert publication (dépôt public).
2. Exécuter `_chantier/scripts/bootstrap.ps1` (L0.1) — désormais non destructif,
   puis `pytest -q`, puis commit local.
3. Trancher l'arbitrage reranker et l'inscrire dans `DECISIONS.md`.
4. Obtenir les credentials du WebDAV de test — H3 tient telle qu'elle est formulée
   (voir la correction ci-dessous : v3 implémente bien WebDAV, c'est même son défaut).
5. Corriger `ALBERT_API_URL` (ajout de `/v1`) au portage de la configuration.
