"""
Colaig — une seule grammaire de citation à la fois.

CE QUE LA CAMPAGNE DU 31/08/2026 A MONTRÉ
-------------------------------------------
Le prompt donné aux agents accolait deux consignes de citation qui ne disent pas la
même chose :

- **prompt de rôle** : « Citer les documents sources entre crochets `[nom_fichier.ext]` »
- **prompt d'espace**, règle 1 : « **Cite l'article, toujours.** »

Sommé de citer des noms de fichiers *et* des numéros d'article, le modèle a produit
quatre grammaires incompatibles dans une même campagne :

    ['service-public.fr']
    ['087-…-section-1-avances-l-article-r2191-10.md']    nom de fichier + article
    ['Nom de la Marque/Modèle']                           un espace réservé
    ['Doc 1, 1.1', 'Doc 1, 1.2', 'Doc 2, 1.1', …]         grammaire inventée

Les douze « Doc N, x.y » sont les plus parlants : ne pouvant satisfaire les deux ordres,
le modèle en a inventé un troisième.

CE QUE CELA COÛTE, MESURÉ
---------------------------
| | cœur RAG déployé | pipeline agent |
|---|---|---|
| citation fantôme | 9/135 | 11/135 |
| refus **toujours** | **22/22** | 18/22 + 4 intermittents |

Le refus est le vrai handicap, et « cite toujours » est ce qui le mine : quand le modèle
n'a rien à citer, l'ordre de citer l'emporte sur celui de se taire.

LA SÉPARATION QUE CE LOT POSE
-------------------------------
Le prompt de **rôle** dit ce que l'agent FAIT. La **grammaire de citation** est une
convention, séparée, servie **seulement si l'espace n'en fournit pas**. Deux consignes
ne peuvent plus se contredire, parce qu'une seule est donnée.

C'est la même règle que `context_builder` énonçait déjà sans la tenir : *« l'espace vient
en dernier — ses règles doivent l'emporter »*. Venir en dernier ne suffit pas à
l'emporter ; il faut que l'autre se taise.
"""

from __future__ import annotations

import pytest


def test_sans_prompt_d_espace_la_convention_par_defaut_est_donnee():
    """Un espace qui ne dit rien doit quand même obtenir des citations."""
    from colaig.agents.context_builder import DEFAULT_PROMPTS, composer_prompt_systeme

    compose = composer_prompt_systeme(DEFAULT_PROMPTS["synthesiser"], "")

    assert "[nom_fichier.ext]" in compose, (
        "sans regles d'espace, la convention par defaut doit rester")


def test_avec_un_prompt_d_espace_la_convention_par_defaut_se_tait():
    """LE défaut. Deux grammaires données ensemble en produisent une troisième."""
    from colaig.agents.context_builder import DEFAULT_PROMPTS, composer_prompt_systeme

    compose = composer_prompt_systeme(
        DEFAULT_PROMPTS["synthesiser"],
        "Cite l'article, toujours — « L2123-1 », pas « le code prévoit que ».")

    assert "[nom_fichier.ext]" not in compose, (
        "la convention par defaut contredit celle de l'espace : "
        f"{compose!r}")
    assert "L2123-1" in compose, "les regles de l'espace doivent etre servies"


def test_le_reste_du_prompt_de_role_survit():
    """LA borne. On retire une convention, pas la description du métier."""
    from colaig.agents.context_builder import DEFAULT_PROMPTS, composer_prompt_systeme

    compose = composer_prompt_systeme(DEFAULT_PROMPTS["synthesiser"], "Regles d'espace.")

    assert "réponse finale" in compose, "le role de l'agent doit rester enonce"
    assert "ne jamais inventer" in compose, (
        "la regle anti-invention n'est pas une convention de format : elle reste")


def test_un_prompt_d_espace_vide_vaut_absence():
    from colaig.agents.context_builder import DEFAULT_PROMPTS, composer_prompt_systeme

    for vide in ("", "   ", "\n"):
        compose = composer_prompt_systeme(DEFAULT_PROMPTS["synthesiser"], vide)
        assert "[nom_fichier.ext]" in compose, f"« {vide!r} » traite comme des regles"


@pytest.mark.asyncio
async def test_le_contexte_d_agent_utilise_la_composition(fake_storage):
    """Le branchement : la règle doit atteindre le prompt réellement envoyé."""
    from colaig.agents.context_builder import build_agent_context

    ctx = await build_agent_context(
        fake_storage, None, "synthesiser",
        prompt_espace="Cite l'article, toujours.")

    assert "[nom_fichier.ext]" not in ctx.system_prompt, (
        f"les deux grammaires coexistent encore : {ctx.system_prompt!r}")
    assert "Cite l'article" in ctx.system_prompt
