"""
Colaig — qu'est-ce qu'un outil destructif ?

STATUT: COMPLET
VERSION: 2026-08-24 - v1.0
LOT: L2.4a

Ce module ne garde rien. Il **classe**.

Pourquoi il est livré seul
---------------------------
Le lot L2.4 prévoit un sélecteur d'action : « aucun destructif exécuté sans
confirmation », par réaction ✅. La confirmation par réaction exige d'étendre
`MessagingProtocol`, donc de modifier `protocols.py` — **arbitrage humain explicite**
(`CLAUDE.md` §5). Et la boucle agentique interactive n'a aucun mécanisme de suspension :
`pause_and_ask_user` n'existe que dans les tâches de fond.

Une liste blanche d'instance aurait pu tenir lieu de garde, à la manière de L2.2. Elle
n'aurait rien protégé : `create_document` y serait soit toujours permis, soit jamais, ce
qui ne change rien face à une consigne injectée qui le déclenche. La menace visée est
**l'appel non voulu d'un outil légitime**, pas la présence de l'outil.

Construire cette garde-là aurait donc produit une protection qui se lit dans le journal
et ne protège de rien — exactement ce que ce chantier passe son temps à trouver ailleurs.
La classification, elle, est nécessaire quel que soit le canal de confirmation retenu.

La règle pour un outil MCP, et pourquoi elle est prudente
-----------------------------------------------------------
La spécification MCP pose `destructiveHint` comme n'ayant de sens que si `readOnlyHint`
est faux, et sa valeur par défaut est **vrai**. Un serveur qui n'annote rien décrit donc,
au sens de la spécification, un outil potentiellement destructif.

On suit la spécification : **annotation absente = destructif**. C'est le sens sûr, et
c'est au serveur de se déclarer inoffensif, pas à nous de le supposer.
"""
from __future__ import annotations

# Outils intégrés qui MODIFIENT quelque chose — stockage, configuration, droits, ou
# qui parlent au monde extérieur.
#
# Le critère retenu : l'appel laisse-t-il une trace que l'utilisateur n'a pas demandée ?
# Un `search_documents` mal déclenché coûte une requête ; un `manage_workspace_owners`
# mal déclenché donne l'administration de l'espace.
DESTRUCTIFS_INTEGRES = frozenset({
    # ── Écriture sur le stockage ──────────────────────────────────────────────
    "create_document",

    # ── Configuration de l'instance et de l'espace ───────────────────────────
    "manage_workspace",
    "set_workspace_prompt",       # remplace le prompt système de l'agent
    "link_conversation",          # rattache une conversation — c'est la frontière d'accès

    # ── Droits ────────────────────────────────────────────────────────────────
    "manage_workspace_owners",    # donne l'administration de l'espace

    # ── Exécution différée ou déléguée ───────────────────────────────────────
    "create_background_task",     # fait exécuter une requête plus tard, sans témoin
    "run_subtask",                # lance un pipeline complet dans un autre espace

    # ── Sortie vers l'extérieur ──────────────────────────────────────────────
    "report_to_user",             # envoie un message ; le contenu part et ne revient pas
})

# Outils intégrés qui lisent, résument ou planifient sans rien laisser derrière eux.
# Listés explicitement : un outil absent des DEUX ensembles doit se voir, pas se deviner.
LECTEURS_INTEGRES = frozenset({
    "search_documents", "fetch_document", "list_documents", "summarize_text",
    "ask_workspace", "find_workspace", "search_skill",
    "search_document_index", "list_document_index",
    "get_classified_documents", "get_document_metadata",
    "list_manageable_workspaces",
    "pause_and_ask_user",         # suspend et interroge — ne modifie rien
    "update_plan",                # écrit le plan de la tâche courante, pas le monde
})


def est_destructif(nom: str, annotations: dict | None = None) -> bool:
    """Cet appel d'outil peut-il laisser une trace non demandée ?

    Args:
        nom: le nom de l'outil tel que le registre le porte. Les outils MCP sont
            préfixés par leur connecteur (`juridique__recherche`).
        annotations: les annotations MCP, quand il s'agit d'un outil externe.

    Returns:
        `True` si l'appel modifie quelque chose, sort du périmètre, ou n'a pas déclaré
        le contraire.
    """
    if nom in LECTEURS_INTEGRES:
        return False
    if nom in DESTRUCTIFS_INTEGRES:
        return True

    # Outil externe. La spécification MCP fait de `destructiveHint` un défaut VRAI dès
    # lors que l'outil n'est pas déclaré en lecture seule. Un serveur qui n'annote rien
    # ne promet donc rien — c'est à lui de se déclarer inoffensif.
    annotations = annotations or {}
    if annotations.get("readOnlyHint") is True:
        return False
    if annotations.get("destructiveHint") is False:
        return False
    return True


def inconnus(noms) -> list[str]:
    """Les outils intégrés qui ne sont classés ni d'un côté ni de l'autre.

    Un outil oublié se comporterait comme un outil externe — donc destructif par défaut,
    ce qui est le sens sûr, mais silencieux. Cette fonction rend l'oubli visible, et un
    test de contrat s'en sert pour qu'un nouvel outil force une décision.
    """
    connus = DESTRUCTIFS_INTEGRES | LECTEURS_INTEGRES
    return sorted(n for n in noms if n not in connus)
