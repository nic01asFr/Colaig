"""
Colaig — ce qu'il faut pour juger un échange ne doit pas mourir avec le pod.

POURQUOI CE JOURNAL EXISTE
----------------------------
La porte 1 demande *« une semaine de dogfooding, relevé des 👍👎 et incidents »*. Ce
protocole suppose qu'un humain réagisse à chaque réponse. Le taux de retour mesuré est
de **17 %** — un geste sur six réponses — et l'utilisateur a dit qu'il ne le ferait pas.

Or les pouces n'étaient qu'un **proxy** pour « la réponse était-elle bonne ». Colaig
produit déjà, à chaque échange et sans que personne n'intervienne : la question, les
sources retenues, la confiance, les citations sans source, le temps de réponse. C'est
plus riche qu'un pouce.

CE QUI MANQUAIT
-----------------
Ces éléments partaient dans le **journal du pod**, en une ligne formatée. Deux défauts :

1. **ils meurent au redéploiement** — seize pods se sont succédé le 30/08 ; une semaine
   d'observation aurait perdu ses données à chaque mise à jour ;
2. **ils ne sont pas relisibles** — une chaîne formatée se relit à coups d'expression
   régulière, qui casse au premier changement de formulation.

C'est la même leçon que le magasin de clés Matrix, et le même correctif : **ce qui doit
survivre à un redémarrage ne vit pas dans le pod.**

LA PROPRIÉTÉ FIGÉE ICI
------------------------
Chaque échange laisse une trace **relisible seule**, sur le stockage de l'espace, à côté
des retours. Un 👍 reste utile — il dit ce qu'un humain a pensé — mais il n'est plus la
seule source d'observation.
"""

from __future__ import annotations

import json

import pytest

from colaig.journal_echanges import consigner_echange, lire_echanges


class _Storage:
    def __init__(self):
        self.fichiers: dict[str, bytes] = {}

    async def upload(self, chemin: str, contenu: bytes) -> None:
        self.fichiers[chemin] = contenu

    async def download(self, chemin: str) -> bytes:
        return self.fichiers[chemin]

    async def mkdir(self, chemin: str) -> None:
        return None

    async def list_files(self, dossier: str):
        class _F:
            def __init__(self, p): self.path, self.name, self.is_directory = p, p.rsplit("/", 1)[-1], False
        return [_F(p) for p in self.fichiers if p.startswith(dossier)]


@pytest.mark.asyncio
async def test_un_echange_se_relit_seul():
    s = _Storage()

    await consigner_echange(
        s, "/espace/", question="quel delai pour le certificat ?",
        reponse="Quinze jours au plus.", sources=["fiche.pdf"],
        confiance=0.72, temps_ms=1661, message_id="$m1")

    ecrit = json.loads(next(iter(s.fichiers.values())))
    assert ecrit["question"] == "quel delai pour le certificat ?"
    assert ecrit["reponse"] == "Quinze jours au plus."
    assert ecrit["sources"] == ["fiche.pdf"]
    assert ecrit["confiance"] == 0.72
    assert ecrit["temps_ms"] == 1661


@pytest.mark.asyncio
async def test_la_trace_vit_a_cote_des_retours():
    """Même espace, même logique : les deux se relisent ensemble."""
    from colaig import paths

    s = _Storage()
    await consigner_echange(s, "/espace/", question="q", reponse="r",
                            sources=[], confiance=0.5, temps_ms=1, message_id="$m")

    chemin = next(iter(s.fichiers))
    assert chemin.startswith(paths.echanges_dir("/espace/"))
    assert chemin.endswith(".json")


@pytest.mark.asyncio
async def test_un_echange_redelivre_ne_compte_qu_une_fois():
    """Le nom du fichier dérive du message : deux écritures ne font qu'une trace."""
    s = _Storage()
    for _ in range(3):
        await consigner_echange(s, "/espace/", question="q", reponse="r",
                                sources=[], confiance=0.5, temps_ms=1, message_id="$m")

    assert len(s.fichiers) == 1


@pytest.mark.asyncio
async def test_la_relecture_rend_les_echanges_du_plus_ancien_au_plus_recent():
    s = _Storage()
    for i in range(3):
        await consigner_echange(s, "/espace/", question=f"q{i}", reponse="r",
                                sources=[], confiance=0.5, temps_ms=1,
                                message_id=f"$m{i}", horodatage=f"{1788000000 + i}")

    echanges = await lire_echanges(s, "/espace/")

    assert [e["question"] for e in echanges] == ["q0", "q1", "q2"]


@pytest.mark.asyncio
async def test_consigner_ne_leve_jamais():
    """LA borne. La réponse est le produit ; sa trace est un confort.

    Un stockage en défaut ne doit pas faire échouer un tour de conversation qui vient
    d'aboutir — c'est la même règle que pour les gestes.
    """
    class _StorageCasse:
        async def mkdir(self, chemin): raise OSError("stockage indisponible")
        async def upload(self, chemin, contenu): raise OSError("stockage indisponible")

    await consigner_echange(_StorageCasse(), "/espace/", question="q", reponse="r",
                            sources=[], confiance=0.5, temps_ms=1, message_id="$m")


@pytest.mark.asyncio
async def test_un_espace_sans_journal_rend_une_liste_vide():
    class _Vide:
        async def list_files(self, dossier): raise OSError("dossier absent")

    assert await lire_echanges(_Vide(), "/espace/") == []


def test_les_deux_sites_d_envoi_consignent():
    """LA borne du branchement : deux chemins mènent à une réponse, pas un.

    `handlers.py` journalise l'échange à deux endroits — le pipeline de production et
    la branche agent. N'en instrumenter qu'un donnerait un relevé qui paraît complet et
    ne l'est pas, ce qui est pire qu'un relevé absent.
    """
    from pathlib import Path

    source = Path("colaig/messaging/handlers.py").read_text(encoding="utf-8")
    assert source.count("consigner_echange") >= 3, (
        "les deux sites d'échange doivent consigner (plus l'import)")


@pytest.mark.asyncio
async def test_deux_echanges_sans_identifiant_ne_s_ecrasent_pas():
    """Un canal qui ne fournit pas d'identifiant de message a droit a son journal.

    Le nom du fichier derivait du SEUL `message_id`. `/ask` — l'endpoint par lequel
    passe toute la mesure — n'en fournit pas : les 135 questions d'une campagne
    ecrivaient donc 135 fois LE MEME fichier, et le journal cense « survivre au
    redeploiement » gardait exactement un echange.

    Releve le 04/09/2026 : 1 fichier pour 135 questions posees.
    """
    s = _Storage()

    for q in ("premiere question", "deuxieme question", "troisieme question"):
        await consigner_echange(s, "/espace/", question=q, reponse="r",
                                sources=[], confiance=0.5, temps_ms=1, message_id="")

    assert len(s.fichiers) == 3
    relus = await lire_echanges(s, "/espace/")
    assert [e["question"] for e in relus] == [
        "premiere question", "deuxieme question", "troisieme question"]


@pytest.mark.asyncio
async def test_un_message_redelivre_ne_compte_qu_une_fois():
    """Le dedoublonnage reste la propriete de ceux qui ont un identifiant.

    Matrix redelivre un evenement apres reconnexion. Deux traces pour un seul echange
    fausseraient toute observation faite sur ce journal.
    """
    s = _Storage()

    for _ in range(3):
        await consigner_echange(s, "/espace/", question="q", reponse="r",
                                sources=[], confiance=0.5, temps_ms=1, message_id="$evt42")

    assert len(s.fichiers) == 1
