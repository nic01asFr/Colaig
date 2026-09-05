"""
Contrat — ce qu'un geste de l'utilisateur déclenche.

STATUT: TESTE
VERSION: 2026-08-28 - v1.0
LOT: L3.3

Critère du lot : « ➕ écrit dans `.colaig/notes.md` ; feedback survit au redémarrage ».

Les quatre gestes
------------------
Colaig pose les quatre sous sa réponse ; l'utilisateur tape sur l'une d'elles.

    👍  la réponse convient          → retour persisté
    👎  la réponse ne convient pas   → retour persisté
    🔄  reformule                    → le tour est rejoué
    ➕  garde ça                     → la réponse est versée dans `.colaig/notes.md`

Pourquoi le retour va dans un FICHIER PAR GESTE
-------------------------------------------------
Un journal unique se lit, se modifie et se réécrit. Deux personnes qui approuvent la
même réponse en même temps produiraient deux lectures du même état et **une seule des
deux écritures survivrait** — le second retour disparaîtrait sans trace ni erreur.

Un fichier par geste n'a pas de lecture préalable, donc pas de course. C'est déjà
l'idiome des conversations et des tâches dans ce dépôt.

Ce que ce lot NE fait pas
--------------------------
La table `message → (question, réponse)` est **en mémoire et bornée**. Après un
redémarrage, ➕ et 🔄 sur une réponse ancienne ne retrouvent rien et le disent. Seul le
RETOUR — ce que le critère exige — est persisté : il ne dépend que de l'identifiant du
message et de l'emoji, tous deux portés par l'événement lui-même.
"""
from __future__ import annotations

import json

import pytest

from colaig.models import Reaction

# Les gestes viennent de leur source unique. Ce fichier en tenait sa propre copie, et
# elle a divergé : il pinnait 🔁 (U+1F501) quand la documentation annonçait 🔄
# (U+1F504). Un test qui redéclare la valeur qu'il vérifie confirme la faute au lieu de
# la trouver. Le codepoint est épinglé une fois, dans `test_capacites.py`.
from colaig.capacites import GARDER, POUCE, POUCE_BAS, REJOUER

ESPACE = "/espace-marches"
SALON = "!salon:tchap.gouv.fr"


def _reaction(emoji: str = POUCE, message_id: str = "$reponse") -> Reaction:
    return Reaction(
        user_id="@alice:tchap.gouv.fr",
        conversation_id=SALON,
        message_id=message_id,
        emoji=emoji,
        reaction_id="$la-reaction-" + emoji,
        horodatage=10**13,
    )


@pytest.fixture
def gestionnaire(fake_storage):
    from colaig.messaging.retours import GestionnaireRetours

    rejoues: list = []

    async def _rejouer(reaction, question):
        rejoues.append((reaction, question))

    g = GestionnaireRetours(fake_storage, rejouer=_rejouer)
    g.retenir("$reponse", conversation_id=SALON, espace=ESPACE,
              question="quel est le seuil de dispense ?",
              reponse="Le seuil est fixé à 40 000 euros HT.")
    g._rejoues = rejoues
    return g


async def _lire(storage, espace):
    from colaig.messaging.retours import lire_retours

    return await lire_retours(storage, espace)


# ── Le retour persisté ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_un_pouce_leve_est_ecrit_dans_l_espace(gestionnaire, fake_storage):
    await gestionnaire.traiter(_reaction(POUCE))

    retours = await _lire(fake_storage, ESPACE)
    assert len(retours) == 1
    assert retours[0]["emoji"] == POUCE
    assert retours[0]["message_id"] == "$reponse"
    assert retours[0]["user_id"] == "@alice:tchap.gouv.fr"


@pytest.mark.asyncio
async def test_le_retour_SURVIT_AU_REDEMARRAGE(gestionnaire, fake_storage):
    """Le critère de fin du lot.

    Le gestionnaire est reconstruit à neuf — sa table en mémoire est vide — et le
    retour se relit quand même : il est dans l'espace, pas dans le processus.
    """
    from colaig.messaging.retours import GestionnaireRetours

    await gestionnaire.traiter(_reaction(POUCE_BAS))

    neuf = GestionnaireRetours(fake_storage)
    assert neuf.retrouver("$reponse") is None, "la table mémoire repart bien vide"

    retours = await _lire(fake_storage, ESPACE)
    assert len(retours) == 1 and retours[0]["emoji"] == POUCE_BAS


