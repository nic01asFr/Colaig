"""On ne pouvait lire qu'un document par son debut.

CE QUE LA TRACE A MONTRE (05/09/2026, campagne complete sur le service)
------------------------------------------------------------------------
    252  search_documents : ok
    217  list_documents   : ok
    202  fetch_document   : ok

Le modele va bien chercher les documents — la conjecture inverse etait fausse. Mais
`fetch_document` ne rend que le DEBUT du fichier, tronque a 3000 caracteres par
defaut. Sur le corpus mesure : 98 documents sur 108 depassent ce seuil, mediane
9 061 caracteres, maximum 75 356.

Le modele le sait — la reponse porte `truncated` — et il insiste : sur 202 appels,
74 demandent 5 000 caracteres et 13 en demandent 10 000. Cela ne l'avance guere : un
article situe au milieu d'un document de 60 000 caracteres reste hors d'atteinte,
quelle que soit la valeur, puisque c'est toujours la tete qu'on lui rend.

Le cas mp-057 s'explique alors entierement. Le modele repond « le document relatif a
la definition du besoin a ete identifie dans le sommaire », le demande, recoit son
en-tete et ses premiers articles, et conclut que l'information n'y figure pas. Elle y
figure, quelques milliers de caracteres plus loin.

CE QUE CE TEST FIXE
---------------------
Un document se lit AUSSI par sa section. C'est generique — tout document structure en
titres markdown — et cela repond au manque observe : le sommaire et la recherche
donnent des titres d'articles, il faut pouvoir en demander un.
"""

from __future__ import annotations

import json

import pytest

from colaig.agents.tools.storage_tools import (
    FETCH_DOCUMENT_DEFINITION,
    create_fetch_handler,
)
from colaig.models import WorkspaceConfig

DOCUMENT = (
    "# Chapitre Ier : DEFINITION DU BESOIN\n\n"
    "> Position dans le Code de la commande publique\n\n"
    + "Un preambule volontairement long. " * 200
    + "\n\n## Article R2111-1\n\nL'acheteur peut effectuer des consultations prealables.\n"
    "\n## Article R2111-8\n\nLes specifications techniques sont formulees de trois manieres.\n"
    "\n## Article R2111-9\n\nUne specification ne peut mentionner une marque.\n"
)


class _Storage:
    def __init__(self):
        self.lus: list[str] = []

    async def exists(self, chemin):
        return chemin.endswith("096-besoin.md")

    async def download(self, chemin):
        self.lus.append(chemin)
        return DOCUMENT.encode("utf-8")


@pytest.fixture
def handler():
    return create_fetch_handler(_Storage(), WorkspaceConfig(
        workspace_id="mesure", name="Mesure", storage_path="/espace-mesure/"))


@pytest.mark.asyncio
async def test_sans_section_le_comportement_ne_change_pas(handler):
    lu = json.loads(await handler("096-besoin.md"))
    assert lu["content"].startswith("# Chapitre Ier")
    assert lu["truncated"] is True


@pytest.mark.asyncio
async def test_une_section_demandee_est_rendue(handler):
    lu = json.loads(await handler("096-besoin.md", section="Article R2111-8"))
    assert "Les specifications techniques sont formulees" in lu["content"]
    assert lu["content"].lstrip().startswith("## Article R2111-8")


@pytest.mark.asyncio
async def test_la_section_s_arrete_a_la_suivante(handler):
    """Servir la fin du document depuis un titre reviendrait a ne rien cibler."""
    lu = json.loads(await handler("096-besoin.md", section="Article R2111-8"))
    assert "Une specification ne peut mentionner une marque" not in lu["content"]


@pytest.mark.asyncio
async def test_une_section_absente_le_dit_et_ne_ment_pas(handler):
    """Rendre le debut du document a la place ferait croire que la section n'existe pas."""
    lu = json.loads(await handler("096-besoin.md", section="Article R9999-1"))
    assert "error" in lu
    assert "R9999-1" in lu["error"]
    assert "sections" in lu, "dire ce que le document porte evite un second appel a l'aveugle"
    assert "Article R2111-8" in lu["sections"]


@pytest.mark.asyncio
async def test_la_section_se_reconnait_sans_le_mot_article(handler):
    """Le modele ecrit « R2111-8 » aussi souvent que « Article R2111-8 »."""
    lu = json.loads(await handler("096-besoin.md", section="R2111-8"))
    assert "Les specifications techniques sont formulees" in lu["content"]


def test_l_outil_annonce_la_section():
    """Une capacite que la definition ne decrit pas n'est jamais employee."""
    noms = {p.name for p in FETCH_DOCUMENT_DEFINITION.parameters}
    assert "section" in noms
    section = next(p for p in FETCH_DOCUMENT_DEFINITION.parameters if p.name == "section")
    assert not section.required
    assert "titre" in section.description.lower() or "section" in section.description.lower()


# ─────────────────────────────────────────────────────────────────────────────
# LE MODELE LIT LA REPONSE DE L'OUTIL, PAS SA DESCRIPTION.
#
# `section` a ete ajoute, decrit, et transmis au modele — le schema OpenAI le porte.
# Sur la campagne suivante : 166 appels a `fetch_document`, ZERO avec `section`.
#
# Ce que la trace montre en revanche, c'est qu'il REAGIT a `truncated` : sur 202
# appels, 74 demandent 5 000 caracteres et 13 en demandent 10 000, apres avoir vu la
# troncature. Il lit donc le resultat et s'y adapte.
#
# Une capacite s'annonce donc la ou le besoin se manifeste — dans la reponse tronquee
# elle-meme — et non seulement dans une description lue une fois, en tete de contexte,
# parmi vingt autres outils.
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_une_reponse_tronquee_dit_ce_qu_elle_cache(handler):
    lu = json.loads(await handler("096-besoin.md", max_chars=500))

    assert lu["truncated"] is True
    assert "sections" in lu, "les titres disent ce que le document porte plus loin"
    assert "Article R2111-8" in lu["sections"]


@pytest.mark.asyncio
async def test_une_reponse_tronquee_dit_comment_obtenir_la_suite(handler):
    """Augmenter `max_chars` ne sert a rien : c'est toujours la tete qu'on rend."""
    lu = json.loads(await handler("096-besoin.md", max_chars=500))

    assert "section" in lu.get("indication", "").lower()


@pytest.mark.asyncio
async def test_une_reponse_entiere_ne_s_encombre_pas(handler):
    """Rien a signaler quand rien ne manque."""
    lu = json.loads(await handler("096-besoin.md", max_chars=100000))

    assert lu["truncated"] is False
    assert "sections" not in lu
    assert "indication" not in lu
