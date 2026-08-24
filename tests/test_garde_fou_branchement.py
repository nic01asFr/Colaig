"""
Contrat — le garde-fou de provenance est une politique de corpus, pas un réglage global.

STATUT: TESTE
VERSION: 2026-08-23 - v1.0
LOT: L1.6

Le piège, et pourquoi il mérite un test à lui seul
---------------------------------------------------
`garde_fou_reponse` juge une réponse à l'aune des **numéros d'article** qu'elle cite.
Sur un corpus juridique, c'est le bon critère : une affirmation de droit sans référence
n'est pas utilisable, celui qui rédige devra la justifier devant un contrôle.

Mais Colaig est multi-tenant par construction — un dossier, une instance. Un espace de
procédures RH, une FAQ technique, un fonds de notes internes ne contiennent **aucun**
numéro d'article. Actif par défaut, ce garde-fou y remplacerait **toute** réponse par un
refus, au motif qu'elle « ne cite rien ». Le service serait muet, et le journal dirait
qu'il protège.

Ce n'est pas une hypothèse. Branché avec un défaut actif, il a fait échouer
`test_generate_confidence_score`, dont la réponse cite `[guide.txt]` — une source de
fichier, pas un article. Le test avait raison contre le branchement.

Ces tests fixent donc les deux moitiés : **inactif, il ne touche à rien** ; **actif, il
fait bien ce pour quoi il existe**. Un garde-fou dont on n'a vérifié qu'une des deux ne
prouve rien.
"""
from __future__ import annotations

import pytest

from colaig.models import (
    ContextMode,
    DocumentChunk,
    SearchResult,
    WorkspaceConfig,
    WorkspaceContext,
)
from colaig.rag.generator import Generator
from tests.fakes import FakeLLM

PASSAGE = (
    "Titre Ier\n\nArticle L2113-10\n\n"
    "Les marchés sont passés en lots séparés, sauf si leur objet ne permet pas "
    "l'identification de prestations distinctes."
)


def _resultats(texte: str = PASSAGE) -> list[SearchResult]:
    chunk = DocumentChunk(text=texte, source_path="ccp.md", source_name="ccp.md",
                          section="Article L2113-10", position=0, doc_type="md")
    return [SearchResult(chunk=chunk, score=0.8, rank=0)]


def _contexte() -> WorkspaceContext:
    return WorkspaceContext(
        workspace=WorkspaceConfig(workspace_id="ccp", name="Commande publique",
                                 storage_path="/ccp/"),
        mode=ContextMode.ASSISTANT,
        system_prompt="Tu réponds sur le code de la commande publique.",
    )


async def _generer(reponse: str, monkeypatch, actif: bool) -> str:
    monkeypatch.setenv("COLAIG_GARDE_FOU_ENABLED", "1" if actif else "0")
    llm = FakeLLM()
    llm.chat_responses = [reponse]
    sortie = await Generator(llm).generate("une question", _contexte(), _resultats())
    return sortie.text


# ── Inactif : il ne touche à rien ───────────────────────────────────────────


async def test_inactif_une_reponse_sans_article_passe(monkeypatch):
    """Le cas d'un espace non juridique — et la raison du défaut inactif.

    Sur un fonds de procédures RH, aucune réponse ne cite d'article. Si le garde-fou
    s'y appliquait, l'assistant ne répondrait plus jamais rien.
    """
    reponse = "La demande de congé se dépose au moins quinze jours avant. [guide.txt]"
    assert await _generer(reponse, monkeypatch, actif=False) == reponse


async def test_inactif_une_reponse_fautive_passe_aussi(monkeypatch):
    """Inactif veut dire inactif — y compris quand cela ne nous arrange pas.

    Sans cette vérification, un défaut de câblage pourrait laisser le garde-fou agir
    en croyant l'avoir coupé, et l'on attribuerait son effet à autre chose.
    """
    reponse = "Selon L9999-1, le seuil est de mille euros."
    assert await _generer(reponse, monkeypatch, actif=False) == reponse


# ── Actif : il fait ce pour quoi il existe ──────────────────────────────────


async def test_actif_une_reponse_ancree_est_rendue_intacte(monkeypatch):
    reponse = "D'après L2113-10, l'allotissement est le principe."
    assert await _generer(reponse, monkeypatch, actif=True) == reponse


async def test_actif_une_reponse_partiellement_ancree_est_annotee(monkeypatch):
    reponse = "D'après L2113-10 et L1414-3, les lots sont séparés."
    sortie = await _generer(reponse, monkeypatch, actif=True)
    assert reponse in sortie, "la réponse d'origine doit rester lisible en entier"
    assert "L1414-3" in sortie and "non vérifiable" in sortie


async def test_actif_une_reponse_sans_attache_est_remplacee(monkeypatch):
    sortie = await _generer("Le seuil est fixé par L9999-1.", monkeypatch, actif=True)
    assert "ne figure pas dans les documents consultés" in sortie
    assert "L9999-1" not in sortie


async def test_actif_la_confiance_tombe_quand_la_reponse_est_remplacee(monkeypatch):
    """Une réponse remplacée ne doit pas hériter du score de la recherche.

    Les passages étaient peut-être excellents ; c'est la réponse qui ne s'y rattache
    pas. Laisser la confiance à 0,8 sur un refus donnerait un indicateur qui ment.
    """
    monkeypatch.setenv("COLAIG_GARDE_FOU_ENABLED", "1")
    llm = FakeLLM()
    llm.chat_responses = ["Le seuil est fixé par L9999-1."]
    sortie = await Generator(llm).generate("q", _contexte(), _resultats())
    assert sortie.confidence == 0.0


@pytest.mark.parametrize("actif", [True, False])
async def test_sans_resultat_de_recherche_le_garde_fou_s_abstient(actif, monkeypatch):
    """Sans passage, il n'y a rien à quoi comparer.

    Le garde-fou déclarerait « aucune attache » — ce qui serait vrai et inutile : le
    défaut est en amont, dans la recherche, et le dire ici masquerait sa cause.
    """
    monkeypatch.setenv("COLAIG_GARDE_FOU_ENABLED", "1" if actif else "0")
    llm = FakeLLM()
    llm.chat_responses = ["Une réponse sans le moindre article."]
    sortie = await Generator(llm).generate("q", _contexte(), [])
    assert sortie.text == "Une réponse sans le moindre article."