@pytest.mark.asyncio
async def test_deux_retours_SIMULTANES_sont_tous_deux_gardes(gestionnaire, fake_storage):
    """Ce qu'un journal unique en lecture-modification-écriture perdrait.

    Deux personnes approuvent la même réponse : les deux gestes comptent.
    """
    import asyncio

    a = _reaction(POUCE)
    b = _reaction(POUCE)
    b.user_id = "@bob:tchap.gouv.fr"
    b.reaction_id = "$autre-reaction"

    await asyncio.gather(gestionnaire.traiter(a), gestionnaire.traiter(b))
    assert len(await _lire(fake_storage, ESPACE)) == 2


@pytest.mark.asyncio
async def test_le_MEME_geste_repete_ne_compte_qu_une_fois(gestionnaire, fake_storage):
    """Matrix peut redélivrer un événement ; le nom du fichier le dédoublonne."""
    await gestionnaire.traiter(_reaction(POUCE))
    await gestionnaire.traiter(_reaction(POUCE))
    assert len(await _lire(fake_storage, ESPACE)) == 1


@pytest.mark.asyncio
async def test_un_retour_porte_la_QUESTION_qui_l_a_provoque(gestionnaire, fake_storage):
    """Un pouce baissé sans la question ne s'analyse pas.

    « 14 % de 👎 » ne dit rien ; « 👎 sur les questions de seuil » se corrige.
    """
    await gestionnaire.traiter(_reaction(POUCE_BAS))
    r = (await _lire(fake_storage, ESPACE))[0]
    assert r["question"] == "quel est le seuil de dispense ?"


@pytest.mark.asyncio
async def test_un_retour_sur_un_message_OUBLIE_est_quand_meme_garde(fake_storage):
    """Un geste sur une réponse ANCIENNE du même salon : la question est sortie de la
    table bornée, mais l'espace du salon, lui, est encore connu.

    Jeter le retour parce qu'on a perdu son contexte reviendrait à ne compter que les
    retours portant sur les cinq cents dernières réponses.
    """
    from colaig.messaging.retours import GestionnaireRetours

    g = GestionnaireRetours(fake_storage)
    g.retenir("$autre", conversation_id=SALON, espace=ESPACE, question="q", reponse="r")

    await g.traiter(_reaction(POUCE, message_id="$oublie"))
    retours = await _lire(fake_storage, ESPACE)
    assert len(retours) == 1
    assert retours[0]["question"] == ""


@pytest.mark.asyncio
async def test_sans_espace_connu_rien_n_est_ecrit(fake_storage):
    """En mode CHATBOT il n'y a pas d'espace : `storage_path` est vide.

    Écrire malgré tout supposerait un dossier par défaut — exactement ce que D42/D43
    refusent.
    """
    from colaig.messaging.retours import GestionnaireRetours

    g = GestionnaireRetours(fake_storage)
    await g.traiter(_reaction(POUCE, message_id="$inconnu"))
    assert await _lire(fake_storage, ESPACE) == []


# ── ➕ : garder la réponse ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_plus_ecrit_la_reponse_dans_notes_md(gestionnaire, fake_storage):
    """Le critère de fin, littéralement."""
    from colaig import paths

    await gestionnaire.traiter(_reaction(GARDER))

    contenu = (await fake_storage.download(paths.notes_file(ESPACE))).decode("utf-8")
    assert "Le seuil est fixé à 40 000 euros HT." in contenu
    assert "quel est le seuil de dispense ?" in contenu


@pytest.mark.asyncio
async def test_plus_AJOUTE_sans_ecraser(gestionnaire, fake_storage):
    """Deux ➕ successifs conservent les deux notes.

    Une note qui efface la précédente est pire qu'une note absente : on croit avoir
    gardé.
    """
    from colaig import paths

    await gestionnaire.traiter(_reaction(GARDER))
    gestionnaire.retenir("$deux", conversation_id=SALON, espace=ESPACE,
                         question="et pour les travaux ?",
                         reponse="Le seuil travaux diffère.")
    r = _reaction(GARDER, message_id="$deux")
    r.reaction_id = "$autre"
    await gestionnaire.traiter(r)

    contenu = (await fake_storage.download(paths.notes_file(ESPACE))).decode("utf-8")
    assert "40 000 euros HT" in contenu
    assert "Le seuil travaux diffère." in contenu


