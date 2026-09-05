"""
Critère de fin du lot L2.1 — « un test qui échoue si un chunk arrive non balisé ».

STATUT: TESTE
VERSION: 2026-08-24 - v1.0
LOT: L2.1

Pourquoi les tests précédents ne suffisaient pas
-------------------------------------------------
`test_balisage_untrusted.py` vérifie les sites **connus** : le générateur, les deux du
synthétiseur, les outils, les skills. C'est nécessaire et insuffisant — il ne dit rien
du dix-huitième module qu'on écrira demain.

Or c'est exactement ce que ce chantier a mesuré trois fois : `sanitize_description`
définie et jamais appelée, `storage_readonly` honoré par un site sur vingt,
`check_quota` présent chez un fournisseur sur quatre. **Une garde qui ne surveille que
les cas déjà connus ne surveille rien.**

Ce que ce test exige
--------------------
Tout module qui **appelle un LLM** passe par `security/wrap.py`, ou figure ci-dessous
avec sa raison. Écrire un nouveau module qui construit un prompt fait donc échouer la
suite jusqu'à ce que quelqu'un ait tranché : il balise, ou il s'explique.

C'est le granularité du module, pas de l'appel — un test au niveau de l'appel demanderait
une analyse de flot que rien ici ne justifie. Le module est l'unité où la décision se
prend.
"""
from __future__ import annotations

import pathlib

RACINE = pathlib.Path(__file__).resolve().parent.parent / "colaig"

# Signatures d'un appel LLM. `protocols.py` est exclu : il déclare, il n'appelle pas.
APPELS_LLM = (".chat(", "chat_with_tools(", "create_message(")

# Modules qui appellent un LLM SANS baliser, et pourquoi c'est légitime.
#
# Cette liste est le cœur du test : elle force une justification écrite plutôt qu'un
# oubli silencieux. Y ajouter une ligne est un acte, pas un réflexe.
DISPENSES = {
    # ── Transport : ils envoient un prompt, ils n'en construisent pas ──────────
    "integrations/albert.py": "client HTTP — reçoit des messages déjà assemblés",
    "integrations/llm/openai_client.py": "client HTTP",
    "integrations/llm/azure_client.py": "client HTTP",
    "integrations/llm/ollama_client.py": "client HTTP",
    "integrations/llm/capability_chain.py": "aiguillage entre clients, n'assemble rien",

    # ── Pas de contenu externe dans le prompt ─────────────────────────────────
    "agents/analyser.py": (
        "n'insère aucun passage — les champs de configuration de l'espace y sont "
        "ASSAINIS par `sanitize_description` (L2.1), ce qui est le traitement juste "
        "pour des paramètres que le prompt énonce en son nom propre"
    ),
    "context/user_memory.py": (
        "extrait des faits du message utilisateur et de la réponse de l'assistant, "
        "non d'un document. Contamination de second ordre notée en D35"
    ),
    # ── Dispense MESUREE, la seule ────────────────────────────────────────────
    "rag/verificateur_fidelite.py": (
        "son taux de detection est un seuil de `reference.json` calibre sur ce prompt "
        "exact ; le baliser invaliderait la calibration (D35). La raison est ecrite "
        "dans le module et verifiee par "
        "`test_le_verificateur_de_fidelite_porte_sa_raison_dans_le_code`"
    ),

    "rag/retriever.py": (
        "HyDE — fait imaginer une réponse à partir de la QUESTION seule, aucun "
        "passage du corpus n'entre dans ce prompt"
    ),
}


def _modules_appelant_un_llm() -> dict[str, str]:
    """Chemin relatif → source, pour tout module de `colaig/` qui appelle un LLM."""
    trouves = {}
    for chemin in RACINE.rglob("*.py"):
        relatif = chemin.relative_to(RACINE).as_posix()
        if relatif == "protocols.py":
            continue
        source = chemin.read_text(encoding="utf-8")
        if any(signature in source for signature in APPELS_LLM):
            trouves[relatif] = source
    return trouves


def test_tout_module_qui_appelle_un_llm_balise_ou_s_explique():
    """Le critère de fin de L2.1, sous sa forme opposable.

    Un module qui construit un prompt sans passer par le point unique et sans figurer
    dans `DISPENSES` fait échouer ce test. C'est voulu : le coût d'y penser doit être
    payé à l'écriture, pas découvert six mois plus tard par un recensement.
    """
    fautifs = []
    for relatif, source in _modules_appelant_un_llm().items():
        if "security.wrap" in source or "security import wrap" in source:
            continue
        if relatif in DISPENSES:
            continue
        fautifs.append(relatif)

    assert not fautifs, (
        "ces modules appellent un LLM sans baliser et sans dispense écrite — "
        "les baliser par `colaig/security/wrap.py`, ou ajouter une ligne à "
        f"`DISPENSES` en disant pourquoi : {', '.join(sorted(fautifs))}"
    )


def test_aucune_dispense_ne_survit_a_son_module():
    """Une dispense pour un fichier disparu est un mensonge qui dort.

    Sans ce test, `DISPENSES` accumulerait des lignes sur des modules renommés ou
    supprimés, et l'on croirait avoir justifié ce qu'on n'a plus.
    """
    presents = set(_modules_appelant_un_llm())
    orphelines = [d for d in DISPENSES if d not in presents]
    assert not orphelines, (
        "dispenses portant sur des modules qui n'appellent plus de LLM — "
        f"les retirer : {', '.join(sorted(orphelines))}"
    )


def test_le_verificateur_de_fidelite_porte_sa_raison_dans_le_code():
    """La seule dispense qui repose sur une MESURE, et non sur la nature du module.

    Les autres dispenses se justifient par ce que le module est — un client HTTP, un
    prompt sans passage. Celle-ci se justifie par un chiffre : le taux de détection du
    vérificateur est un seuil de `reference.json`, calibré sur ce prompt exact, et le
    baliser invaliderait la calibration (D35).

    Une dispense adossée à une mesure doit pouvoir être re-vérifiée. Ce test exige donc
    que la raison reste **écrite dans le module**, avec le nom du fichier de référence.

    Ce test vérifie que la raison est toujours là. Si quelqu'un retire le commentaire
    sans baliser, la dispense devient invisible et le test le dit.
    """
    source = (RACINE / "rag" / "verificateur_fidelite.py").read_text(encoding="utf-8")
    assert "security/wrap.py" in source, (
        "le vérificateur ne balise pas ET n'explique plus pourquoi — l'un ou l'autre"
    )
    assert "reference.json" in source, (
        "la raison invoquée est un seuil mesuré : il doit être nommé, sinon la "
        "dispense n'est plus vérifiable"
    )
