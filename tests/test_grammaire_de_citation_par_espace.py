"""
La grammaire de citation se declare par espace, comme le garde-fou lui-meme.

Pourquoi ce reglage ne peut pas etre global
-------------------------------------------
`generator.py` porte un `TODO-HAUTE` depuis le 23/08/2026 : « porter ce reglage dans
`workspace.yaml`, ou il a sa place — une variable d'environnement est globale, or la
decision ne l'est pas ». `verification_citations` porte le meme, pour le format.

Colaig est multi-tenant par construction : un dossier, une instance. Le garde-fou
juge une reponse a l'aune des articles qu'elle cite ; sur un fonds RH ou une FAQ
technique, aucune reponse n'en cite, et il les remplacerait TOUTES par un refus. Le
service serait muet et le journal dirait qu'il protege.

Symetriquement, le format de citation appartient au corpus : `\d+\.\d+` designe un
article dans un CCAG, mais un taux, une version ou une date ailleurs. Mesure du
01/09/2026 : sans le format « clause », le garde-fou remplace par un refus la reponse
juste de mp-013, qui citait « Article 4.1 » du CCAG Travaux.

Ces tests figent que la decision est portee par l'espace, et qu'elle reste inactive
tant qu'aucun espace ne la demande.
"""
from colaig.context.workspace import load_workspace


async def test_le_garde_fou_est_inactif_tant_qu_un_espace_ne_le_demande_pas(mock_storage):
    """Le defaut protege les espaces sans articles, qui sont la majorite."""
    mock_storage.add_file("/ws/.colaig/config.yaml", b"workspace_id: ws\nname: W")
    ws = await load_workspace(mock_storage, "/ws/")
    assert ws.garde_fou_provenance is False
    assert ws.format_citation == []


async def test_un_espace_juridique_declare_sa_grammaire(mock_storage):
    """Un fonds de marches publics melange deux grammaires : le Code et les CCAG."""
    mock_storage.add_file(
        "/ws/.colaig/config.yaml",
        b"workspace_id: ws\nname: W\ngarde_fou_provenance: true\n"
        b"format_citation:\n  - code\n  - clause\n",
    )
    ws = await load_workspace(mock_storage, "/ws/")
    assert ws.garde_fou_provenance is True
    assert ws.format_citation == ["code", "clause"]


async def test_un_format_inconnu_est_ignore_et_ne_casse_rien(mock_storage):
    """`config.yaml` est du contenu externe : il ne doit pas pouvoir casser Colaig.

    Les formats connus sont les cles de `_MOTIFS`. Un nom absent y leverait un
    `KeyError` a chaque generation — une faute de frappe dans un fichier de
    configuration rendrait l'espace muet.
    """
    mock_storage.add_file(
        "/ws/.colaig/config.yaml",
        b"workspace_id: ws\nname: W\ngarde_fou_provenance: true\n"
        b"format_citation:\n  - code\n  - jurisprudence\n",
    )
    ws = await load_workspace(mock_storage, "/ws/")
    assert ws.format_citation == ["code"]