@pytest.mark.asyncio
async def test_plus_sur_un_message_OUBLIE_ne_fabrique_pas_de_note(fake_storage):
    """Rien à garder : mieux vaut ne rien écrire qu'une note vide."""
    from colaig import paths
    from colaig.messaging.retours import GestionnaireRetours

    g = GestionnaireRetours(fake_storage)
    await g.traiter(_reaction(GARDER, message_id="$oublie"))
    assert not await fake_storage.exists(paths.notes_file(ESPACE))


# ── 🔄 : rejouer ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rejouer_relance_le_tour_avec_la_question_d_origine(gestionnaire):
    await gestionnaire.traiter(_reaction(REJOUER))
    assert len(gestionnaire._rejoues) == 1
    assert gestionnaire._rejoues[0][1] == "quel est le seuil de dispense ?"


@pytest.mark.asyncio
async def test_rejouer_n_est_PAS_compte_comme_un_retour(gestionnaire, fake_storage):
    """🔄 n'est ni une approbation ni un rejet : c'est une demande.

    Le verser dans les retours brouillerait la seule mesure de qualité qu'on ait.
    """
    await gestionnaire.traiter(_reaction(REJOUER))
    assert await _lire(fake_storage, ESPACE) == []


@pytest.mark.asyncio
async def test_rejouer_sans_question_connue_ne_relance_rien(fake_storage):
    from colaig.messaging.retours import GestionnaireRetours

    rejoues: list = []

    async def _rejouer(reaction, question):
        rejoues.append(question)

    g = GestionnaireRetours(fake_storage, rejouer=_rejouer)
    await g.traiter(_reaction(REJOUER, message_id="$oublie"))
    assert rejoues == []


# ── Ce qui n'est pas un geste de Colaig ─────────────────────────────────────


@pytest.mark.asyncio
async def test_un_emoji_QUELCONQUE_est_ignore(gestionnaire, fake_storage):
    """Un salon vit sa vie : 🎉 sur une réponse n'est pas une instruction.

    N'agir que sur les quatre gestes proposés, c'est refuser qu'un contenu extérieur
    choisisse ce que Colaig fait — principe 4 du `CLAUDE.md` racine.
    """
    await gestionnaire.traiter(_reaction("\N{PARTY POPPER}"))
    assert await _lire(fake_storage, ESPACE) == []
    assert gestionnaire._rejoues == []


@pytest.mark.asyncio
async def test_une_panne_de_stockage_ne_casse_pas_la_reception(gestionnaire, monkeypatch):
    """Un retour perdu est regrettable ; une boucle de réception morte est une panne."""
    async def _tombe(*a, **k):
        raise OSError("stockage indisponible")

    monkeypatch.setattr(gestionnaire._storage, "upload", _tombe)
    await gestionnaire.traiter(_reaction(POUCE))       # ne lève pas


# ── La table en mémoire ─────────────────────────────────────────────────────


def test_la_table_des_messages_est_bornee(fake_storage):
    """Un bot qui tourne des mois ne garde pas toutes ses réponses en mémoire."""
    from colaig.messaging.retours import _MAX_MESSAGES_RETENUS, GestionnaireRetours

    g = GestionnaireRetours(fake_storage)
    for i in range(_MAX_MESSAGES_RETENUS + 100):
        g.retenir(f"$m{i}", conversation_id=SALON, espace=ESPACE,
                  question=f"q{i}", reponse=f"r{i}")

    assert len(g._messages) <= _MAX_MESSAGES_RETENUS
    assert g.retrouver("$m0") is None, "le plus ancien doit sortir"
    assert g.retrouver(f"$m{_MAX_MESSAGES_RETENUS + 99}") is not None


# ── Le nom du fichier de retour ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_l_identifiant_d_evenement_ne_devient_pas_un_nom_de_fichier(
        gestionnaire, fake_storage):
    """Un `event_id` Matrix peut contenir `:` et `$`, illégaux ou piégeux comme nom.

    Il est donc haché pour nommer le fichier, et conservé **dans** le contenu — sans
    quoi on perdrait la seule clé qui dédoublonne un événement redélivré.
    """
    from colaig import paths

    r = _reaction(POUCE)
    r.reaction_id = "$abc:tchap.gouv.fr"
    await gestionnaire.traiter(r)

    fichiers = [f for f in fake_storage.files
                if f.startswith(paths.feedback_dir(ESPACE))]
    assert len(fichiers) == 1
    assert ":" not in fichiers[0].rsplit("/", 1)[-1]

    contenu = json.loads(await fake_storage.download(fichiers[0]))
    assert contenu["reaction_id"] == "$abc:tchap.gouv.fr"
