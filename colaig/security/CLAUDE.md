# security/ — contrats des gardes

Ce module regroupe ce qui protège l'instance de son propre contenu. Il ne contient
aucune I/O : tout y est fonction pure, testable hors ligne.

---

## wrap.py — point de passage unique du balisage

**Contrat.** Aucun contenu externe n'entre dans un prompt autrement que par `baliser()`.
C'est le principe 4 de `CLAUDE.md` racine, et il vise cinq familles : documents d'un
espace de stockage, résultats d'outils MCP, contenu web, skills, configuration lue
depuis l'espace.

```python
from colaig.security.wrap import CONSIGNE, baliser, formater_skills

baliser(chunk.text, source=nom_fichier, nature="document")
baliser(resultat, source=nom_outil, nature="outil")
formater_skills(agent_ctx.skills)                  # entiers
formater_skills(agent_ctx.skills[:3], taille_max=500)  # tronqués, budget de jetons
```

`nature` vaut `document`, `outil`, `web`, `skill`, `serveur-mcp` ou `configuration`. Le
modèle doit savoir **ce qu'il lit** : un résultat d'outil et un document déposé par un
collègue n'appellent pas la même prudence, et le prompt ne le devine pas.

`CONSIGNE` accompagne le bloc balisé dans le message système. Elle dit deux choses :
le contenu est une donnée, et **les balises qui apparaîtraient à l'intérieur ne font pas
foi** — seules celles que nous posons comptent.

### Les trois règles

1. **Le contenu ne peut pas fermer sa balise.** Toute occurrence des marqueurs à
   l'intérieur du contenu est neutralisée. La détection est une expression régulière
   tolérante, pas un `str.replace` littéral : un modèle lit `</ untrusted >` comme la
   fermeture.
2. **La neutralisation est visible.** On signale, on ne supprime pas — retirer une
   portion en silence modifierait un document que l'utilisateur croit lire intact, et
   masquerait la tentative au lieu de la révéler. Même arbitrage que le garde-fou de
   provenance.
3. **Un seul point de passage.** Le motif précédent — `<<<DOCUMENT>>>` … `<<<FIN
   DOCUMENT>>>` avec insertion brute — avait été écrit **trois fois** avant que ce
   module existe, et les trois copies étaient forgeables à l'identique.

### Ce que ce module ne fait pas

Il ne rend pas le modèle immunisé. Il **déclare** ce qui est donnée et ce qui est
instruction ; il ne garantit pas que le modèle respecte la déclaration. C'est la
condition nécessaire, jamais suffisante — la suffisance se mesure (lot L2.5).

### Une exception, et elle est mesurée

`rag/verificateur_fidelite.py` interpole son extrait sans baliser. Son taux de détection
est un seuil de `_chantier/reference.json`, calibré avec ce prompt exact : le baliser
invaliderait la calibration. Le porter suppose de remesurer. Voir D35.

### Le test qui garde les autres

`tests/test_balisage_untrusted.py` vérifie qu'aucun `.py` de `colaig/` ne contient plus
le marqueur forgeable — **dans son code**, commentaires et docstrings filtrés par
`tokenize`, pour que `wrap.py` puisse continuer de documenter la faille qu'il supprime
sans qu'il faille inscrire une dérogation par nom de fichier.

---

## prompt_sanitizer.py — atténuation, pas garde

`sanitize_system_prompt()` retire les caractères de contrôle, tronque, et **journalise**
trois motifs d'injection. Elle ne bloque rien et ne balise rien : le module se décrit
lui-même comme une atténuation.

Deux défauts connus, consignés en D35 et non corrigés :

- `sanitize_description()` est définie et **appelée nulle part**.
- `agents/task_scheduler.py` construit son `WorkspaceContext` à la main et
  court-circuite `sanitize_system_prompt` : les tâches de fond n'ont pas le filtre que
  le chemin conversationnel possède.

---

## acl.py — qui peut quoi

`WorkspaceACL.can_manage()` / `can_manage_workspace()`. Les *owners* ne sont pas
modifiables par `manage_workspace` (hors `_UPDATABLE`) — anti-escalade : un utilisateur
autorisé à mettre à jour un espace ne doit pas pouvoir s'y ajouter comme propriétaire.

## path_validator.py — chemins refusés

Repose sur `paths.is_reserved_path()`, qui accepte tout segment *commençant par* un
point réservé, donc aussi `.colaig-ignore`. **Ne pas y substituer** `is_instance_path()`,
qui exige l'égalité stricte : cela autoriserait la lecture de `.colaig-ignore` comme
document ordinaire.

## url_validator.py — anti-SSRF

Valide les **arguments** envoyés à un serveur MCP. Ne valide pas ce qui en revient : le
contenu de retour relève de `wrap.py`.

## secrets_filter.py — dernière barrière avant l'utilisateur

`mask_secrets()` s'applique à la réponse générée, jamais au corpus. Un secret présent
dans un document indexé ne doit pas ressortir dans une réponse.

## citation_checker.py — audit de provenance des sources de fichier

`audit_and_adjust()` pénalise la confiance quand la réponse cite des sources absentes.
À ne pas confondre avec `rag/garde_fou_reponse.py`, qui juge les **numéros d'article**
et relève d'une politique de corpus, pas d'un réglage global.

## federation_guard.py — chunks venus d'un pair

`validate_peer_chunks()` tronque et normalise. Elle **ne balise pas** : le balisage a
lieu en aval, au point de passage unique.
