"""
Colaig — ses propres réponses passées ne sont pas un catalogue de sources.

CE QUI A ÉTÉ MESURÉ, LE 30/08/2026
------------------------------------
Sur une question réelle dans Tchap, le vérificateur a signalé :

    citation_checker: 2 citation(s) sans source correspondante:
      ['fiche_reflexe_accident___annexe_1.pdf', 'fiche_reflexe_accident___annexe_4.pdf']

Ces deux documents n'étaient **pas** dans les passages récupérés. Ils étaient dans
l'historique de la conversation — huit noms de documents y sont visibles, comptés dans
le fichier `.colaig/conversations/` de l'espace, et ces deux-là en font partie.

**Le modèle n'hallucine pas : il imite.** `generator.py` réinjecte les tours passés
verbatim, citations comprises. Le modèle voit donc, dans son propre contexte, huit noms
présentés exactement dans la forme `[nom.pdf]` qu'on lui demande d'employer pour citer.
Rien ne les distingue d'une liste de sources disponibles.

C'EST LA TROISIÈME FOIS
-------------------------
Même motif, trois fois dans la journée :

- les emojis de gestes, recopiés depuis ses propres réponses ;
- les crochets de ses consignes, relus comme des citations ;
- les noms de documents, rejoués depuis l'historique.

À chaque fois, la cause est la même : **on rend au modèle une sortie que le système
avait fabriquée**, et il la traite comme une entrée légitime.

CE QUE CELA COÛTAIT
---------------------
Le garde-fou tient — la citation est signalée, laissée en clair, non numérotée. Mais
`audit_and_adjust` retranche 30 % : une réponse par ailleurs juste tombait à 0,51.

POURQUOI RETIRER, ET NON REMPLACER PAR UN MARQUEUR
-----------------------------------------------------
Remplacer `[fiche.pdf]` par `[source]` garderait la trace qu'une affirmation était
sourcée. Mais le modèle imiterait alors `[source]` — que le vérificateur compterait à
son tour comme une citation sans source. On échangerait un fantôme contre un autre.

LA BORNE
----------
Seuls les tours de l'ASSISTANT sont nettoyés. Un utilisateur qui écrit « regarde dans
fiche_reflexe.pdf » dit quelque chose d'utile, et ce n'est pas Colaig qui l'a fabriqué.
"""

from __future__ import annotations

from colaig.messaging.sources_numerotees import retirer_les_citations


def test_une_citation_disparait_sans_laisser_de_trou():
    texte = "Un debriefing est organise [AccEvtGrave Support.pdf]. Il dure deux seances."

    assert retirer_les_citations(texte) == (
        "Un debriefing est organise. Il dure deux seances.")


def test_plusieurs_citations_dans_une_phrase():
    texte = ("La hierarchie accompagne l'agent [fiche_a.pdf], "
             "et informe l'administration [fiche_b.pdf].")

    rendu = retirer_les_citations(texte)

    assert "fiche_a.pdf" not in rendu and "fiche_b.pdf" not in rendu
    assert rendu == "La hierarchie accompagne l'agent, et informe l'administration."


def test_une_citation_en_fin_de_ligne():
    texte = "- Signaler l'accident [fiche_reflexe_accident.pdf]\n- Consulter un medecin"

    rendu = retirer_les_citations(texte)

    assert rendu == "- Signaler l'accident\n- Consulter un medecin"


def test_un_espace_reserve_n_est_pas_une_citation():
    """LA borne du vérificateur, reprise ici pour la même raison.

    « [nom de l'espace] » est un exemple que Colaig écrit dans ses propres consignes.
    Le retirer rendrait la consigne incompréhensible.
    """
    texte = "Ecris : !space create [nom de l'espace], puis consulte [guide.pdf]."

    rendu = retirer_les_citations(texte)

    assert "[nom de l'espace]" in rendu
    assert "guide.pdf" not in rendu


def test_un_texte_sans_citation_ne_bouge_pas():
    texte = "Bonjour, en quoi puis-je aider ?"

    assert retirer_les_citations(texte) == texte


def test_la_fonction_ne_modifie_pas_son_entree():
    texte = "Voir [guide.pdf]."
    copie = str(texte)

    retirer_les_citations(texte)

    assert texte == copie


# ─────────────────────────────────────────────────────────────────────────────
# LE BRANCHEMENT — c'est là que le défaut vit
# ─────────────────────────────────────────────────────────────────────────────


def test_l_historique_donne_au_modele_ne_porte_plus_de_noms(fake_llm, fake_storage):
    """Ce qui part vers le modèle ne doit pas ressembler à un catalogue de sources."""
    from colaig.models import ContextMode, WorkspaceContext
    from colaig.rag.generator import Generator

    g = Generator(fake_llm)
    contexte = WorkspaceContext(workspace=None, mode=ContextMode.ASSISTANT,
                                system_prompt="Consigne.")

    messages = g._build_messages(
        "et si c'est un usager ?", contexte, [],
        [{"role": "user", "content": "qui accompagne l'agent ?"},
         {"role": "assistant",
          "content": "La hierarchie [fiche_reflexe_accident___annexe_4.pdf]."}],
        None)

    passe = "\n".join(m["content"] for m in messages if m["role"] == "assistant")
    assert "fiche_reflexe_accident___annexe_4.pdf" not in passe, (
        f"le modele voit un nom de document qu'il n'a pas recu en source : {passe!r}")
    assert "La hierarchie" in passe, "le contenu du tour passe doit rester"


def test_les_tours_de_l_utilisateur_restent_intacts(fake_llm):
    """LA borne. Ce que l'utilisateur écrit n'est pas fabriqué par Colaig."""
    from colaig.models import ContextMode, WorkspaceContext
    from colaig.rag.generator import Generator

    g = Generator(fake_llm)
    contexte = WorkspaceContext(workspace=None, mode=ContextMode.ASSISTANT,
                                system_prompt="Consigne.")

    messages = g._build_messages(
        "et ensuite ?", contexte, [],
        [{"role": "user", "content": "regarde dans [fiche_reflexe.pdf] stp"}], None)

    utilisateur = "\n".join(m["content"] for m in messages if m["role"] == "user")
    assert "fiche_reflexe.pdf" in utilisateur


def test_les_trois_assembleurs_de_prompt_sont_couverts():
    """LA borne du lot : trois modules reinjectent l'historique, pas un.

    `generator.py` est celui qui tourne en production. `analyser.py` (deux sites) et
    `synthesiser.py` appartiennent au pipeline agent, qui n'est pas deploye — mais y
    laisser le defaut le ferait revenir le jour ou `COLAIG_AGENTS_ENABLED` sera pose,
    et personne ne s'en souviendrait.

    Ce test lit le source : c'est le seul moyen de couvrir un chemin qu'aucune
    execution n'emprunte aujourd'hui.
    """
    from pathlib import Path

    for module, sites in (("colaig/rag/generator.py", 1),
                          ("colaig/agents/analyser.py", 2),
                          ("colaig/agents/synthesiser.py", 1)):
        source = Path(module).read_text(encoding="utf-8")
        assert source.count("retirer_les_citations") >= sites + 1, (
            f"{module} reinjecte l'historique sans nettoyer les citations "
            f"({sites} site(s) attendu(s))")
